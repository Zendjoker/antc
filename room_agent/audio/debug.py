"""TEMPORARY audio diagnostics. AUDIO_DEBUG=1 saves what every stage heard under debug/audio/<run>/ :
events.jsonl (one line per decision) and wav files (raw mic, processed audio, the audio each Whisper call received).
Nothing here changes behaviour; remove the `debug.` calls once the corruption is fixed."""
import collections
import itertools
import json
import os
import threading
import time
import wave
from pathlib import Path

import numpy as np

ENABLED = os.getenv("AUDIO_DEBUG") == "1"
SR = 16000
RING_S = float(os.getenv("AUDIO_DEBUG_RING_S", "60"))
DIR = Path(os.getenv("AUDIO_DEBUG_DIR", "debug/audio")) / time.strftime("%Y%m%d-%H%M%S")
ctx = threading.local()  # who is asking Whisper: "utterance" (default), "barge" or "settle"
_ring = collections.deque(maxlen=int(RING_S / 0.08))  # (time, raw, clean, voice, playing)
_by_id = {}  # id(clean block) -> ring entry, so a recorded utterance can find its raw audio
_n = itertools.count(1)
_lock = threading.Lock()


def block(raw, clean, voice, playing):
    if not ENABLED:
        return
    entry = (time.time(), raw, clean, voice, playing)
    with _lock:
        if len(_ring) == _ring.maxlen:
            _by_id.pop(id(_ring[0][2]), None)
        _ring.append(entry)
        _by_id[id(clean)] = entry


def raw_of(frames):
    """Raw mic audio and TTS-playing flags for frames that came out of mic_q (cleaned blocks)."""
    with _lock:
        entries = [_by_id.get(id(f)) for f in frames]
    known = [e for e in entries if e]
    if not known:
        return None, 0.0, 0.0
    return (np.concatenate([e[1] for e in known]), sum(1 for e in known if e[4]) / len(known),
            known[0][0])  # raw audio, share of frames recorded while TTS played, wall time of the first frame


def window(t0, t1):
    with _lock:
        sel = [e for e in _ring if t0 <= e[0] <= t1]
    if not sel:
        return None
    return {"raw": np.concatenate([e[1] for e in sel]), "clean": np.concatenate([e[2] for e in sel]),
            "voice": np.array([e[3] for e in sel]), "playing": np.array([e[4] for e in sel]),
            "t": np.array([e[0] for e in sel])}


def wav(name, pcm, sr=SR):
    """Save int16 (or float in -1..1) audio; returns the file name."""
    if not ENABLED or pcm is None or len(pcm) == 0:
        return ""
    DIR.mkdir(parents=True, exist_ok=True)
    pcm = np.asarray(pcm)
    if pcm.dtype != np.int16:
        pcm = np.clip(pcm * 32768.0 if np.abs(pcm).max() <= 1.5 else pcm, -32768, 32767).astype(np.int16)
    path = DIR / f"{next(_n):03d}_{name}.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(pcm.tobytes())
    return path.name


def event(kind, **fields):
    if not ENABLED:
        return
    DIR.mkdir(parents=True, exist_ok=True)
    line = {"t": round(time.time(), 3), "kind": kind, **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in fields.items()}}
    with _lock, open(DIR / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, default=str) + "\n")
