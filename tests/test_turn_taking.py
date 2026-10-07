"""Turn-taking: pauses while forming a request must not trigger an answer: python tests/test_turn_taking.py
(the last part uses the real model; everything else is offline)"""
import os
import pathlib
import sys
import tempfile
import threading
import time

import numpy as np

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ.update(REMINDERS_FILE=str(tmp / "rem.json"), MEMORY_DB=str(tmp / "mem.db"), SETTINGS_FILE=str(tmp / "set.json"),
                  PYTHONIOENCODING="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.audio.fillers import LOOP  # noqa: E402
from room_agent.conversation import session  # noqa: E402
from room_agent.text import join_fragments, looks_unfinished  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


print("1) is the speech trailing off?")
for t in ["Set an alarm...", "Can you...", "Set an alarm for", "I want you to", "Can you", "Remind me to call my mom and",
          "So basically,", "Set an alarm\u2026"]:
    check(f"unfinished: {t!r}", looks_unfinished(t))
for t in ["What time is it?", "Set a timer for 10 seconds.", "Set an alarm.", "What time is it", "Set a timer for ten seconds",
          "Thank you.", "Can you check the weather?", "Turn off the lights"]:
    check(f"finished: {t!r}", not looks_unfinished(t))
check("fragments join cleanly", join_fragments("Set an alarm...", "...8 AM.") == "Set an alarm 8 AM.",
      repr(join_fragments("Set an alarm...", "...8 AM.")))
check("a final full stop survives when nothing follows", join_fragments("Set a timer.", "") == "Set a timer.")


class Delay:
    delay = 0.4


class Engine:
    interrupted = threading.Event()
    capturing = True
    user_voice_at = 0.0
    delay = Delay()

    def drain_mic(self):
        pass

    def wait_drained(self):
        pass

    def flush(self):
        pass


texts = []


def run(script, model=None):
    """converse() with scripted utterances (None = a long silence)."""
    calls, phrases, waits, left = [], [], [], list(range(len(script)))
    rt.engine, rt.explaining, rt.patience = Engine(), False, 1.0
    texts[:] = list(script)

    def fake_record(mic_q, start_timeout=0, already_heard=False, **kw):
        waits.append(start_timeout)
        while left and texts[left[0]] is None:
            left.pop(0)  # a silence: the wait simply runs out
            return None
        return np.array([left.pop(0)]) if left else None

    def take(h, msg, raw=None, final=False):
        calls.append((raw, final))
        rt.control.clear()
        return model(raw, final) if model else None

    session.record_utterance = fake_record
    session.transcribe = lambda pcm: str(texts[int(pcm[0])])
    session.take_turn = take
    session.speak_phrase = lambda kind, history=None: (phrases.append(kind), (False, False))[1]
    session.settle_mic = lambda started=None: (False, False)
    session.filler_if_slow = session.stop_thinking = lambda *a, **k: None
    session.converse(None, [])
    return calls, [p for p in phrases if p not in ("sleep", "checkin")], waits


print("2) fragments across a pause become one turn")
calls, phrases, waits = run(["Set an alarm...", "...8 AM."])
check("'Set an alarm...' [pause] '...8 AM.' -> one request", calls == [("Set an alarm 8 AM.", True)], str(calls))
check("nothing said in between", not phrases)
calls, phrases, waits = run(["Can you...", "...check the weather?"])
check("'Can you...' [pause] '...check the weather?' -> one request", calls == [("Can you check the weather?", True)], str(calls))
calls, phrases, waits = run(["Set an alarm for", "8 AM."])
check("a dangling 'for' waits too", calls == [("Set an alarm for 8 AM.", True)], str(calls))


def hold_when_incomplete(raw, final):  # what the model path does when a required detail is missing
    if not final and "8" not in raw:
        rt.control["hold"] = True
        return "listen"
    return None


calls, phrases, waits = run(["Set an alarm.", "8 AM."], model=hold_when_incomplete)
check("a finished-sounding but incomplete request is held, then combined", calls == [("Set an alarm.", False), ("Set an alarm 8 AM.", True)],
      str(calls))
check("it waits MISSING_WAIT_S for the rest, not longer", waits[1] == config.MISSING_WAIT_S, str(waits[:2]))

print("3) complete requests are not delayed")
for text in ["Set a timer for 10 seconds.", "What time is it?", "What time is it"]:
    calls, phrases, waits = run([text])
    check(f"{text!r}: answered at once", calls == [(text, False)] and waits[1] not in (config.LISTEN_WAIT_S, config.MISSING_WAIT_S),
          f"{calls} {waits[:2]}")

print("4) a long silence after an incomplete request: it answers, once")
calls, phrases, waits = run(["Set an alarm...", None])
check("asked after the wait", calls == [("Set an alarm", True)] and waits[1] == config.LISTEN_WAIT_S, f"{calls} {waits[:2]}")

if "--offline" in sys.argv:
    print("FAILED:" if failures else "ALL PASSED", failures or "")
    sys.exit(1 if failures else 0)

print("5) real model: what a request with a missing detail does")
from room_agent.conversation.turn import take_turn  # noqa: E402
from room_agent.llm import claude  # noqa: E402

rt.tts_enabled = True
spoken, ran = [], []


def drain():
    while True:
        item = rt.speak_q.get()
        if isinstance(item, str) and item != LOOP:
            spoken.append(str(item))
        rt.speak_q.task_done()


threading.Thread(target=drain, daemon=True).start()
real_run_tool = claude.run_tool
claude.run_tool = lambda name, args: (lambda out: (ran.append((name, args, out[:60])), out)[1])(real_run_tool(name, args))


def turn(history, text, final=False):
    spoken.clear()
    ran.clear()
    t0 = time.time()
    signal = take_turn(history, text, raw=text, final=final)
    return signal, " ".join(spoken), time.time() - t0


for attempt in range(3):
    h = []
    signal, said, dt = turn(h, "Set an alarm.")
    check(f"run {attempt + 1}: 'Set an alarm.' -> holds, says nothing", signal == "listen" and not said and not ran,
          f"signal={signal!r} said={said!r} ran={ran}")
    signal, said, dt = turn(h, "Set an alarm 8 AM.", final=True)
    alarm = [r for r in ran if r[0] == "set_alarm"]
    check(f"run {attempt + 1}: combined with '8 AM.' -> set_alarm runs", bool(alarm) and alarm[0][2].startswith("OK"), f"{ran}")
    h = []
    turn(h, "Set an alarm.")
    signal, said, dt = turn(h, "Set an alarm", final=True)
    check(f"run {attempt + 1}: silence after it -> asks for the time only", "time" in said.lower() and not [r for r in ran if r[2].startswith("OK")],
          f"said={said!r}")

h = []
signal, said, dt = turn(h, "What time is it?")
check("'What time is it?' answered in the same turn", signal is None and bool(said), f"{said!r} ({dt:.1f}s)")
h = []
signal, said, dt = turn(h, "Set a timer for 10 seconds.")
check("'Set a timer for 10 seconds.' runs at once", any(r[0] == "set_timer" and r[2].startswith("OK") for r in ran), f"{ran} ({dt:.1f}s)")

print("FAILED:" if failures else "ALL PASSED", failures or "")
sys.exit(1 if failures else 0)
