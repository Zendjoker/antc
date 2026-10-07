"""Goal-first tool use: "set an alarm in 10 seconds" is a countdown timer, not a reason to ask "timer or alarm?".
No real API calls: OpenAI is a fake that plays back what a model might do (including the wrong tool).

Run:  .venv\\Scripts\\python -m tests.test_intent
"""

import os
import sys
import time

from tests.harness import setup_env

TMP = setup_env()

from room_agent import runtime as rt  # noqa: E402
from room_agent.tools import timers  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

convo = Conversation()
fake, REQUESTS, SPOKEN = convo.model, convo.requests, convo.spoken
t = Checker()
check, FAILS = t.check, t.fails
from room_agent.conversation.turn import take_turn  # noqa: E402


def results(history):
    return [b["content"] for m in history if isinstance(m["content"], list) for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result"]


def scenario(said, tool_call, follow_up, final=True):
    """One turn where the model makes `tool_call`, then (given the tool result) says `follow_up`."""
    timers.cancel_timer("all")
    rt.pending, rt.last_ring = None, None
    REQUESTS.clear()
    SPOKEN.clear()
    fake.scripts = [{"tool": tool_call}, {"text": follow_up}]
    history = []
    rt.turn_text = said
    take_turn(history, said, final=final)
    items = sorted(timers._items.values(), key=lambda i: i["due"])
    timers.cancel_timer("all")
    return history, items


# ---------------------------------------------------------------- the reported bug
print("the reported sentence, whichever way the model reaches for it:")
SAID = "Can you set an alarm in 10 seconds and wake me up from bed? Please motivate me."
MOTIVATE = "Done, ten seconds. Up you get, today's yours to win!"
for label, call in [("set_alarm with no time", ("set_alarm", {"label": "wake up", "message": "Rise and shine, champ!"})),
                    ("set_alarm with time='in 10 seconds'", ("set_alarm", {"time": "in 10 seconds", "label": "wake up"})),
                    ("set_timer (the right tool)", ("set_timer", {"seconds": 10, "label": "wake up"}))]:
    for final in (True, False):
        history, items = scenario(SAID, call, MOTIVATE, final=final)
        tag = f"{label}, {'final' if final else 'mid-thought'}"
        check(f"{tag}: a 10 second countdown rings until acknowledged",
              len(items) == 1 and items[0]["kind"] == "timer" and items[0]["repeat"]
              and 7 <= items[0]["due"] - time.time() <= 10, items)
        check(f"{tag}: tool result is OK (no 'needs a clock time')", any(r.startswith("OK") for r in results(history))
              and not any("NEEDS" in r or "24-hour" in r for r in results(history)), results(history))
        check(f"{tag}: the motivation is spoken", any("today's yours" in s for s in SPOKEN), SPOKEN)
_, items = scenario(SAID, ("set_alarm", {"message": "Rise and shine, champ!"}), MOTIVATE)
check("the model's wake-up message is kept on the countdown", items and items[0]["message"] == "Rise and shine, champ!")
sent = "\n".join(m["content"] for m in REQUESTS[0]["messages"] if m["role"] == "system")
check("the prompt says: goal first, never make them pick timer vs alarm", "They don't know or care about your tool names" in sent)
check("the prompt says: do every request in the message", "do all of them in this reply" in sent)
check("both timer tools are offered for this sentence",
      {"set_timer", "set_alarm"} <= {t["function"]["name"] for t in REQUESTS[0]["tools"]})

# ---------------------------------------------------------------- the other examples
print("other phrasings (the model may pick either tool):")
for said, call, kind, secs in [
    ("Wake me up in 10 minutes", ("set_alarm", {}), "timer", 600),
    ("Alarm me in 30 seconds", ("set_alarm", {"label": "alarm"}), "timer", 30),
    ("Remind me in 20 seconds to leave", ("set_alarm", {"label": "leave", "message": "Time to leave!"}), "timer", 20),
    ("Remind me in 20 seconds to leave", ("set_timer", {"seconds": 20, "label": "leave"}), "timer", 20),
    ("Set an alarm for 8 AM", ("set_alarm", {"time": "08:00"}), "alarm", None),
    ("Wake me tomorrow at 7", ("set_alarm", {"time": "07:00", "label": "wake up"}), "alarm", None),
    ("wake me in half an hour", ("set_alarm", {"time": "half an hour"}), "timer", 1800),
    ("alarm in 1 hour 30 minutes", ("set_alarm", {}), "timer", 5400),
]:
    history, items = scenario(said, call, "Got it.")
    ok = len(items) == 1 and items[0]["kind"] == kind
    if ok and secs:
        left = items[0]["due"] - time.time()
        ok = secs - 3 <= left <= secs
    check(f"{said!r} via {call[0]} -> {kind}" + (f" {secs}s" if secs else ""), ok, (items, results(history)))
history, items = scenario("Wake me tomorrow", ("set_alarm", {}), "What time?")
check("'Wake me tomorrow' (no time at all) still asks for the time", not items
      and any(r.startswith("NEEDS") for r in results(history)), results(history))

print("time phrases:")
for text, want in [("in 10 seconds", 10), ("10s", 10), ("in ten minutes", 600), ("in a minute", 60),
                   ("in half an hour", 1800), ("in an hour and 15 minutes", 4500), ("in 2 hrs", 7200),
                   ("at 7:30", None), ("8 AM", None), ("tomorrow at 7 o'clock", None), ("as soon as possible", None),
                   ("wake me up", None)]:
    check(f"{text!r} -> {want}", timers.relative_seconds(text) == want, timers.relative_seconds(text))

print("\nALL INTENT TESTS PASSED" if not FAILS else f"\nFAILED: {FAILS}")
sys.stdout.flush()
os._exit(1 if FAILS else 0)
