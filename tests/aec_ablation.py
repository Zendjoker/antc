"""Which part of the WebRTC processing (echo cancellation / noise suppression / high-pass) damages your voice?
python tests/aec_ablation.py
Your recorded raw mic speech (tests/stt_session.npz) is mixed with the agent's own speech leaking into the mic at several
levels, run through the same livekit AudioProcessingModule the app uses with each feature on or off, and transcribed.
Reported: word error rate against what you actually said, and how much louder/quieter your voice came out."""
import pathlib
import sys

import numpy as np
from scipy.signal import resample_poly

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from livekit import rtc  # noqa: E402

from room_agent import config  # noqa: E402
from room_agent.audio.stt import load_whisper, whisper_text  # noqa: E402
from room_agent.audio.tts import load_piper, piper_pcm  # noqa: E402

d = np.load(ROOT / "tests" / "stt_session.npz")
RAW, VOICE, STAMPS, MARKS = d["raw"], d["voice"], d["stamps"], d["marks"]
B = 1280
SAID = {5: "Set a timer for five seconds.", 6: "Switch to a British voice.", 7: "Stay quiet until I call you.",
        11: "Remind me in fifteen minutes to move my car."}
MONOLOGUE = ("Sure, let me tell you about that. Once upon a time there was a small lighthouse on a rocky coast, and every "
             "night the keeper climbed the long spiral stairs to light the great lamp, so that ships far out at sea could "
             "find their way safely home through the storm. The keeper never missed a single night in forty years.")
NUMS = {"five": "5", "fifteen": "15", "ten": "10"}


def words(t):
    import re
    return [NUMS.get(w, w) for w in re.sub(r"[^a-z0-9' ]", " ", t.lower()).split()]


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    dist = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, dist[0] = dist[0], i
        for j, hw in enumerate(h, 1):
            prev, dist[j] = dist[j], min(dist[j] + 1, dist[j - 1] + 1, prev + (rw != hw))
    return dist[len(h)] / max(len(r), 1)


def clip(i):
    lo, hi = int(np.searchsorted(STAMPS, MARKS[i][0] - 0.2)), int(np.searchsorted(STAMPS, MARKS[i][1] + 0.2))
    voiced = [b for b in range(lo, hi) if VOICE[b] >= 0.5]
    a, z = max(voiced[0] - 6, lo), min(voiced[-1] + 8, hi)
    return RAW[a * B: z * B]


def run_apm(mic, ref24, aec, ns, hpf=True):
    apm = rtc.AudioProcessingModule(echo_cancellation=aec, noise_suppression=ns, high_pass_filter=hpf)
    out = np.zeros_like(mic)
    for k in range(len(mic) // 160):
        r = ref24[k * 240:(k + 1) * 240]
        if len(r) == 240:
            apm.process_reverse_stream(rtc.AudioFrame(r.tobytes(), 24000, 1, 240))
        f = rtc.AudioFrame(mic[k * 160:(k + 1) * 160].tobytes(), 16000, 1, 160)
        apm.process_stream(f)
        out[k * 160:(k + 1) * 160] = np.frombuffer(bytes(f.data), dtype=np.int16)
    return out


load_piper()
load_whisper()
whisper_text(np.zeros(16000, dtype=np.int16))
agent24 = np.frombuffer(b"".join(piper_pcm(MONOLOGUE)), dtype=np.int16)
agent16 = resample_poly(agent24.astype(np.float32), 2, 3)
START = int(6.0 * 16000)
CONFIGS = [("AEC+NS (the app)", True, True), ("AEC only", True, False), ("NS only", False, True), ("neither", False, False)]
print(f"{'':26}" + "".join(f"{n:>20}" for n, _, _ in CONFIGS))
for label, gain, playing in (("no agent audio at all", 0.0, False), ("agent playing, no leak (headphones)", 0.0, True),
                             ("agent leaks, gain 0.1", 0.1, True), ("agent leaks, gain 0.25", 0.25, True)):
    totals = {n: [] for n, _, _ in CONFIGS}
    levels = {n: [] for n, _, _ in CONFIGS}
    for i, said in SAID.items():
        user = clip(i)
        n = START + len(user) + 16000
        mic = np.zeros(n, dtype=np.float32)
        mic[START:START + len(user)] += user
        echo = np.zeros(n, dtype=np.float32)
        delayed = np.concatenate([np.zeros(int(0.12 * 16000), dtype=np.float32), agent16])[:n]
        echo[:len(delayed)] = delayed
        mic = np.clip(mic + gain * echo, -32768, 32767).astype(np.int16)
        ref = np.zeros(int(n * 1.5), dtype=np.int16)
        if playing:
            ref[:len(agent24)] = agent24[:len(ref)]
        for name, aec, ns in CONFIGS:
            out = run_apm(mic, ref, aec, ns)
            seg = out[START - 4800: START + len(user) + 4800]
            totals[name].append(wer(said, whisper_text(seg)))
            ref_user = mic[START: START + len(user)].astype(np.float32)
            levels[name].append(20 * np.log10((np.sqrt(np.mean(out[START:START + len(user)].astype(np.float32) ** 2)) + 1e-6)
                                              / (np.sqrt(np.mean(user.astype(np.float32) ** 2)) + 1e-6)))
    print(f"{label:26}" + "".join(f"{np.mean(totals[n]) * 100:12.0f}% WER {np.mean(levels[n]):+4.0f}dB" for n, _, _ in CONFIGS), flush=True)
sys.stdout.flush()
