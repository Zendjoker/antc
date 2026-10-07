"""Barge-in acceptance simulation with the REAL engine in real time: python tests/barge_sim.py [--speaker 0|1] [--quick]
The sound card is replaced by a feeder: the "mic" is your recorded speech (held-out half of tests/stt_session.npz) or another
voice, plus the agent's own playback delayed 400 ms and 4x louder than your voice (your measured numbers); the "speaker" is
drained into the echo canceller's reference like the real output callback. Everything else is the app's code:
WebRTC AEC/NS, Silero VAD, barge-in logic, speaker check, Whisper verification, settle_mic, record_utterance, transcribe.
  a) the agent talks 22 s, you stay silent             -> zero interruptions
  b) you interrupt mid-reply (6 sentences)             -> it stops quickly, your full sentence is captured
  c) other voices (TTS) talk over it (6 voices)        -> no interruption"""
import json
import os
import pathlib
import re
import sys
import tempfile
import threading
import time

ARGS = sys.argv[1:]
SPEAKER = ARGS[ARGS.index("--speaker") + 1] if "--speaker" in ARGS else "1"
QUICK = "--quick" in ARGS
TRACE = "--trace" in ARGS
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ.update(SPEAKER_VERIFY=SPEAKER, VOICEPRINT_FILE=str(tmp / "voiceprint.npz"), PYTHONIOENCODING="utf-8")
if TRACE:
    os.environ.update(AUDIO_DEBUG="1", AUDIO_DEBUG_RING_S="120")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import numpy as np  # noqa: E402
from scipy.signal import resample_poly  # noqa: E402

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.audio import mic as mic_input  # noqa: E402
from room_agent.audio import speaker_id  # noqa: E402
from room_agent.audio.engine import FRAME, AudioEngine  # noqa: E402
from room_agent.audio.mic import record_utterance  # noqa: E402
from room_agent.audio.stt import load_whisper, transcribe, verify_barge, whisper_text  # noqa: E402
from room_agent.audio.tts import load_piper, open_piper, piper_pcm  # noqa: E402
from room_agent.conversation.session import settle_mic  # noqa: E402

B, SR = FRAME, 16000
TICK = B / SR
DELAY_S, ECHO_RATIO, USER_RMS = 0.4, 4.0, 350.0
MONOLOGUE = ("Sure, let me tell you about that. Once upon a time there was a small lighthouse on a rocky coast, and every "
             "night the keeper climbed the long spiral stairs to light the great lamp, so that ships far out at sea could "
             "find their way safely home through the storm. The keeper never missed a single night in forty years, and "
             "when he finally retired, the whole town came to say thank you.")
OTHERS = ["Stop, I have a question about the weather in San Francisco.", "What alarm are you going to set for me tomorrow?"]

d = np.load(ROOT / "tests" / "stt_session.npz")
RAW, CLEAN, VOICE, STAMPS, MARKS = d["raw"], d["clean"], d["voice"], d["stamps"], d["marks"]
items = {}
for i in range(len(MARKS)):
    lo, hi = int(np.searchsorted(STAMPS, MARKS[i][0] - 0.2)), int(np.searchsorted(STAMPS, MARKS[i][1] + 0.2))
    voiced = [b for b in range(lo, hi) if VOICE[b] >= 0.5]
    if len(voiced) >= 8:
        a, z = max(voiced[0] - 6, lo), min(voiced[-1] + 8, hi)
        items[i] = (RAW[a * B: z * B], CLEAN[a * B: z * B], (voiced[0] - a) * TICK)
ids = sorted(items)
enroll_ids, test_ids = ids[0::2], ids[1::2]
quiet = [b for b in range(len(VOICE)) if VOICE[b] < 0.1 and np.abs(RAW[b * B:(b + 1) * B]).max() < 300]
FLOOR = np.concatenate([RAW[b * B:(b + 1) * B] for b in quiet[:400]])


def rms(x):
    x = np.asarray(x, dtype=np.float32)
    return float(np.sqrt(np.mean(x[np.abs(x) > 50] ** 2))) + 1e-6


# ---------- other voices ----------
def synth(voice, text):
    parts = []
    for c in voice.synthesize(text):
        g = np.gcd(SR, c.sample_rate)
        parts.append(resample_poly(c.audio_int16_array.astype(np.float32), SR // g, c.sample_rate // g))
    return np.concatenate(parts)


def other_voices():
    from piper import PiperVoice

    out = {}
    for p in sorted((ROOT / "models" / "tts_impostors").glob("*.onnx")):
        out[p.stem] = synth(PiperVoice.load(str(p)), OTHERS[len(out) % 2])
    import soundfile as sf
    import subprocess

    for voice in ("David", "Zira"):
        wav = tmp / f"{voice}.wav"
        subprocess.run(["powershell", "-NoProfile", "-Command", "Add-Type -AssemblyName System.Speech; $s = New-Object "
                        f"System.Speech.Synthesis.SpeechSynthesizer; $s.SelectVoice('Microsoft {voice} Desktop'); "
                        f"$s.SetOutputToWaveFile('{wav}'); $s.Speak('{OTHERS[0]}'); $s.SetOutputToNull()"], check=True, capture_output=True)
        a, sr = sf.read(wav, dtype="float32")
        g = np.gcd(SR, sr)
        out[voice] = resample_poly(a if a.ndim == 1 else a[:, 0], SR // g, sr // g) * 32767
    return {k: (v * (USER_RMS / rms(v))).astype(np.int16) for k, v in out.items()}


# ---------- engine + feeder ----------
rt.engine = engine = AudioEngine(config.OUT_SR, aec=config.AEC, noise_suppression=config.NOISE_SUPPRESSION, barge_in=config.BARGE_IN,
                                 speech_rms=config.SPEECH_RMS, barge_ms=config.BARGE_MIN_SPEECH_MS, barge_vad=config.BARGE_VAD,
                                 duck_gain=config.BARGE_DUCK_GAIN if config.BARGE_DUCK else 1.0)
engine.full_duplex, engine.capturing, rt.tts_enabled = True, True, True
load_piper()
load_whisper()
engine.barge_verifier = verify_barge
whisper_text(np.zeros(SR, dtype=np.int16))
if SPEAKER == "1":
    ex = speaker_id.make_extractor()
    centroid, sims, n = speaker_id.build_voiceprint(ex, [items[i][1] for i in enroll_ids])
    speaker_id.save_voiceprint(centroid, sum(len(items[i][1]) for i in enroll_ids) / SR)
    print(f"voiceprint from {len(enroll_ids)} of your recorded sentences ({sum(len(items[i][1]) for i in enroll_ids) / SR:.0f} s); "
          f"testing on the other {len(test_ids)}", flush=True)
    speaker_id.gate.active()
    speaker_id.gate.score(np.zeros(SR, dtype=np.int16))
    engine.set_sensitive(True, config.SPEAKER_BARGE_VAD, config.SPEAKER_BARGE_RMS, config.SPEAKER_BARGE_GAP)
threading.Thread(target=engine._process_loop, daemon=True).start()

PLAYBACK_RMS = rms(resample_poly(np.frombuffer(b"".join(piper_pcm(MONOLOGUE)), dtype=np.int16).astype(np.float32), 2, 3))
ECHO_GAIN = ECHO_RATIO * USER_RMS / PLAYBACK_RMS
feed = {"near": None, "pos": 0, "t0": None, "floor": 0, "echo": np.zeros(0, dtype=np.float32), "on": True}


def ticker():
    nxt = time.perf_counter()
    out = np.zeros((config.OUT_SR // 100, 1), dtype=np.int16)
    delay = int(DELAY_S * SR)
    while True:
        played = []
        for _ in range(8):
            engine._on_play(out, len(out), None, None)
            played.append(out[:, 0].copy())
        feed["echo"] = np.concatenate([feed["echo"], resample_poly(np.concatenate(played).astype(np.float32), 2, 3)[:B]])
        echo = np.zeros(B, dtype=np.float32)
        if len(feed["echo"]) >= delay + B:
            echo, feed["echo"] = feed["echo"][:B], feed["echo"][B:]
        near = feed["near"]
        if near is not None and feed["pos"] < len(near):
            if feed["pos"] == 0:
                feed["t0"] = time.time()
            blk = np.pad(near[feed["pos"]: feed["pos"] + B].astype(np.float32), (0, max(0, B - len(near[feed["pos"]: feed["pos"] + B]))))
            feed["pos"] += B
        else:
            s = feed["floor"] % (len(FLOOR) - B)
            blk, feed["floor"] = FLOOR[s: s + B].astype(np.float32), feed["floor"] + B
        engine._raw_q.put(np.clip(blk + ECHO_GAIN * echo, -32768, 32767).astype(np.int16))
        nxt += TICK
        time.sleep(max(0.0, nxt - time.perf_counter()))


threading.Thread(target=ticker, daemon=True).start()
time.sleep(2)

logs = []


class Capture:
    """The app's one-line-per-event logs, so the run shows what the speaker check and barge-in decided."""

    def __init__(self):
        import logging

        self.h = logging.Handler()
        self.h.emit = lambda r: logs.append(r.getMessage())
        logging.getLogger("room-agent").addHandler(self.h)
        logging.getLogger("room-agent").setLevel(logging.INFO)


Capture()


def norm(t):
    nums = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen".split())}
    return [nums.get(w, w) for w in re.sub(r"[^a-z0-9' ]", " ", t.lower()).split() if w not in ("uh", "um")]


def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    dist = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, dist[0] = dist[0], i
        for j, hw in enumerate(h, 1):
            prev, dist[j] = dist[j], min(dist[j] + 1, dist[j - 1] + 1, prev + (rw != hw))
    return dist[len(h)] / max(len(r), 1)


def trial(near, onset_s, hold_s, listen=True):
    """One reply being spoken; `near` is injected 6 s in. -> dict(interrupted, latency, final, logs)."""
    rt.recent_speech.clear()
    rt.recent_speech.append(MONOLOGUE)
    feed.update(near=None, pos=0, t0=None)
    engine.drain_mic()
    engine.interrupted.clear()
    logs.clear()
    started = time.time()
    engine.play(b"".join(piper_pcm(MONOLOGUE)))
    if near is not None:
        def inject():
            time.sleep(6.0)
            feed.update(near=near, pos=0)
        threading.Thread(target=inject, daemon=True).start()
    time.sleep(0.2)
    t_int = None
    while time.time() - started < hold_s and engine.is_playing():
        if engine.interrupted.is_set():
            t_int = time.time()
            break
        time.sleep(0.01)
    latency = None if t_int is None or not feed["t0"] else t_int - (feed["t0"] + onset_s)
    final = None
    if t_int is not None and listen:
        heard, barged = settle_mic(started)
        rt.tts_end = time.time() + engine.echo_tail()
        pcm = record_utterance(engine.mic_q, already_heard=heard, start_timeout=8)
        final = transcribe(pcm) if pcm is not None else None
    engine.flush()
    if TRACE and near is not None and feed["t0"]:
        from room_agent.audio import debug

        w = debug.window(feed["t0"] - 0.3, feed["t0"] + len(near) / SR + 0.3)
        if w is not None:
            rows = []
            for k in range(len(w["voice"])):
                c, m = rms(w["clean"][k * B:(k + 1) * B]), rms(w["raw"][k * B:(k + 1) * B])
                cand = w["voice"][k] >= engine.barge_vad_eff and c >= engine.speech_rms * engine.barge_rms_factor and c / m > 0.15
                rows.append(f"{w['voice'][k]:.2f}/{c:.0f}/{c / m:.2f}{'*' if cand else ' '}")
            print("      frames (voice/cleanedRMS/cleaned:raw, * = candidate):", " ".join(rows), flush=True)
    time.sleep(1.0)
    engine.drain_mic()
    engine.interrupted.clear()
    return {"interrupted": t_int is not None, "latency": latency, "final": final, "logs": [m for m in logs if "barge" in m or "speaker" in m]}


print(f"\n=== speaker verification {'ON' if SPEAKER == '1' else 'OFF'}; echo {DELAY_S * 1000:.0f} ms, {ECHO_RATIO:.0f}x louder than you "
      f"(playback RMS {PLAYBACK_RMS:.0f}, echo gain {ECHO_GAIN:.2f}) ===", flush=True)
print("\na) the agent talks 22 s, you stay silent")
r = trial(None, 0, 22.0)
print(f"   interrupted: {r['interrupted']}   {r['logs'][:4]}")
a_false = int(r["interrupted"])

print("\nb) you interrupt mid-reply")
b_rows = []
for i in test_ids[: 3 if QUICK else 6]:
    raw, clean, onset = items[i]
    truth = whisper_text(clean)
    r = trial(raw, onset, 14.0)
    w = wer(truth, r["final"]) if r["final"] else 1.0
    b_rows.append((r["interrupted"], r["latency"], w))
    print(f"   item {i + 1}: interrupted {r['interrupted']}  stop {('%.2f s after you started' % r['latency']) if r['latency'] is not None else '-'}  "
          f"WER {w * 100:.0f}%\n      said   {truth!r}\n      heard  {r['final']!r}\n      {r['logs'][-3:]}", flush=True)

print("\nc) other voices talk over the agent")
others = other_voices()
c_rows = []
for name, clip in list(others.items())[: 3 if QUICK else 8]:
    r = trial(clip, 0.3, 12.0, listen=False)
    c_rows.append(r["interrupted"])
    print(f"   {name:24} interrupted: {r['interrupted']}   {r['logs'][-2:]}", flush=True)

lat = [x[1] for x in b_rows if x[1] is not None]
print("\nSUMMARY  speaker verification", "ON" if SPEAKER == "1" else "OFF")
print(f"  a) silent for 22 s:            {a_false} false interruptions")
print(f"  b) your interruptions:         {sum(x[0] for x in b_rows)}/{len(b_rows)} stopped it; median stop time "
      f"{(np.median(lat) if lat else float('nan')):.2f} s after you started; full sentence WER {np.mean([x[2] for x in b_rows]) * 100:.0f}%")
print(f"  c) other voices:               {sum(c_rows)}/{len(c_rows)} false interruptions")
sys.stdout.flush()
os._exit(0)
