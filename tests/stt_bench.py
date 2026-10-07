"""Compare speech-to-text settings on the real recording from tests/stt_capture.py:  python tests/stt_bench.py [quick]"""
import pathlib
import re
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from room_agent import config  # noqa: E402
from room_agent.audio.stt import load_whisper  # noqa: E402,F401  (registers the CUDA DLL paths on Windows)

from faster_whisper import WhisperModel  # noqa: E402

SR, BLOCK = 16000, 1280
data = np.load(pathlib.Path(__file__).with_name("stt_session.npz"))
RAW, CLEAN, VOICE = data["raw"], data["clean"], data["voice"]
STAMPS, MARKS, TEXTS, KINDS = data["stamps"], data["marks"], list(data["texts"]), list(data["kinds"])
SPEECH_RMS = config.SPEECH_RMS

NUMS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                                        "fifteen sixteen seventeen eighteen nineteen twenty".split())}
NUMS.update(thirty="30", forty="40", fifty="50")


def norm(text):
    text = re.sub(r"\.\.\.|[^a-z0-9' ]", " ", text.lower().replace(":", " "))
    words = [NUMS.get(w, w) for w in text.split() if w not in ("uh", "um")]
    return words


def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / max(len(r), 1)


def window(i):
    lo = int(np.searchsorted(STAMPS, MARKS[i][0] - 0.2))
    hi = int(np.searchsorted(STAMPS, MARKS[i][1] + 0.2))
    return lo, hi


def blocks(audio, lo, hi):
    return audio[lo * BLOCK: hi * BLOCK]


def rms(x):
    return float(np.sqrt(np.mean(x.astype(np.float32) ** 2))) + 1e-6


def segment(lo, hi, silence_s, patience=1.0):
    """What record_utterance would cut out of this stretch: (start_block, end_block) pairs."""
    out, start, silent, heard = [], None, 0.0, False
    for b in range(lo, hi):
        speech = VOICE[b] >= config.VAD_THRESHOLD and rms(CLEAN[b * BLOCK:(b + 1) * BLOCK]) >= SPEECH_RMS * 0.5
        if speech:
            if start is None:
                start = b
            heard, silent = True, 0.0
        elif heard:
            silent += BLOCK / SR
            if silent >= silence_s * patience:
                out.append((start, b + 1))
                start, heard, silent = None, False, 0.0
    if heard:
        out.append((start, hi))
    return out


def clip(audio, seg, lead=3):
    """The utterance as recorded: from a few blocks before the first speech (the recorder starts earlier) to its end."""
    return blocks(audio, max(seg[0] - lead, 0), seg[1])


def normalize(x, target=3000.0):
    x = x.astype(np.float32)
    gain = min(target / rms(x[np.abs(x) > 0] if np.any(x) else x), 30.0)
    return np.clip(x * gain, -32768, 32767)


MODELS = {}


def model(name):
    if name not in MODELS:
        t = time.time()
        MODELS[name] = WhisperModel(name, device="cuda", compute_type="float16")
        print(f"   (loaded {name} in {time.time() - t:.0f}s)")
    return MODELS[name]


def transcribe(name, audio, **kw):
    t = time.time()
    segs, _ = model(name).transcribe(audio.astype(np.float32) / 32768.0, language="en", **kw)
    segs = list(segs)
    return " ".join(s.text for s in segs).strip(), time.time() - t, segs


def run(label, name, source, silence_s=0.6, pre=None, **kw):
    """Transcribe every item the way the pipeline would deliver it; returns mean WER and mean latency."""
    wers, times, utt = [], [], 0
    per_item = []
    for i, ref in enumerate(TEXTS):
        lo, hi = window(i)
        parts = []
        for seg in segment(lo, hi, silence_s):
            audio = clip(source, seg)
            if pre:
                audio = pre(audio)
            text, dt, _ = transcribe(name, audio, **kw)
            parts.append(text)
            times.append(dt)
            utt += 1
        hyp = " ".join(parts)
        wers.append(wer(ref, hyp))
        per_item.append(hyp)
    print(f"{label:58} WER {np.mean(wers) * 100:5.1f}%   latency {np.mean(times) * 1000:5.0f} ms   utterances {utt}")
    return per_item, wers


if __name__ == "__main__":
    print(f"recording: {len(RAW) / SR:.0f}s, {len(TEXTS)} items; SPEECH_RMS={SPEECH_RMS} SILENCE_S={config.SILENCE_S}\n")
    print("AUDIO LEVELS (speech blocks only, int16 RMS; 3000 is a healthy level, quiet rooms leave the floor under 50)")
    for src_name, src in (("raw mic", RAW), ("after AEC+noise suppression", CLEAN)):
        speech, floor = [], []
        for b in range(len(VOICE)):
            x = src[b * BLOCK:(b + 1) * BLOCK]
            (speech if VOICE[b] >= 0.5 else floor).append(rms(x))
        print(f"   {src_name:30} speech {np.median(speech):6.0f}   loud {np.percentile(speech, 90):6.0f}   floor {np.median(floor):5.0f}")
    print()
    base = lambda: None  # noqa: E731
    load_whisper()  # (also puts the CUDA DLL folders on the path)
    warm = transcribe("small.en", clip(CLEAN, (0, 10)), beam_size=1)
    print("ENDPOINTING (how many pieces each item is cut into; 1 per sentence is ideal, pauses split it)")
    for s in (0.6, 0.8, 1.0, 1.4):
        counts = [len(segment(*window(i), s)) for i in range(len(TEXTS))]
        print(f"   SILENCE_S={s}: pieces per item {counts}")
    print("\nDECODING / PREPROCESSING (small.en, your SILENCE_S)")
    cur, _ = run("CURRENT: small.en, beam 1, cleaned audio", "small.en", CLEAN, 0.6, beam_size=1)
    run("endpoint 1.0 s", "small.en", CLEAN, 1.0, beam_size=1)
    run("endpoint 1.0 s + raw mic audio (no AEC/NS)", "small.en", RAW, 1.0, beam_size=1)
    run("endpoint 1.0 s + gain-normalised", "small.en", CLEAN, 1.0, pre=normalize, beam_size=1)
    run("endpoint 1.0 s + normalised + beam 5", "small.en", CLEAN, 1.0, pre=normalize, beam_size=5)
    run("endpoint 1.0 s + normalised + beam 5 + vad_filter", "small.en", CLEAN, 1.0, pre=normalize, beam_size=5, vad_filter=True)
    if "quick" not in sys.argv:
        print("\nMODELS (endpoint 1.0 s, normalised, beam 5, no context carried over)")
        for name in ("small.en", "medium.en", "distil-large-v3", "large-v3-turbo"):
            run(f"{name}", name, CLEAN, 1.0, pre=normalize, beam_size=5, condition_on_previous_text=False)

