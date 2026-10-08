"""Regression tests for the live-conversation bugs (alarm state, phone-call grounding, barge-in on choppy speech,
unfinished sentences, SocialState confidence, repeated offers, tool disclosure). Offline: scripted model, temp files,
a fake audio engine; no PC actions, no network.

Run:  .venv\\Scripts\\python -m tests.test_live_fixes
"""

import itertools
import time

from tests.harness import setup_env

setup_env()

from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

t = Checker()
core.ensure_loaded()

# ---------------------------------------------------------------- 1. alarm state
print("1. Alarm state:")
from room_agent.tools import timers  # noqa: E402

timers.set_alarm("07:00", label="wake up", daily=True)
timers.set_alarm("15:30", label="dentist")
rt.last_ring = {"label": "wake up", "message": "Morning! Time to get up.", "kind": "alarm", "at": time.time(), "stopped": True}
ctx = "\n".join(core.context_lines("okay okay I'm up"))
t.check("context: the alarm that rang is FINISHED and stopped", "FINISHED" in ctx and "stopped when they spoke" in ctx, ctx[-400:])
t.check("context: tomorrow's daily occurrence is named as a different one, not ringing", "NEXT occurrence" in ctx, ctx[-400:])


def labels():
    with timers._lock:
        return sorted(i["label"] for i in timers._items.values())


for said, label in (("stop", "wake up"), ("okay stop it", "all"), ("turn it off", "alarm")):
    rt.new_turn(said)
    r = executor.execute("cancel_timer", {"label": label, "confidence": 0.95})
    t.check(f"'{said}' right after it stopped: nothing else is cancelled", r.message.startswith("OK: nothing to stop")
            and labels() == ["dentist", "wake up"], (r.message, labels()))
rt.new_turn("cancel my wake up alarm for tomorrow")
r = executor.execute("cancel_timer", {"label": "wake up", "confidence": 0.95})
t.check("an explicit 'cancel ... tomorrow' still cancels it", r.success and labels() == ["dentist"], (r.message, labels()))
rt.last_ring = {"label": "dentist", "message": "", "kind": "alarm", "at": time.time() - 600, "stopped": True}
rt.new_turn("stop the dentist alarm")
r = executor.execute("cancel_timer", {"label": "dentist", "confidence": 0.95})
t.check("long after a ring, 'stop the dentist alarm' works normally", r.success and labels() == [], (r.message, labels()))


class RingEngine:
    """Just enough engine for timers._ring: you speak right after the alarm's first announcement."""

    def __init__(self):
        self.reads = itertools.count()
        self.interrupted = __import__("threading").Event()

    @property
    def user_voice_at(self):
        return 0.0 if next(self.reads) == 0 else 5.0

    def wait_drained(self):
        pass


rt.engine, rt.tts_enabled = RingEngine(), False
item = {"id": 99, "kind": "alarm", "label": "nap", "message": "Nap's over.", "due": time.time(), "repeat": True, "daily": False}
rt.last_ring = {"label": "nap", "message": "Nap's over.", "kind": "alarm", "at": time.time(), "stopped": False}
timers._ring(item)
t.check("an alarm stopped by speaking in a pause is recorded as stopped (latest verified state)",
        rt.ringing is None and rt.last_ring["stopped"] is True, rt.last_ring)
rt.engine, rt.last_ring = None, None

# ---------------------------------------------------------------- 2. phone-call grounding
print("2. Phone-call grounding:")
from room_agent.prompt import runtime_context  # noqa: E402
from room_agent.truth import ClaimGuard  # noqa: E402

g = ClaimGuard(lambda: 0, lambda: False)
for s in ("Want me to ring your phone instead?", "I can call you when it goes off.", "Should I set an alarm on your phone?",
          "I could send a reminder to your phone.", "I'll text you the details."):
    t.check(f"held back (no such tool): {s!r}", g.unverified(s) == ["phone_offer"], g.unverified(s))
for s in ("I can't call you on request, sorry.", "I can't call you on request; I only call you by myself while you're driving, "
          "for something important.", "Call me if you need anything.", "Is your phone on silent?"):
    t.check(f"allowed (honest / the real feature): {s!r}", g.unverified(s) == [], g.unverified(s))
rt.new_turn("can you call me")
t.check("the capability list says calling/texting their phone on request is NOT available",
        "texting or calling their own phone" in runtime_context("can you call me").split("NOT available")[-1])
convo = Conversation()
convo.say("can you call me when my alarm goes off?",
          scripts=[{"text": "I can't call your phone. Want me to ring your phone with a reminder instead?"},
                   {"text": "I can't call your phone; the alarm rings here in the room."}])
t.check("a false offer is never spoken; the corrected answer is", convo.said() == ["I can't call your phone.",
        "I can't call your phone; the alarm rings here in the room."] or "ring your phone" not in " ".join(convo.said()), convo.said())

# ---------------------------------------------------------------- 3. barge-in on choppy speech
print("3. Barge-in:")
from room_agent.audio.engine import BargeTracker  # noqa: E402


def run(pattern, gap=3, win=4, barge_frames=3, min_frames=4):
    """Feed a voice pattern (1 = a frame that looks like voice) -> 'judge' (enough to verify) or the rejections."""
    tr, out = BargeTracker(), []
    for n, bit in enumerate(pattern, 1):
        event, ep = tr.step(n, bool(bit), gap)
        if event == "too_short":
            out.append(f"too short ({ep['frames']})")
        if bit and tr.sustained(win, barge_frames, min_frames):
            return "judge", out
    return "none", out


t.check("continuous speech is judged right away (unchanged)", run([1, 1, 1, 1, 1])[0] == "judge")
choppy = [1, 1, 0, 0, 0, 1, 1, 0, 0, 0, 1, 1, 0, 0, 0, 1, 1]  # a long interruption through the echo canceller
t.check("a long interruption arriving in pieces is judged (it used to be 'too short' piece after piece)",
        run(choppy)[0] == "judge", run(choppy))
t.check("a short real word in two pieces ('st-op') is judged in sensitive mode",
        run([1, 1, 0, 1, 1, 1], gap=5, win=6, min_frames=3)[0] == "judge")
blips = [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]
t.check("isolated echo blips far apart never reach the judge (no extra checks, no false stop)", run(blips)[0] == "none", run(blips))
t.check("a rejection says how much voice there was", run([1, 1, 0, 0, 0])[1] == ["too short (2)"], run([1, 1, 0, 0, 0]))

# ---------------------------------------------------------------- 4. unfinished utterances
print("4. Unfinished utterances:")
from room_agent.text import looks_unfinished  # noqa: E402

for s, want in (("but can you like", True), ("so could you, um", True), ("and like", True), ("can you uh", True),
                ("I need you to like", True), ("what do you like", False), ("turn off the lights um", False),
                ("open spotify", False), ("what time is it", False), ("I like it", False)):
    t.check(f"{s!r} -> {'wait for the rest' if want else 'answer now'}", looks_unfinished(s) is want)

# ---------------------------------------------------------------- 5. SocialState confidence
print("5. SocialState:")
from room_agent.social.signals import Evidence  # noqa: E402
from room_agent.social.state import SocialState  # noqa: E402

s = SocialState()
now = time.time()
for k in range(5):  # the same history-derived observation, re-derived on five turns in a row
    s.turns += 1
    s.add([Evidence("frustration", 0.5, "behavior", now + k * 10, "cut Jarvis off")])
v = s.value("frustration", now + 40)
t.check("re-derived evidence isn't counted as 5 independent confirmations", 0.4 < v < 0.5 and len(s.evidence) == 1, (v, len(s.evidence)))
t.check("...and it keeps decaying from when it first happened", s.value("frustration", now + 600) < 0.15,
        s.value("frustration", now + 600))
s.add([Evidence("frustration", 0.4, "language", now + 50, "ugh"), Evidence("frustration", 0.45, "language", now + 50, "seriously?")])
snap = s.snapshot(now + 60)
t.check("two sources still raise it, but confidence never reads 1.00", snap["confidence"] < 0.95 and snap["frustration"] in ("medium", "high"),
        snap)
s2 = SocialState()
s2.turns = 3
s2.add([Evidence("low", 0.6, "language", now, "so tired")])
t.check("low energy fades over turns without new evidence", s2.value("low", now + 1800) < s2.value("low", now) / 3)

# ---------------------------------------------------------------- 6. repeated offers
print("6. Repeated offers:")
rt.recent[:] = [{"role": "user", "text": "can you call me", "time": ""},
                {"role": "assistant", "text": "I can't call your phone. Want me to set a timer instead?", "time": ""}]
lines = "\n".join(core.context_lines("no, never mind"))
t.check("after 'no', the context notes what was already explained and offered", "you_already_explained" in lines
        and "you_already_offered" in lines and "set a timer" in lines, lines[-300:])
t.check("after 'yes', no such note (they took it)", "you_already_offered" not in "\n".join(core.context_lines("yes please")))
rt.recent[:] = [{"role": "user", "text": "set a timer", "time": ""}, {"role": "assistant", "text": "Sure, I can do that.", "time": ""}]
t.check("'I can do that' is not counted as an offer", "you_already_offered" not in "\n".join(core.context_lines("thanks")))
rt.recent[:] = []

# ---------------------------------------------------------------- 7. tool disclosure
print("7. Tool disclosure:")
from room_agent.llm.openai_backend import relevant_tools  # noqa: E402
from room_agent.tools.registry import active_tools  # noqa: E402

chatty = [{"role": "user", "content": "how's it going"},
          {"role": "assistant", "content": "Good! I could open Spotify, change the volume, check the door, or set a timer."},
          {"role": "user", "content": "nah just chatting"}]
names = {x["name"] for x in relevant_tools(active_tools(), chatty)}
t.check("areas Jarvis merely mentioned aren't offered on the next turns", not names & {"open_app", "set_volume", "set_timer"},
        sorted(names))
offer = [{"role": "user", "content": "I'm cooking"}, {"role": "assistant", "content": "Want me to set a timer for the pasta?"},
         {"role": "user", "content": "yes"}]
t.check("'yes' to Jarvis's own question keeps that area's tools", "set_timer" in {x["name"] for x in relevant_tools(active_tools(), offer)})
used = [{"role": "user", "content": "turn it down"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "volume_down", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "OK: 40%"}]},
        {"role": "assistant", "content": "It's at 40."}, {"role": "user", "content": "more"}]
t.check("a tool used recently keeps its area ('more' after turning it down)", "volume_down" in {x["name"] for x in relevant_tools(active_tools(), used)})

t.done("LIVE FIXES")
