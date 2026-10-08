"""Fixes.md acceptance tests (the automatable part): false memories, false transcripts, interruptions, sensor grounding,
capability-aware actions, fewer model calls, clean shutdown, corrections. Offline: scripted model, fake memory model,
simulated Zigbee2MQTT and audio; no PC actions, no network. Hardware behavior (a real mic, a real door) is NOT proven
here: see the manual procedure in the report.

Run:  .venv\\Scripts\\python -m tests.test_reliability_fixes
"""

import json
import threading
import time

from tests.harness import setup_env

TMP = setup_env(UNITS="imperial")

from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor  # noqa: E402
from room_agent.memory import MemoryWriter  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

t = Checker()
core.ensure_loaded()


# ================================================================ 1. false memories
print("1. False memories:")


class FakeMemoryModel:
    """Plays the memory model: returns whatever facts we tell it to propose (with or without a quote)."""

    def __init__(self):
        self.propose, self.summary, self.seen = {}, "SKIP", []

    def tool(self, system, prompt, tool):
        self.seen.append(prompt)
        return self.propose

    def text(self, system, prompt):
        self.seen.append(prompt)
        return self.summary


mm = FakeMemoryModel()
w = MemoryWriter(rt.memory, "Adam", mm.tool, mm.text)
rt.writer = w


def facts():
    return [f["fact"] for f in rt.memory.snapshot()["facts"]]


mm.propose = {"profile": {}, "remove_fact_ids": [], "add_facts": [{"content": "Wants a daily checklist", "category": "preference",
                                                                    "quote": "daily checklist"}]}
w.observe("no thanks, I'm good", "Okay!", asked="Want me to make you a daily checklist?")
w.flush(5)
t.check("assistant suggests a checklist -> no preference created (not in their words)", "Wants a daily checklist" not in facts(), facts())
w.observe("I want a daily checklist every morning", "Got it.")
w.flush(5)
saved = [f for f in rt.memory.snapshot()["facts"] if f["fact"] == "Wants a daily checklist"]
t.check("user explicitly asks for a daily checklist -> saved, as their own statement", saved and saved[0]["source"] == "user_statement",
        saved)
rt.memory.remove_ids([f["id"] for f in saved])

# the observed bug: misheard "dailies list", then "I didn't say none of this"
from room_agent.conversation import corrections  # noqa: E402

mm.propose = {"profile": {}, "remove_fact_ids": [], "add_facts": [{"content": "Wants a dailies list (daily checklist or habits)",
                                                                    "category": "preference", "quote": "dailies list"}]}
rt.recent[:] = [{"role": "user", "text": "I need a dailies list", "time": "2026-10-07 23:50:00"},
                {"role": "assistant", "text": "Sure, what should be on your dailies list?", "time": "2026-10-07 23:50:02"}]
w.observe("I need a dailies list", "Sure, what should be on your dailies list?")
w.flush(5)
learned_first = "Wants a dailies list (daily checklist or habits)" in facts()
rt.new_turn("I didn't say none of this")
wrong = corrections.on_user_turn("I didn't say none of this")
t.check("'I didn't say none of this' -> recognized as a correction of the last message", wrong == "I need a dailies list", wrong)
t.check("...and the memory learned from the misheard words is removed", learned_first and "dailies" not in " ".join(facts()), facts())
w.observe("I didn't say none of this", "My bad, I misheard you.")
mm.summary = "Adam corrected a misheard request."
mm.seen.clear()
w.conversation_ended(0)
w.flush(5)
summary_input = " ".join(mm.seen)
t.check("the summary sees it as misheard, never as a request", "[misheard] I need a dailies list" in summary_input
        and "Assistant: Sure, what should be on your dailies list?" not in summary_input, summary_input[-300:])
t.check("the correction survives a restart (saved dialogue keeps the mark)",
        any(m["text"].startswith("[misheard]") for m in rt.memory.load_recent(24)), rt.memory.load_recent(24)[:2])
mm.propose = {"profile": {}, "remove_fact_ids": [], "add_facts": [{"content": "Likes jazz", "category": "preference", "quote": "I like jazz"}]}
w.observe("I like jazz", "Nice.", uncertain=True)
w.flush(5)
t.check("uncertain speech recognition never becomes permanent knowledge", "Likes jazz" not in facts(), facts())
mm.propose = {"profile": {"home_location": "Paris"}, "profile_quotes": {}, "remove_fact_ids": [], "add_facts": []}
w.observe("my sister lives in Paris", "Cool.")
w.flush(5)
t.check("a guessed profile value without their words is refused (sister's city isn't their home)",
        rt.memory.get("home_location") != "Paris", rt.memory.get("home_location"))

# ================================================================ 2. false transcripts
print("2. Speech recognition:")
from room_agent.audio import speech_check as sc  # noqa: E402


class Delay:
    delay = 0.12


class Engine:
    delay = Delay()
    user_voice_at = 0.0
    interrupted = threading.Event()

    def is_playing(self):
        return False


rt.engine = Engine()
rt.recent_speech.clear()
rt.turn_speech.clear()
rt.recent_speech.append("If you want, I can set that up for you right now.")
now = time.time()
rt.tts_end = now
during = now - 1.0  # it started a second before the agent stopped
sure, unsure = {"logprob": -0.3, "no_speech": 0.05}, {"logprob": -0.9, "no_speech": 0.3}
rt.stt_confidence = sure
t.check("'If you want' heard over Jarvis's own sentence -> its echo, not a command",
        sc.classify_audio("If you want.", during)[0] == sc.AGENT_ECHO, sc.classify_audio("If you want.", during))
rt.stt_confidence = unsure
t.check("'And you're in danger' over Jarvis's audio, recognition unsure -> not a user message",
        sc.classify_audio("And you're in danger.", during)[0] == sc.NOISE, sc.classify_audio("And you're in danger.", during))
for word in ("Stop.", "Wait.", "No."):
    t.check(f"'{word}' over Jarvis's audio -> still yours (short commands are kept)", sc.classify_audio(word, during)[0] == sc.USER,
            sc.classify_audio(word, during))
rt.stt_confidence = sure
t.check("'Yes.' heard clearly -> yours (an unsure 'yes' over its audio isn't taken as a confirmation)",
        sc.classify_audio("Yes.", during)[0] == sc.USER, sc.classify_audio("Yes.", during))
t.check("a short phrase after Jarvis finished (echo window over) -> yours", sc.classify_audio("dailies list", now + 2)[0] == sc.USER)
t.check("quiet room: 'Thank you.' (silence hallucination) -> noise", sc.classify_audio("Thank you.", None)[0] == sc.NOISE)
t.check("a full sentence -> captured as yours", sc.classify_audio("Can you set a timer for ten minutes please", now + 2)[0] == sc.USER)
t.check("uncertainty band: barely-passing recognition is flagged, short commands never are",
        sc.transcript_uncertain("I need a dailies list", unsure) and not sc.transcript_uncertain("stop", unsure)
        and not sc.transcript_uncertain("I need a dailies list", sure))
from room_agent.tools import timers  # noqa: E402

timers.set_timer(600, label="tea")
rt.new_turn("cancel the tea timer")
rt.turn.uncertain = True
r = executor.execute("cancel_timer", {"label": "tea", "confidence": 0.95})
t.check("low-confidence speech -> nothing irreversible (cancel asks first)", not r.success and "wasn't sure it heard" in r.message,
        r.message)
r = executor.execute("set_timer", {"seconds": 60, "label": "check"})
t.check("...but a safe, undoable action still just runs", r.success, r.message)
rt.turn.uncertain = False
from room_agent.audio import stt  # noqa: E402

runs = []


class FakeWhisper:
    def transcribe(self, audio, **kw):
        runs.append(1)
        seg = type("S", (), {"text": " stop", "avg_logprob": -0.2, "no_speech_prob": 0.01, "compression_ratio": 1.1})()
        return [seg], None


stt._whisper = FakeWhisper()
import numpy as np  # noqa: E402

pcm = (np.random.RandomState(1).randn(8000) * 800).astype(np.int16)
a, b = stt.whisper_text(pcm), stt.whisper_text(pcm)
t.check("the same audio is never transcribed twice (barge check + utterance)", a == b == "stop" and len(runs) == 1, runs)
stt._whisper = None

# ================================================================ 4. sensor grounding
print("4. Sensors:")
from room_agent.tools import zigbee  # noqa: E402

B = "zigbee2mqtt"
DEVICES = [{"friendly_name": "Door sensor", "ieee_address": "0x1", "power_source": "Battery",
            "definition": {"model": "MCCGQ11LM", "description": "Door and window sensor", "exposes": [{"property": "contact"}]}},
           {"friendly_name": "Vibration sensor", "ieee_address": "0x3", "power_source": "Battery",
            "definition": {"model": "DJT11LM", "description": "Vibration sensor", "exposes": [{"property": "vibration"}, {"property": "action"}]}},
           {"friendly_name": "LED strip", "ieee_address": "0x4", "power_source": "Mains",
            "definition": {"model": "LGYCDD01LM", "description": "LED Strip T1", "exposes": [{"type": "light", "features": [
                {"property": "state"}, {"property": "brightness", "value_max": 254}, {"name": "color_xy", "property": "color"},
                {"property": "color_temp", "value_min": 153, "value_max": 370}]}]}}]


class FakeZ2M:
    def __init__(self):
        self.sent, self.answer = [], True

    def publish(self, topic, payload):
        name = topic[len(B) + 1:-len("/set")]
        cmd = json.loads(payload)
        self.sent.append((name, cmd))
        if self.answer:
            new = dict(cmd)
            if "color" in cmd:
                new.update(color_mode="xy", color={"x": 0.14, "y": 0.1})
            threading.Timer(0.05, lambda: zigbee.hub.handle(f"{B}/{name}", json.dumps(new).encode())).start()


fz = FakeZ2M()
hub = zigbee.hub
hub.client = fz
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())
hub.handle(f"{B}/Door sensor", b'{"contact": true}')
hub.handle(f"{B}/LED strip", b'{"state": "ON", "brightness": 200}')
from room_agent.conversation import greet  # noqa: E402
from room_agent.conversation.states import State  # noqa: E402

rt.state.go(State.LISTENING, "talking")  # (we're mid-conversation when the door opens)
greet.start()
hub.handle(f"{B}/Door sensor", b'{"contact": false}')
hub.handle(f"{B}/Door sensor", b'{"contact": true}')
ev = hub.last_event("Door sensor", "opened")
t.check("door opens -> the event is recorded", ev is not None)
t.check("greeting suppressed because Jarvis was busy -> the event stays, with why",
        ev and "no greeting because we were already talking" in ev["note"], ev)
ctx = "\n".join(core.context_lines("did you notice me come in?"))
t.check("'Did you notice me come in?' -> the context has the door event", "Door sensor opened at" in ctx, ctx[-500:])
t.check("no motion sensor installed -> said explicitly, never invented", "NO motion or presence sensor" in ctx)
t.check("the bed vibration sensor isn't presented as a room motion sensor", "NOT a room motion sensor" in ctx)
again = zigbee.Hub()
t.check("the door history survives a restart (saved on disk)", again.last_event("Door sensor", "opened") is not None)
hub.handle(f"{B}/Door sensor/availability", b'{"state": "offline"}')
r = executor.execute("home_sensors", {"device": "door"})
t.check("sensor offline -> reported unavailable, not guessed", "UNAVAILABLE" in r.message, r.message)
hub.handle(f"{B}/Door sensor/availability", b'{"state": "online"}')
rt.state.go(State.WAKE_WORD_ONLY, "test")

# ================================================================ 5. capability-aware actions
print("5. Capabilities:")
from room_agent.cognition import reflex  # noqa: E402
from room_agent.truth import ClaimGuard  # noqa: E402

for said, check in (("dim the LED to 30%", lambda s: s.get("brightness") == 76), ("change the color to blue", lambda s: "color" in s),
                    ("turn the light off", lambda s: s.get("state") == "OFF")):
    m = reflex.match(said)
    ok = m is not None and m[0].name == "set_light"
    if ok:
        rt.new_turn(said)
        line, res = reflex.run(*m)
        ok = res.success and res.verified and check(fz.sent[-1][1])
    t.check(f"'{said}' -> runs directly (no model, no question), verified", ok, (m and m[1], fz.sent[-1:]))
g = ClaimGuard(lambda: 0, lambda: False)
t.check("'Want me to cool it down or open the window?' with no such devices -> held back",
        g.unverified("Want me to cool it down or open the window?") != [])
t.check("...an honest 'I can't open the window' passes", g.unverified("I can't open the window, there's no controller connected.") == [])
fz.answer = False
rt.new_turn("lights on")
r = executor.execute("set_light", {"on": True})
t.check("device disconnected (no answer) -> never claimed as done", not r.success and "didn't answer" in r.message, r.message)
fz.answer = True
DEV2 = DEVICES + [dict(DEVICES[2], friendly_name="Desk lamp", ieee_address="0x5")]
hub.handle(f"{B}/bridge/devices", json.dumps(DEV2).encode())
r = executor.execute("set_light", {"on": True})
t.check("ambiguous light -> asks only which one", not r.success and "which light?" in r.message, r.message)
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())

# ================================================================ 6. fewer model calls
print("6. Model calls:")
convo = Conversation()
convo.say("give the strip a nice pink glow tonight", scripts=[{"tools": [("set_light", {"color": "pink"})]},
                                                             {"text": "SHOULD NOT BE NEEDED"}])
t.check("a verified simple action ends without a second model call", len(convo.requests) == 1
        and any("now" in s or "on" in s for s in convo.said()), (len(convo.requests), convo.said()))
fz.answer = False
convo.say("give the strip a green glow tonight", scripts=[{"tools": [("set_light", {"color": "green"})]}, {"text": "It didn't respond, sorry."}])
t.check("a failed action still gets the model's honest answer (2 calls)", len(convo.requests) == 2, len(convo.requests))
fz.answer = True

# ================================================================ 7. clean shutdown
print("7. Shutdown:")
from room_agent import cli  # noqa: E402
from room_agent.audio.engine import AudioEngine  # noqa: E402


class SlowStream:
    def abort(self):
        time.sleep(30)  # (a stuck driver)

    def close(self):
        pass


eng = AudioEngine.__new__(AudioEngine)
eng._lock, eng._buf, eng._streams = threading.Lock(), bytearray(b"\x01" * 100), [SlowStream()]
t0 = time.time()
eng.close(timeout=0.3)
eng.close(timeout=0.3)
t.check("audio close never hangs on a stuck stream, and is safe to call twice", time.time() - t0 < 2 and not eng._buf, time.time() - t0)
calls, exits = [], []


class E:
    def close(self):
        calls.append("audio")


real_exit = cli.os._exit
cli.os._exit = lambda code: exits.append(code)
rt.engine = E()
import room_agent.phone.tunnel as tunnel  # noqa: E402

real_stop = tunnel.stop
tunnel.stop = lambda: calls.append("tunnel")
cli.shutdown(flush_s=0.5)
cli.shutdown(flush_s=0.5)
cli.os._exit, tunnel.stop = real_exit, real_stop
t.check("shutdown: audio, memory, tunnel stopped once, then exit (a second call is a no-op)",
        calls == ["audio", "tunnel"] and exits == [0], (calls, exits))
rt.engine = None

# ================================================================ 8. corrections in conversation
print("8. Corrections:")
from room_agent.actions import pending  # noqa: E402

rt.recent[:] = []
convo = Conversation()
convo.say("send an email to sam", scripts=[{"text": "Who's Sam, what's the address?"}])
pending.confirming("set_timer", {"seconds": 600})
convo.say("that's not what I said", scripts=[{"text": "My bad, I misheard you. What did you need?"}])
t.check("misunderstands -> user corrects -> the half-done task is discarded", rt.pending is None)
system = "\n".join(m["content"] for m in convo.requests[0]["messages"] if m["role"] == "system") if convo.requests else ""
t.check("...the model is told it's discarded and must not continue it", "DISCARDED" in system, system[-400:])
convo.say("what time is it", scripts=[{"text": "It's 11:58."}])
system = "\n".join(m["content"] for m in convo.requests[0]["messages"] if m["role"] == "system")
t.check("...and the next turn isn't told to correct anything again (state is clean)", "DISCARDED" not in system)
t.check("...while the transcript keeps the misheard request marked", any(m["text"].startswith("[misheard]") for m in rt.recent),
        [m["text"] for m in rt.recent][:3])
t.done("RELIABILITY FIXES")
