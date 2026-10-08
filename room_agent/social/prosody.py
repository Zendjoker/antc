"""How something was said: speaking rate, loudness, pauses, pitch movement. Cheap (a few ms of numpy on the utterance
that was already recorded), and only ever SUPPORTING evidence: loud and fast is more energy, quiet and slow is less,
compared with this person's own usual (a running baseline), never with an absolute "angry voice" threshold. Audio alone
never produces frustration, joking or sadness; it can only raise or lower energy and strengthen what the words say.
"""

import numpy as np

from room_agent.social.signals import Evidence

SR = 16000
FRAME = 640                 # 40 ms analysis frames
MIN_BASELINE = 3            # utterances heard before relative features are trusted
_base = {"n": 0, "loud_db": None, "rate": None, "pitch_var": None}


def _ema(old, new, a=0.25):
    return new if old is None else (1 - a) * old + a * new


def _pitch(frame):
    """Rough f0 (Hz) of a voiced 40 ms frame by autocorrelation, or None (unvoiced / unsure)."""
    x = frame - frame.mean()
    if not np.any(x):
        return None
    ac = np.fft.irfft(np.abs(np.fft.rfft(x, 2 * len(x))) ** 2)[: len(x)]
    lo, hi = SR // 400, SR // 70  # 70-400 Hz
    if ac[0] <= 0:
        return None
    k = lo + int(np.argmax(ac[lo:hi]))
    return SR / k if ac[k] / ac[0] > 0.35 else None


def measure(pcm, text):
    """Features of one utterance (int16 16 kHz) -> dict, or None if there's too little speech to say anything."""
    if pcm is None or len(pcm) < SR // 2:
        return None
    x = np.asarray(pcm, dtype=np.float32)
    frames = x[: len(x) // FRAME * FRAME].reshape(-1, FRAME)
    rms = np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-6
    floor = np.percentile(rms, 20)
    voiced = rms > max(floor * 2.5, 120.0)
    if voiced.sum() < 5:
        return None
    speech_s = voiced.sum() * FRAME / SR
    # pauses: runs of >= 300 ms of non-voiced frames between voiced ones
    idx = np.flatnonzero(voiced)
    gaps = np.diff(idx) - 1
    pauses = int(np.sum(gaps * FRAME / SR >= 0.3))
    words = len(str(text or "").split())
    f0 = [p for p in (_pitch(f) for f in frames[voiced][:120]) if p]
    pitch_var = float(np.std(12 * np.log2(np.array(f0) / np.median(f0)))) if len(f0) >= 6 else None
    return {"loud_db": float(20 * np.log10(np.median(rms[voiced]))), "rate": words / max(speech_s, 0.3),
            "pauses": pauses, "speech_s": round(float(speech_s), 2), "pitch_var": pitch_var, "words": words}


def evidence(feat, now):
    """Supporting evidence from `feat` relative to this person's baseline (then the baseline learns from it)."""
    if not feat or feat["words"] < 2:
        return []
    out = []
    if _base["n"] >= MIN_BASELINE:
        loud = feat["loud_db"] - _base["loud_db"]
        rate = feat["rate"] / max(_base["rate"], 0.5)
        if loud > 4 and rate > 1.1:
            out.append(Evidence("energy_up", 0.25, "audio", now, f"louder (+{loud:.0f} dB) and faster (x{rate:.2f}) than usual"))
        elif loud < -4 and rate < 0.9:
            out.append(Evidence("energy_down", 0.25, "audio", now, f"quieter ({loud:.0f} dB) and slower (x{rate:.2f})"))
        elif loud > 6:
            out.append(Evidence("energy_up", 0.15, "audio", now, f"louder than usual (+{loud:.0f} dB)"))
        elif rate < 0.75 and feat["pauses"] >= 2:
            out.append(Evidence("energy_down", 0.15, "audio", now, "slow, with pauses"))
        if feat["pitch_var"] and _base["pitch_var"] and feat["pitch_var"] > 1.5 * _base["pitch_var"]:
            out.append(Evidence("energy_up", 0.1, "audio", now, "more pitch movement than usual"))
    _base["n"] += 1
    _base["loud_db"] = _ema(_base["loud_db"], feat["loud_db"])
    _base["rate"] = _ema(_base["rate"], feat["rate"])
    if feat["pitch_var"]:
        _base["pitch_var"] = _ema(_base["pitch_var"], feat["pitch_var"])
    return out


def reset():
    _base.update(n=0, loud_db=None, rate=None, pitch_var=None)
