"""Building and loading raw audio: tones, silence, sound files, WAV encoding."""

import io
import wave

import numpy as np

from room_agent.config import HERE, OUT_SR, SR


def tone(freq=880, dur=0.12):
    t = np.linspace(0, dur, int(OUT_SR * dur), False)
    return (6000 * np.sin(2 * np.pi * freq * t)).astype(np.int16).tobytes()


def silence(dur):
    return np.zeros(int(OUT_SR * dur), dtype=np.int16).tobytes()


def load_sound(path, peak=0.5, gain=None):
    """Any audio file (mp3/wav/flac/ogg) as mono PCM at OUT_SR. Normalized to `peak`, or with `gain`
    just scaled from its original volume."""
    import soundfile as sf
    from scipy.signal import resample_poly

    data, sr = sf.read(str(HERE / path), dtype="float32", always_2d=True)
    audio = data.mean(axis=1)
    if sr != OUT_SR:
        g = np.gcd(sr, OUT_SR)
        audio = resample_poly(audio, OUT_SR // g, sr // g)
    audio *= gain if gain is not None else peak / max(np.abs(audio).max(), 1e-6)
    return (audio * 32767).astype(np.int16).tobytes()


def to_wav(pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
