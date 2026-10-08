"""Timer / alarm corrections, offline (scripted model): "no, I said 3" leaves exactly one timer, the corrected one; a new
request still adds one; an old or failed correction never removes the original. No PC actions, no API calls.

Run:  .venv\\Scripts\\python -m tests.test_timer_correction
"""

import time

from tests.harness import setup_env

setup_env()

from room_agent.actions import core  # noqa: E402
from room_agent.actions.context import env  # noqa: E402
from room_agent.tools import timers  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

t = Checker()
convo = Conversation()


def running():
    """[(kind, seconds left or HH:MM)] of everything set, soonest first."""
    with timers._lock:
        items = sorted(timers._items.values(), key=lambda i: i["due"])
    return [(i["kind"], round(i["due"] - time.time()) if i["kind"] == "timer" else time.strftime("%H:%M", time.localtime(i["due"])))
            for i in items]


def clear():
    timers.cancel_timer("all")
    env.last_successful_action = None


def near(secs, want):
    return abs(secs - want) <= 3


print("'No, I said 3':")
convo.say("set a timer for 2 minutes", scripts=[{"tools": [("set_timer", {"seconds": 120})]}, {"text": "Two minutes, go."}])
t.check("first: one 2-minute timer", len(running()) == 1 and near(running()[0][1], 120), running())
convo.say("no, I said 3", scripts=[{"tools": [("set_timer", {"seconds": 180})]}, {"text": "Got it, three minutes."}])
r = running()
t.check("after the correction: exactly one timer, 3 minutes (the 2-minute one is gone)", len(r) == 1 and near(r[0][1], 180), r)
for said in ("no, I meant 4 minutes", "make it 5 minutes", "nope, 6"):
    want = int("".join(c for c in said if c.isdigit())) * 60
    convo.say(said, scripts=[{"tools": [("set_timer", {"seconds": want})]}, {"text": "Done."}])
    r = running()
    t.check(f"{said!r} -> still one timer, the corrected length", len(r) == 1 and near(r[0][1], want), r)

print("Not a correction:")
clear()
convo.say("set a timer for 2 minutes", scripts=[{"tools": [("set_timer", {"seconds": 120})]}, {"text": "Okay."}])
convo.say("and another one for 3 minutes", scripts=[{"tools": [("set_timer", {"seconds": 180})]}, {"text": "Okay."}])
t.check("'another one for 3 minutes' adds a second timer", len(running()) == 2, running())
clear()
convo.say("set two timers, 1 minute and 2 minutes", scripts=[{"tools": [("set_timer", {"seconds": 60}), ("set_timer", {"seconds": 120})]},
                                                         {"text": "Both set."}])
t.check("two timers asked for in one request: both kept", len(running()) == 2, running())

print("Safety:")
clear()
convo.say("set a timer for 2 minutes", scripts=[{"tools": [("set_timer", {"seconds": 120})]}, {"text": "Okay."}])
env.last_successful_action.at -= 600  # (ten minutes ago)
convo.say("no, I said 3", scripts=[{"tools": [("set_timer", {"seconds": 180})]}, {"text": "Okay."}])
t.check("a 'correction' long after the timer was set doesn't silently cancel it", len(running()) == 2, running())
clear()
convo.say("set a timer for 2 minutes", scripts=[{"tools": [("set_timer", {"seconds": 120})]}, {"text": "Okay."}])
convo.say("no, I said 0 seconds", scripts=[{"tools": [("set_timer", {"seconds": 0})]}, {"text": "That didn't work."}])
r = running()
t.check("a correction that fails leaves the original running (never none)", len(r) == 1 and near(r[0][1], 120), r)

print("Alarms:")
clear()
convo.say("wake me up at 7", scripts=[{"tools": [("set_alarm", {"time": "07:00"})]}, {"text": "Alarm at 7."}])
convo.say("no, I said 7:30", scripts=[{"tools": [("set_alarm", {"time": "07:30"})]}, {"text": "7:30 then."}])
r = running()
t.check("'no, I said 7:30' -> one alarm, at 7:30", r == [("alarm", "07:30")], r)
ctx = " ".join(core.context_lines("no, I said 3 minutes"))
t.check("the model is told a correction replaces the timer (no cancel needed)", "CORRECTING" in ctx and "replaces the old one" in ctx)
clear()
t.done("TIMER CORRECTION")
