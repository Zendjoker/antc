"""A ringing timer must stop the moment confirmed user speech arrives: python tests/test_ring_ack.py
(simulated engine and speaker; the final conversation turn uses the real model)"""
import os
import pathlib
import sys
import tempfile
import threading
import time

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ.update(REMINDERS_FILE=str(tmp / "rem.json"), MEMORY_DB=str(tmp / "mem.db"), SETTINGS_FILE=str(tmp / "set.json"),
                  ALARM_GAP_S="0.5", PYTHONIOENCODING="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from room_agent import runtime as rt  # noqa: E402
from room_agent.audio import speech_check as sc  # noqa: E402
from room_agent.audio.fillers import LOOP  # noqa: E402
from room_agent.conversation.turn import take_turn  # noqa: E402
from room_agent.tools import timers  # noqa: E402
from room_agent.truth import ClaimGuard  # noqa: E402


class Delay:
    delay = 0.4


class Engine:
    capturing = True
    interrupted = threading.Event()
    user_voice_at = 0.0
    delay = Delay()

    def flush(self):
        pass

    def wait_drained(self):
        pass


rt.engine, rt.tts_enabled = Engine(), True
spoken, failures = [], []


def speaker():  # plays one queued item at a time, slowly, like real playback
    while True:
        item = rt.speak_q.get()
        if isinstance(item, str) and item != LOOP:
            spoken.append((time.time(), str(item)))
            rt.tts_end = time.time() + 0.6
        time.sleep(0.4)
        rt.speak_q.task_done()


threading.Thread(target=speaker, daemon=True).start()


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


def real_speech_after_ring_starts(text):
    """What the session does with a transcript while the ring is going: classify, drop echo, else acknowledge."""
    label, detail = sc.classify_audio(text, time.time())
    dropped = label == sc.AGENT_ECHO or (label == sc.UNCERTAIN and rt.tts_end > time.time() and not rt.ringing)
    return label, dropped, detail


print("5-second timer rings, then the user speaks")
for phrase in ["I'm here.", "I'm heading to work right now."]:
    spoken.clear()
    timers.set_timer(5, "work", "Hey, time to head to work!")
    deadline = time.time() + 10
    while not (spoken and rt.ringing) and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(1.3)  # let it ring a couple of times (chimes + repeats are queued)
    rt.engine.user_voice_at = 0.0
    label, dropped, detail = real_speech_after_ring_starts(phrase)
    check(f"{phrase!r} is kept as user speech while ringing", not dropped, f"-> {label} ({detail})")
    before = len(spoken)
    t_ack = time.time()
    check("ring is acknowledged", timers.acknowledge_ring())
    time.sleep(3.0)  # long enough for several more repeats if it hadn't stopped
    after = [m for t, m in spoken[before:] if t > t_ack + 0.05]
    check("zero ringing messages after the user spoke", after == [], str(after))
    check("ring state cleared, queue empty", rt.ringing is None and rt.speak_q.empty())
    check("nothing is ringing any more", not timers.acknowledge_ring())

print("normal conversation continues")
history = []
spoken.clear()
take_turn(history, "I'm here.")
time.sleep(1.0)
reply = " ".join(m for _, m in spoken)
check("it answers normally", bool(reply) and "time to head" not in reply.lower(), repr(reply[:90]))
check("snooze context is available", rt.last_ring and rt.last_ring["label"] == "work")

print("claim checker")
g = ClaimGuard(lambda: 0, lambda: False)
check("casual 'Good, you're all set then.' is not blocked", g.unverified("Good, you're all set then.") == [])
check("a bare 'Done.' with no action still is", g.unverified("Done.") != [])

print("\nALL PASSED" if not failures else f"\nFAILED: {failures}")
sys.exit(1 if failures else 0)
