"""The turn's intent (cognition/understand.read_turn): what the words ask for, read by code once per turn. Offline: no
model, no PC actions.

Run:  .venv\\Scripts\\python -m tests.test_turn_intent
"""

import time

from tests.harness import setup_env

setup_env()

from room_agent import cognition  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core  # noqa: E402
from room_agent.cognition import understand  # noqa: E402
from room_agent.cognition.understand import TurnIntent, read_turn  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
ANY = object()

print("op / scope / target / value (table):")
# (words, op, scope, target, value): ANY = not checked
CASES = [
    ("turn on the lead.", "turn_on", "device", "LED", ANY),
    ("Turn on the LED", "turn_on", "device", "LED", ANY),
    ("switch off the lid", "turn_off", "device", "LED", ANY),
    ("turn the l.e.d. off", "turn_off", "device", "LED", ANY),
    ("Close the tab.", "close", "tab", ANY, ANY),
    ("close chrome", "close", "app", "chrome", ANY),
    ("close this tab please", "close", "tab", ANY, ANY),
    ("Cancel the mission.", "cancel", "mission", ANY, ANY),
    ("cancel the mission for good", "cancel_for_good", "mission", ANY, ANY),
    ("pause the mission", "pause", "mission", ANY, ANY),
    ("Stop. No.", "stop", None, ANY, ANY),
    ("Put volume 50%.", "set", "volume", ANY, 50),
    ("Lower the volume, please.", "lower", "volume", ANY, ANY),
    ("Turn off music.", "turn_off", "media", ANY, ANY),
    ("Put it at 20%.", "set", ANY, ANY, 20),
    ("is the door closed?", "ask", ANY, ANY, ANY),
    ("how much token did we consume", "report", ANY, ANY, ANY),
    ("Call me boss from now on", ANY, "memory", ANY, ANY),
    ("set a timer for 5 minutes", ANY, "timer", ANY, 5),
    ("dim the lights", "lower", "device", ANY, ANY),
]
for text, op, scope, target, value in CASES:
    it = read_turn(text)
    ok = all(want is ANY or got == want for got, want in ((it.op, op), (it.scope, scope), (it.target, target),
                                                         (it.value, value)))
    t.check(f"{text!r} -> " + " ".join(f"{k}={v}" for k, v in (("op", op), ("scope", scope), ("target", target),
                                                                ("value", value)) if v is not ANY), ok, it.describe())

print("Only a device op makes 'lead' a device; business requests stay business:")
it = read_turn("lead on")
t.check("'lead on' (no verb) -> not the device", it.scope != "device", it.describe())
it = read_turn("find me leads for plumbers in Oakland")
t.check("'find me leads for plumbers in Oakland' -> not the device", it.scope != "device", it.describe())
it = read_turn("turn on the lead.")
t.check("'turn on the lead.' -> not a business mission", not it.business and it.route != "business_mission",
        (it.route, it.business))
it = read_turn("how much token did we consume")
print(f"  (note) goals.route('how much token did we consume') kept as: {it.route!r}")
t.check("'how much token did we consume' -> not a business mission", not it.business, it.route)
for text in ("find three restaurants in Daly City without a website",
             "make a research of restaurants that doesn't have a website, give me three"):
    it = read_turn(text)
    t.check(f"{text!r} -> a business mission", it.business and it.route == "business_mission", it.describe())
it = read_turn("how do I find restaurants without websites")
t.check("'how do I find restaurants without websites' -> read without crashing (business is either)",
        isinstance(it.business, bool), it.describe())
it = read_turn("don't close chrome")
t.check("'don't close chrome' -> close, negated", it.op == "close" and it.negated, it.describe())

print("Never raises, stays fast:")
for bad in ("", None, "???", "x" * 500, 42, "turn " * 100):
    try:
        it = read_turn(bad)
        ok = isinstance(it, TurnIntent)
    except Exception as e:  # noqa: BLE001
        ok, it = False, e
    t.check(f"read_turn({str(bad)[:20]!r}) -> a TurnIntent, no exception", ok, it)
t.check("an empty turn reads as nothing", read_turn("").op is None and read_turn("").scope is None)
read_turn("warm up")
t0 = time.perf_counter()
for text, *_ in CASES * 5:
    read_turn(text)
ms = (time.perf_counter() - t0) * 1000 / (len(CASES) * 5)
t.check(f"under 2 ms a turn ({ms:.3f} ms)", ms < 2.0, ms)
t.check("describe() is one short line", read_turn("switch off the lid").describe() == "op=turn_off scope=device target=LED",
        read_turn("switch off the lid").describe())

print("Vocabulary (device names registered by an area):")
saved = list(core.VOCAB)  # (areas' own providers come back after)
core.VOCAB.clear()
core.register_vocabulary(lambda: ["Desk Lamp", "desk lamp", " ", None, "Fairy Lights"])
core.register_vocabulary(lambda: 1 / 0)  # (a broken provider is skipped)
t.check("vocabulary() de-duplicates, skips blanks and broken providers", core.vocabulary() == ["Desk Lamp", "Fairy Lights"],
        core.vocabulary())
it = read_turn("turn off the desk lamp")
t.check("a registered name is the device, spelled as registered", it.scope == "device" and it.target == "Desk Lamp",
        it.describe())
core.VOCAB[:] = saved

print("Optional Capability fields (default: today's behavior):")
cap = core.Capability("x_test", "test", {"type": "object", "properties": {}}, lambda a: "OK")
t.check("scope / precheck / named_in_words / asks default to None",
        (cap.scope, cap.precheck, cap.named_in_words, cap.asks) == (None, None, None, None))
t.check("runtime: reply_style None, ack_word ''", rt.reply_style is None and rt.ack_word == "")

print("begin_turn stores it on the turn:")
rt.new_turn("Turn on the LED")
t.check("a new turn starts with no intent", rt.turn.intent is None)
info = cognition.begin_turn("Turn on the LED")
cognition.end_turn("", None)
t.check("rt.turn.intent is set (op turn_on, scope device) and info['intent'] is the same object",
        isinstance(rt.turn.intent, TurnIntent) and rt.turn.intent.op == "turn_on" and rt.turn.intent.scope == "device"
        and info.get("intent") is rt.turn.intent, getattr(rt.turn.intent, "describe", lambda: None)())
real = understand.read_turn
understand.read_turn = lambda text: 1 / 0
try:
    rt.new_turn("close the tab")
    info = cognition.begin_turn("close the tab")
    cognition.end_turn("", None)
    t.check("a failing read_turn never breaks the turn (intent None, level still set)",
            rt.turn.intent is None and info.get("level"), info)
except Exception as e:  # noqa: BLE001
    t.check("a failing read_turn never breaks the turn (intent None, level still set)", False, e)
finally:
    understand.read_turn = real

t.done("TURN INTENT")
