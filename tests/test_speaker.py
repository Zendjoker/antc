"""Speaker verification: python tests/test_speaker.py  (offline; uses your recorded speech in tests/stt_session.npz)"""
import os
import pathlib
import sys
import tempfile

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ.update(VOICEPRINT_FILE=str(tmp / "voiceprint.npz"), SPEAKER_VERIFY="1", MEMORY_DB=str(tmp / "m.db"),
                  SETTINGS_FILE=str(tmp / "s.json"), REMINDERS_FILE=str(tmp / "r.json"), PYTHONIOENCODING="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import numpy as np  # noqa: E402

from room_agent import config  # noqa: E402
from room_agent.audio import speaker_id, stt  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


if not (ROOT / "tests" / "stt_session.npz").exists():  # (a personal voice recording, never in the repo)
    print("SKIPPED: no recorded speech in tests/stt_session.npz (record it with tests/stt_capture.py)")
    raise SystemExit(0)
d = np.load(ROOT / "tests" / "stt_session.npz")
CLEAN, VOICE, STAMPS, MARKS = d["clean"], d["voice"], d["stamps"], d["marks"]
B = 1280
clips = {}
for i in range(len(MARKS)):
    lo, hi = int(np.searchsorted(STAMPS, MARKS[i][0] - 0.2)), int(np.searchsorted(STAMPS, MARKS[i][1] + 0.2))
    voiced = [b for b in range(lo, hi) if VOICE[b] >= 0.5]
    if len(voiced) >= 8:
        clips[i] = CLEAN[max(voiced[0] - 2, lo) * B: min(voiced[-1] + 4, hi) * B]
ids = sorted(clips)
enroll, test = ids[0::2], ids[1::2]

print("1) no voiceprint: the gate is off and nothing changes")
gate = speaker_id.SpeakerGate()
check("inactive", not gate.active(), gate.error)
check("verdict is off", gate.verdict(clips[ids[0]]) == ("off", None))
state = stt.verify_barge.__defaults__
calls = []
orig = stt.whisper_text
stt.whisper_text = lambda pcm: (calls.append(1), "wait stop")[1]
stt.speaker_id.gate = gate
check("verify_barge falls back to the Whisper check", stt.verify_barge(clips[ids[0]])[0] is True and calls)

print("2) enroll from half of your speech, test on the other half")
ex = speaker_id.make_extractor()
centroid, sims, n = speaker_id.build_voiceprint(ex, [clips[i] for i in enroll])
speaker_id.save_voiceprint(centroid, sum(len(clips[i]) for i in enroll) / 16000)
check("voiceprint file written", config.VOICEPRINT_FILE.exists())
gate = speaker_id.SpeakerGate()
stt.speaker_id.gate = gate
check("active after enrolling", gate.active(), gate.error)
scores = [gate.score(clips[i]) for i in test]
check("your held-out sentences score as you", min(scores) >= config.SPEAKER_ACCEPT,
      f"scores {[round(s, 2) for s in scores]} (accept >= {config.SPEAKER_ACCEPT})")
check("verdicts say you", all(gate.verdict(clips[i])[0] == "you" for i in test))

print("3) other voices")
from room_agent.audio.tts import open_piper  # noqa: E402
from scipy.signal import resample_poly  # noqa: E402

voice = open_piper("en_US-ryan-high")
audio = np.concatenate([resample_poly(c.audio_int16_array.astype(np.float32), 16000 // np.gcd(16000, c.sample_rate),
                                      c.sample_rate // np.gcd(16000, c.sample_rate))
                        for c in voice.synthesize("Stop, I have a question about the weather in San Francisco today.")]).astype(np.int16)
ryan = gate.verdict(audio)
check("the agent's own voice is not you", ryan[0] == "not you", str(ryan))
calls.clear()
check("not-you short-circuits before Whisper", stt.verify_barge(audio)[0] is False and not calls)
rng = np.random.default_rng(0)
noise = (rng.normal(0, 400, 16000)).astype(np.int16)
check("noise is not you", gate.verdict(noise)[0] in ("not you", "unsure"), str(gate.verdict(noise)))

print("4) short snippets are judged by the stricter first threshold, then given a second look")
short = clips[test[0]][: int(0.5 * 16000)]
v = gate.verdict(short)
check("a 0.5 s snippet is you, unsure or not you (never crashes)", v[0] in ("you", "unsure", "not you"), str(v))
unsure = [clips[i][: int(0.5 * 16000)] for i in ids if gate.verdict(clips[i][: int(0.5 * 16000)])[0] == "unsure"]
if unsure:
    r = stt.verify_barge(unsure[0])
    check("an unsure snippet is retried with more audio (detail starts with 'noise')", r[0] is False and r[1].startswith("noise"), str(r))
else:
    print("  [skip] no 0.5 s snippet of yours fell in the unsure band")

print("5) failures never break interrupting")
gate = speaker_id.SpeakerGate()
orig_make = speaker_id.make_extractor
speaker_id.make_extractor = lambda: (_ for _ in ()).throw(RuntimeError("model missing"))
check("model fails to load -> off", not gate.active() and "model missing" in gate.error, gate.error)
speaker_id.make_extractor = orig_make
bad = tmp / "other.npz"
np.savez(bad, centroid=centroid, model="some_other_model.onnx", seconds=30.0)
config.VOICEPRINT_FILE = bad
gate = speaker_id.SpeakerGate()
check("voiceprint from another model -> off, asks to enroll again", not gate.active() and "enroll again" in gate.error, gate.error)
config.VOICEPRINT_FILE = pathlib.Path(os.environ["VOICEPRINT_FILE"])
config.SPEAKER_VERIFY = False
gate = speaker_id.SpeakerGate()
check("SPEAKER_VERIFY=0 -> off", not gate.active())

stt.whisper_text = orig
print("FAILED:" if failures else "ALL PASSED", failures or "")
sys.exit(1 if failures else 0)
