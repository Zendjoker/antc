"""Speech validation before STT, and "let me finish": python tests/test_listening.py  (offline, no model calls)"""
import os
import pathlib
import sys
import tempfile
import threading

import numpy as np

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ.update(REMINDERS_FILE=str(tmp / "rem.json"), MEMORY_DB=str(tmp / "mem.db"), SETTINGS_FILE=str(tmp / "set.json"),
                  PYTHONIOENCODING="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.audio import mic  # noqa: E402
from room_agent.conversation import session  # noqa: E402
from room_agent.text import is_let_me_finish  # noqa: E402

failures = []
B = 1280
STEP = B / 16000
rng = np.random.default_rng(1)


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


def frame(rms, voice):
    return (rng.normal(0, rms, B).astype(np.int16), voice)


def speech(n, rms=900, voice=0.9):
    return [frame(rms, voice) for _ in range(n)]


def quiet(seconds):
    return [frame(15, 0.02) for _ in range(round(seconds / STEP))]


class Feed:
    def __init__(self, frames):
        self.frames = list(frames)

    def get(self):
        if not self.frames:
            raise EOFError
        return self.frames.pop(0)


def record(frames, explaining=False, **kw):
    rt.explaining = explaining
    try:
        return mic.record_utterance(Feed(frames), **kw)
    except EOFError:
        return "ran out"
    finally:
        rt.explaining = False


def seconds(pcm):
    return len(pcm) / 16000


print("1) nothing said: no recording may reach Whisper")
check("silence", record(quiet(8), start_timeout=5) is None)
check("one click (1 voiced frame)", record(quiet(1) + [frame(700, 0.9)] + quiet(8), start_timeout=5) is None)
check("echo flicker (2 weak voiced frames)", record(quiet(1) + [frame(400, 0.55), frame(350, 0.5)] + quiet(8), start_timeout=5) is None)
check("a click, then real speech right after", seconds(record(quiet(1) + [frame(700, 0.9)] + quiet(1) + speech(10) + quiet(2),
                                                              start_timeout=8)) > 1.0)
print("2) real speech, including quiet and short, is still recorded")
check("normal sentence", isinstance(record(quiet(1) + speech(25) + quiet(2), start_timeout=5), np.ndarray))
check("quiet speech (rms 260)", isinstance(record(quiet(1) + speech(12, rms=260, voice=0.8) + quiet(2), start_timeout=5), np.ndarray))
check("short word (5 voiced frames)", isinstance(record(quiet(1) + speech(5, rms=500, voice=0.8) + quiet(2), start_timeout=5), np.ndarray))
check("verified interruption (already heard)", isinstance(record(speech(2) + quiet(2), already_heard=True), np.ndarray))

print("3) a 1-2 second thinking pause")
sentence = speech(12) + quiet(1.5) + speech(12) + quiet(2)
normal = record(sentence, start_timeout=5)
check("normally the turn ends at the pause", seconds(normal) < 2.5, f"({seconds(normal):.1f}s recorded)")
held = record(sentence + quiet(2), explaining=True, start_timeout=5)
check("while explaining it keeps both parts", seconds(held) > 2.5, f"({seconds(held):.1f}s recorded)")

print("4) 'let me finish' phrases")
for t in ["Let me finish my sentence.", "Let me finish.", "hold on, let me talk", "Okay, let me think.", "I'm not done.",
          "Let me explain", "Please don't interrupt me.", "Give me a second to think."]:
    check(f"hold: {t!r}", is_let_me_finish(t))
for t in ["What's the weather?", "Let me know when the timer is done.", "Set a timer for ten minutes.",
          "Let me finish my sentence and then set a timer for ten minutes and also tell me what the weather will be tomorrow.",
          "I'm not sure what time it is."]:
    check(f"not a hold: {t!r}", not is_let_me_finish(t))


class Delay:
    delay = 0.4


class Engine:
    interrupted = threading.Event()
    capturing = True
    user_voice_at = 0.0
    delay = Delay()

    def drain_mic(self):
        pass


def run(script):
    """converse() with scripted utterances; returns (model calls, spoken stock phrases, record start_timeouts)."""
    calls, phrases, waits, left = [], [], [], list(script)
    rt.engine, rt.explaining, rt.patience = Engine(), False, 1.0

    def fake_record(mic_q, start_timeout=0, already_heard=False, **kw):
        waits.append(start_timeout)
        return np.array([left.pop(0)]) if left else None

    session.record_utterance = fake_record
    session.transcribe = lambda pcm: str(texts[int(pcm[0])])
    session.take_turn = lambda h, msg, raw=None, final=False: calls.append((raw, final))
    session.speak_phrase = lambda kind, history=None: (phrases.append(kind), (False, False))[1]
    session.settle_mic = lambda started=None: (False, False)
    session.filler_if_slow = session.stop_thinking = lambda *a, **k: None
    texts[:] = list(script)
    left[:] = list(range(len(script)))
    session.converse(None, [])
    return calls, [p for p in phrases if p not in ("sleep", "checkin")], waits


texts = []
print("5) 'Let me finish my sentence.'")
calls, phrases, waits = run(["Let me finish my sentence."])
check("no model call", not calls, str(calls))
check("no spoken reply", not phrases, str(phrases))
check("waits patiently for the explanation", len(waits) > 1 and waits[1] == config.EXPLAIN_WAIT_S, str(waits[:2]))

print("6) the explanation: fragments are collected, one answer at the end")
calls, phrases, waits = run(["Let me finish my sentence.", "So I was thinking about", "my schedule for tomorrow, and",
                             "I need to be at the airport by six."])
check("one answer, after the whole thought", len(calls) == 1 and calls[0][1] is True, str(calls))
check("it has all the parts", bool(calls) and "thinking about my schedule" in calls[0][0] and calls[0][0].endswith("by six."),
      str(calls))
check("no stock phrases in between", not phrases, str(phrases))
check("a finished statement waits only a short extra time", config.EXPLAIN_DONE_S in waits, str(waits))

print("7) a question inside the explanation is answered right away")
calls, phrases, waits = run(["Let me explain.", "I have a meeting at nine and a flight at noon, so what time should I leave?"])
check("answered once", len(calls) == 1, str(calls))

print("8) direct question, no hold: answered at once, normal timing")
calls, phrases, waits = run(["What time is it?"])
check("answered", len(calls) == 1 and calls[0] == ("What time is it?", False), str(calls))
check("no extra wait", waits[1] != config.LISTEN_WAIT_S and not rt.explaining, str(waits))

print("FAILED:" if failures else "ALL PASSED", failures or "")
sys.exit(1 if failures else 0)


