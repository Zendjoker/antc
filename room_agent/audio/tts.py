"""Text-to-speech: local Piper and ElevenLabs, plus the on-disk cache of short stock clips."""

import logging
import re
import threading
from pathlib import Path

import numpy as np
import requests

from room_agent.audio import voices
from room_agent.config import EL_KEY, EL_MODEL, OUT_SR, VOICES_DIR

log = logging.getLogger("room-agent")

el = requests.Session()  # reuses the TLS connection, saves ~100-200ms per sentence
_piper = None
_piper_lock = threading.RLock()
_clips = {}
_clip_lock = threading.Lock()


def elevenlabs_refused(e):
    r = getattr(e, "response", None)
    return r is not None and r.status_code in (401, 402, 403, 429)


# ---------- Piper (free, local) ----------
def open_piper(name):
    """Load a Piper voice by name, downloading it first if needed (~60-120 MB). Both files are downloaded to
    temporary names and only then put in place, so a failed download never leaves a broken voice behind."""
    try:
        from piper import PiperVoice
    except ImportError:
        raise SystemExit("TTS_PROVIDER=piper needs: pip install -r requirements/local.txt")
    path = VOICES_DIR / f"{name}.onnx"
    meta = Path(str(path) + ".json")
    if not (path.exists() and meta.exists()):
        lang, speaker, quality = name.split("-")
        base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang.split('_')[0]}/{lang}/{speaker}/{quality}/{name}"
        path.parent.mkdir(exist_ok=True)
        log.info("downloading Piper voice %s...", name)
        parts = []
        for target, ext in ((path, ".onnx"), (meta, ".onnx.json")):
            r = requests.get(base + ext, timeout=120)
            r.raise_for_status()
            part = Path(str(target) + ".part")
            part.write_bytes(r.content)
            parts.append((part, target))
        for part, target in parts:
            part.replace(target)
    return PiperVoice.load(str(path))


def load_piper():
    global _piper
    with _piper_lock:
        if _piper is None:
            _piper = open_piper(voices.current.piper)
        return _piper


def install_piper_voice(voice_id, voice):
    """Switch to an already loaded Piper voice."""
    global _piper
    with _piper_lock:
        voices.current.piper, _piper = voice_id, voice


def piper_pcm(text):
    """Synthesize locally and resample to the output stream rate."""
    from scipy.signal import resample_poly

    from room_agent.social.delivery import piper_config

    cfg = piper_config(getattr(text, "delivery", None))  # (this reply's pace and energy, kept subtle)
    if cfg:
        from piper import SynthesisConfig

        cfg = SynthesisConfig(**cfg)
    for chunk in load_piper().synthesize(str(text), syn_config=cfg):
        audio = chunk.audio_int16_array.astype(np.float32)
        if chunk.sample_rate != OUT_SR:
            g = np.gcd(OUT_SR, chunk.sample_rate)
            audio = resample_poly(audio, OUT_SR // g, chunk.sample_rate // g)
        yield np.clip(audio, -32768, 32767).astype(np.int16).tobytes()


# ---------- whole clips ----------
def synth(text):
    """Whole clip as PCM bytes at OUT_SR (used for cached filler sounds)."""
    if voices.provider() == "piper":
        return b"".join(piper_pcm(text))
    r = el.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voices.current.eleven}?output_format=pcm_{OUT_SR}",
        headers={"xi-api-key": EL_KEY},
        json={"text": text, "model_id": EL_MODEL},
        timeout=30,
    )
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        if not elevenlabs_refused(e):
            raise
        voices.fall_back_to_piper("out of credits" if "quota" in r.text else f"status {r.status_code}")
        return b"".join(piper_pcm(text))
    return r.content


def trim_silence(pcm, threshold=300):
    """Cut leading/trailing quiet so a filler is a quick "mm", not "mm" plus half a second of nothing."""
    a = np.frombuffer(pcm, dtype=np.int16)
    loud = np.flatnonzero(np.abs(a) > threshold)
    if not len(loud):
        return pcm
    pad = int(OUT_SR * 0.05)
    return a[max(0, loud[0] - pad) : loud[-1] + pad].tobytes()


def clip(text):
    """Cached filler clip, generated once per voice and stored on disk."""
    with _clip_lock:
        return _clip_locked(text)


def _clip_locked(text):
    if text not in _clips:
        provider = voices.provider()
        voice = voices.current.piper if provider == "piper" else f"{voices.current.eleven}_{EL_MODEL}"
        slug = re.sub(r"\W", "", text).lower()
        path = VOICES_DIR / "clips" / f"{provider}_{voice}_{OUT_SR}_{slug}.pcm"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(trim_silence(synth(text)))
        _clips[text] = path.read_bytes()
        if provider != voices.provider():  # fell back while recording it: re-record in the fallback voice
            del _clips[text]
            return _clip_locked(text)
    return _clips[text]


def clear_clips():
    """Stock phrases get re-recorded in the new voice."""
    with _clip_lock:
        _clips.clear()


def record_in_background(texts):
    """Record these stock phrases (cached on disk) without blocking; a failed one is skipped."""

    def work():
        for text in texts:
            try:
                clip(text)
            except BaseException as e:
                log.debug("clip %r: %s", text, e)

    threading.Thread(target=work, daemon=True).start()
