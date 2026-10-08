"""Speech-to-text: local Whisper or Deepgram, plus the Whisper check that tells your interruptions from echo."""

import logging
import os
import threading
import time
from pathlib import Path

import numpy as np
import requests

from room_agent import config
from room_agent import runtime as rt
from room_agent.audio import debug, speaker_id
from room_agent.audio.sounds import to_wav
from room_agent.audio.speech_check import STOP_WORDS, is_filler, is_own_speech, norm_words
from room_agent.config import DG_KEY, STT_PROVIDER, WHISPER_DEVICE, WHISPER_MODEL

log = logging.getLogger("room-agent")

_whisper = None
_whisper_lock = threading.Lock()  # the main loop and the barge-in check share one model


_load_lock = threading.Lock()  # (startup preloads it in the background; the voice loop may ask before it's done)


def preload():
    """Start loading Whisper now, in the background, so it's ready by the time the wake word model is."""
    threading.Thread(target=load_whisper, name="whisper-preload", daemon=True).start()


def load_whisper():
    global _whisper
    if _whisper is not None:
        return _whisper
    with _load_lock:
        return _load()


def _load():
    global _whisper
    if _whisper is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise SystemExit("STT_PROVIDER=whisper needs: pip install -r requirements/local.txt")
        if WHISPER_DEVICE == "cuda" and os.name == "nt":
            # pip's nvidia-cublas / nvidia-cudnn wheels put their DLLs here; Windows won't find them otherwise
            import site

            for sp in site.getsitepackages():
                for d in (Path(sp) / "nvidia").glob("*/bin"):
                    os.add_dll_directory(str(d))
                    os.environ["PATH"] = str(d) + os.pathsep + os.environ["PATH"]
        log.info("loading Whisper model %s on %s (first run downloads it)...", WHISPER_MODEL, WHISPER_DEVICE)
        compute = "float16" if WHISPER_DEVICE == "cuda" else "int8"
        _whisper = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=compute)
    return _whisper


# A segment Whisper isn't sure about is dropped rather than trusted: this is how silence and noise turn into "Thank you."
MIN_LOGPROB = -1.0  # average token log-probability below this: it was guessing
MAX_NO_SPEECH = 0.6  # Whisper's own "this isn't speech" estimate
MAX_COMPRESSION = 2.4  # text that repeats itself: a decoding loop


def normalized(pcm, target=3000.0, max_gain=30.0):
    """Whisper was trained on healthy levels; your mic delivers speech around RMS 300-400, which costs it accuracy."""
    x = pcm.astype(np.float32)
    if not np.any(x):
        return x
    return np.clip(x * min(target / float(np.sqrt(np.mean(x[x != 0] ** 2))), max_gain), -32768, 32767)


_recent_runs = {}  # (audio fingerprint, prompt) -> (text, confidence): the same audio is never decoded twice


def whisper_text(pcm, prompt=None):
    """`prompt`: what the speech is expected to be (Whisper's initial prompt), e.g. while Jarvis waits for an address."""
    key = (len(pcm), hash(np.asarray(pcm).tobytes()), prompt or "")
    if key in _recent_runs:  # (e.g. the barge-in check already transcribed exactly this snippet)
        text, rt.stt_confidence = _recent_runs[key]
        return text
    with _whisper_lock:
        # beam 5 and a fixed temperature: the default fallback re-decodes doubtful audio with random sampling,
        # which is where completely unrelated phrases came from. No carry-over text between segments either.
        t0, sent = time.time(), normalized(pcm)
        segments, _ = load_whisper().transcribe(
            sent / 32768.0, language="en", beam_size=5, temperature=0.0, condition_on_previous_text=False,
            initial_prompt=prompt or None,
        )
        kept, seen = [], []
        for s in segments:
            drop = s.avg_logprob < MIN_LOGPROB or s.compression_ratio > MAX_COMPRESSION or (
                s.no_speech_prob > MAX_NO_SPEECH and s.avg_logprob < -0.6
            )
            seen.append((s.text.strip(), round(s.avg_logprob, 2), round(s.no_speech_prob, 2), drop))
            if drop:
                log.info("dropped doubtful speech %r (logprob %.2f, no-speech %.2f)", s.text.strip(), s.avg_logprob, s.no_speech_prob)
                continue
            kept.append(s)
        text = " ".join(s.text for s in kept).strip()
        rt.stt_confidence = ({"logprob": min(s.avg_logprob for s in kept), "no_speech": max(s.no_speech_prob for s in kept)}
                             if kept else None)
        rt.stt_raw = " ".join(t for t, *_ in seen).strip()  # (everything Whisper produced, kept or not: diagnostics)
        _recent_runs[key] = (text, rt.stt_confidence)
        while len(_recent_runs) > 8:
            _recent_runs.pop(next(iter(_recent_runs)))
        if debug.ENABLED:
            engine = rt.engine
            debug.event("whisper", why=getattr(debug.ctx, "why", "utterance"), seconds=len(pcm) / 16000,
                        rms=float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))), text=text, segments=seen,
                        took=time.time() - t0, tts_playing=bool(engine and engine.is_playing()),
                        audio=debug.wav("whisper_" + getattr(debug.ctx, "why", "utterance"), sent))
        return text


def verify_barge(pcm, check_speaker=True):
    """Barge-in evidence check. First, is it your enrolled voice? (cheap, and it keeps other voices and the agent's own echo
    away from Whisper.) Then transcribe the snippet: your words interrupt; the agent's own words (echo),
    silence-hallucinations, filler sounds and noise don't."""
    state, score = speaker_id.gate.verdict(pcm) if check_speaker else ("off", None)
    if state == "not you":
        return False, f"not your voice ({score:.2f})"
    if state == "unsure":
        return False, f"noise: not sure it's your voice yet ({score:.2f})"  # (checked again with more audio)
    if state == "you" and score >= config.SPEAKER_TRUST:
        return True, f"your voice ({score:.2f})"  # that sure: no need to wait for Whisper too
    text = whisper_text(pcm)
    words = norm_words(text)
    if is_filler(text):
        return False, f"noise ({text!r})"
    if not [w for w in words if w not in STOP_WORDS]:
        return False, f"noise: nothing to tell it apart yet ({text!r})"  # (checked again with more audio)
    if is_own_speech(words):
        return False, f"likely echo ({text!r})"
    return True, repr(text)


def transcribe(pcm):
    from room_agent.actions.pending import stt_hint

    hint = stt_hint()  # (Jarvis is waiting for an email address: say so to the recognizer)
    if hint:
        log.info("speech recognition hint: expecting an email address")
    if STT_PROVIDER == "whisper":
        text = whisper_text(pcm, prompt=hint)
        if hint:
            log.info("email: RAW TRANSCRIPT %r (heard while an address was expected)", text)
        return text
    r = requests.post(
        "https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true",
        headers={"Authorization": f"Token {DG_KEY}", "Content-Type": "audio/wav"},
        data=to_wav(pcm),
        timeout=20,
    )
    r.raise_for_status()
    best = r.json()["results"]["channels"][0]["alternatives"][0]
    rt.stt_confidence = {"score": best.get("confidence", 0.0)}
    return best["transcript"].strip()
