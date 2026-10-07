"""Emulated barge-in test, stage by stage, with your real recorded voice (tests/stt_session.npz, raw mic blocks).
python tests/barge_emulated.py [echo_gain ...]     (default: 0 0.1 0.25; 0 = headphones, >0 = the agent leaks into the mic)
The real AudioEngine runs (WebRTC AEC/NS, Silero VAD, barge-in logic, Whisper verification, mic_q, settle_mic,
record_utterance, transcribe) in real time; only the sound card is replaced: the "mic" is your recorded speech plus the
agent's own audio delayed and attenuated, and the "speaker" is drained into the echo reference like the real callback.
Each trial prints the transcript at every stage so the stage that damages the speech is visible."""
import os
import pathlib
import sys
import threading
import time

os.environ["AUDIO_DEBUG"] = "1"
os.environ["AUDIO_DEBUG_RING_S"] = "900"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import numpy as np  # noqa: E402
from scipy.signal import resample_poly  # noqa: E402

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.audio import debug  # noqa: E402
from room_agent.audio import mic as mic_input  # noqa: E402
from room_agent.audio.engine import FRAME, AudioEngine  # noqa: E402
from room_agent.audio.mic import record_utterance  # noqa: E402
from room_agent.audio.speech_check import classify_audio  # noqa: E402
from room_agent.audio.stt import load_whisper, transcribe, verify_barge, whisper_text  # noqa: E402
from room_agent.audio.tts import load_piper, piper_pcm  # noqa: E402
from room_agent.conversation.session import settle_mic  # noqa: E402

TICK = FRAME / 16000
MONOLOGUE = ("Sure, let me tell you about that. Once upon a time there was a small lighthouse on a rocky coast, and every "
             "night the keeper climbed the long spiral stairs to light the great lamp, so that ships far out at sea could "
             "find their way safely home through the storm. The keeper never missed a single night in forty years.")
GAINS = [float(a) for a in sys.argv[1:]] or [0.0, 0.1, 0.25]
ITEMS = [0, 1, 2, 5, 6, 7, 11]  # tests/stt_session.npz items whose sentence was read as prompted

d = np.load(ROOT / "tests" / "stt_session.npz")
RAW, CLEAN, VOICE, STAMPS, MARKS, TEXTS = d["raw"], d["clean"], d["voice"], d["stamps"], d["marks"], d["texts"]
B = FRAME


def user_clip(i):
    """Raw mic audio of item i from a little before its speech to a little after, plus the quiet room floor."""
    lo, hi = int(np.searchsorted(STAMPS, MARKS[i][0] - 0.2)), int(np.searchsorted(STAMPS, MARKS[i][1] + 0.2))
    voiced = [b for b in range(lo, hi) if VOICE[b] >= 0.5]
    a, z = max(voiced[0] - 6, lo), min(voiced[-1] + 8, hi)
    return RAW[a * B: z * B], voiced[0] - a  # the clip, and how many blocks of lead-in it has


def room_floor():
    quiet = [b for b in range(len(VOICE)) if VOICE[b] < 0.1 and np.abs(RAW[b * B:(b + 1) * B]).max() < 300]
    return np.concatenate([RAW[b * B:(b + 1) * B] for b in quiet[:400]])


FLOOR = room_floor()
setup = AudioEngine(config.OUT_SR, aec=config.AEC, noise_suppression=config.NOISE_SUPPRESSION, barge_in=config.BARGE_IN,
                    speech_rms=config.SPEECH_RMS, barge_ms=config.BARGE_MIN_SPEECH_MS, barge_vad=config.BARGE_VAD)
rt.engine = engine = setup
engine.full_duplex, engine.capturing, rt.tts_enabled = True, True, True
load_piper()
load_whisper()
engine.barge_verifier = verify_barge
whisper_text(np.zeros(16000, dtype=np.int16))
threading.Thread(target=engine._process_loop, daemon=True).start()

feed = {"user": None, "pos": 0, "t_user": None, "gain": 0.0, "floor_pos": 0, "echo": np.zeros(0, dtype=np.float32)}
DELAY = int(0.12 * 16000)


def ticker():
    """The sound card: every 80 ms one mic block in, 80 ms of playback out (draining the agent's buffer)."""
    nxt = time.perf_counter()
    out = np.zeros((config.OUT_SR // 100, 1), dtype=np.int16)
    while True:
        played = []
        for _ in range(8):
            engine._on_play(out, len(out), None, None)
            played.append(out[:, 0].copy())
        spk = resample_poly(np.concatenate(played).astype(np.float32), 2, 3)[:B]
        feed["echo"] = np.concatenate([feed["echo"], spk])
        echo = feed["echo"][:B] if len(feed["echo"]) >= DELAY + B else np.zeros(B, dtype=np.float32)
        if len(feed["echo"]) >= DELAY + B:
            feed["echo"] = feed["echo"][B:]
        if feed["user"] is not None and feed["pos"] < len(feed["user"]):
            if feed["pos"] == 0:
                feed["t_user"] = time.time()
            blk = feed["user"][feed["pos"]: feed["pos"] + B].astype(np.float32)
            blk = np.pad(blk, (0, B - len(blk)))
            feed["pos"] += B
        else:
            start = feed["floor_pos"] % (len(FLOOR) - B)
            blk, feed["floor_pos"] = FLOOR[start: start + B].astype(np.float32), feed["floor_pos"] + B
        mic = np.clip(blk + feed["gain"] * echo, -32768, 32767).astype(np.int16)
        engine._raw_q.put(mic)
        nxt += TICK
        time.sleep(max(0.0, nxt - time.perf_counter()))


threading.Thread(target=ticker, daemon=True).start()
time.sleep(2)


def play_user(clip, offset_s):
    time.sleep(offset_s)
    feed["user"], feed["pos"] = clip, 0


def stage_texts(i, clip, lead_blocks, t_user, t_end):
    """Whisper on the same speech at each stage: raw user-only, raw as the mic got it, after AEC/NS."""
    out = {"truth (raw, no echo)": whisper_text(clip)}
    w = debug.window(t_user - 0.5, t_end + 0.3)
    if w is not None:
        out["raw mic (user+echo)"] = whisper_text(w["raw"])
        out["after AEC/NS"] = whisper_text(w["clean"])
    return out


def trial(i, gain, interrupt=True):
    rt.recent_speech.clear()
    rt.recent_speech.append(MONOLOGUE)
    clip, lead = user_clip(i)
    feed.update(user=None, pos=0, gain=gain, t_user=None)
    engine.drain_mic()
    engine.interrupted.clear()
    n_events = len(open(debug.DIR / "events.jsonl").read().splitlines()) if (debug.DIR / "events.jsonl").exists() else 0
    started = time.time()
    engine.play(b"".join(piper_pcm(MONOLOGUE)))
    threading.Thread(target=play_user, args=(clip, 6.0), daemon=True).start()
    time.sleep(0.2)
    while engine.is_playing() and not engine.interrupted.is_set():
        time.sleep(0.02)
    interrupted = engine.interrupted.is_set()
    heard, barged = settle_mic(started)
    rt.tts_end = time.time() + engine.echo_tail()
    pcm = record_utterance(engine.mic_q, already_heard=heard, start_timeout=8)
    final = transcribe(pcm) if pcm is not None else None
    t_end = time.time()
    label = classify_audio(final, mic_input.last_speech_start)[0] if final else "none"
    events = [__import__("json").loads(line) for line in open(debug.DIR / "events.jsonl").read().splitlines()[n_events:]]
    verifies = [(round(e["seconds"], 2), e["detail"]) for e in events if e["kind"] == "barge_verify"]
    utt = next((e for e in events if e["kind"] == "utterance"), None)
    t_user = feed["t_user"] or started + 6.0
    return {"i": i, "sentence": str(TEXTS[i]), "gain": gain, "interrupted": interrupted, "heard": heard, "verifies": verifies,
            "final": final, "label": label, "utt": utt, "stages": stage_texts(i, clip, lead, t_user, t_end),
            "seconds_clip": len(clip) / 16000, "t_user": t_user, "events": events}


def first_frame_time(utt):
    return None


results = []
for gain in GAINS:
    print(f"\n=== echo gain {gain} ({'headphones: no leak' if gain == 0 else 'agent leaks into the mic'}) ===", flush=True)
    for i in ITEMS:
        r = trial(i, gain)
        results.append(r)
        u = r["utt"] or {}
        print(f"\nitem {i + 1}: said {r['sentence']!r}")
        for k, v in r["stages"].items():
            print(f"   {k:22} {v!r}")
        print(f"   barge verify (partial)   {r['verifies']}")
        print(f"   FINAL to conversation    {r['final']!r} [{r['label']}]  interrupted={r['interrupted']} heard={r['heard']}"
              f" frames={u.get('frames')} first_speech_frame={u.get('first_speech_frame')} tts_share={u.get('tts_share')}", flush=True)
        time.sleep(1.0)

print("\nsaved to", debug.DIR)
sys.stdout.flush()
os._exit(0)
