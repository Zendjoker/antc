"""Tests for four fixes: stale timer state, short answers dropped as noise, location honesty, smaller per-turn context.
No real API calls: OpenAI is a fake that records each request.

Run:  .venv\\Scripts\\python -m tests.test_fixes
"""

import json
import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from tests.harness import Checker, Conversation, setup_env  # noqa: E402
TMP = setup_env(WEATHER_LOCATION="", ALARM_GAP_S="0.3", TTS_PROVIDER="piper", PHONE_MODE="0")  # (independent of .env)

import tiktoken  # noqa: E402

from room_agent import runtime as rt  # noqa: E402
from room_agent.audio import speech_check as sc  # noqa: E402
from room_agent.llm import openai_backend  # noqa: E402
from room_agent.tools import timers, weather  # noqa: E402

REQUESTS = []  # kwargs of every request that would have gone to OpenAI





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

    def is_playing(self):
        return False


from tests.harness import Checker, Conversation  # noqa: E402

convo = Conversation(engine=Engine())
fake, REQUESTS, SPOKEN = convo.model, convo.requests, convo.spoken
from room_agent.conversation.turn import take_turn  # noqa: E402

t = Checker()
check, FAILS = t.check, t.fails


def turn(history, text):
    rt.turn_text = text
    return take_turn(history, text, final=True)


def system_sent(req):
    return "\n".join(m["content"] for m in req["messages"] if m["role"] == "system")


def tools_sent(req):
    return {t["function"]["name"] for t in req.get("tools") or []}


# ---------------------------------------------------------------- 1. timer state
print("1. timer rings -> 'I'm here' -> 'cancel it'")
history = [{"role": "user", "content": "set a 10 second countdown"},
           {"role": "assistant", "content": "Done, the ten second timer is counting down. Just say anything to stop it."}]
timers.set_timer(1, label="10 second countdown", message="Hey, your ten seconds are up!")
deadline = time.time() + 5
while not rt.ringing and time.time() < deadline:
    time.sleep(0.05)
check("the timer rings", bool(rt.ringing))
check("acknowledge_ring stops it", timers.acknowledge_ring())
while rt.ringing and time.time() < deadline:
    time.sleep(0.05)
check("it stopped ringing", rt.ringing is None)

REQUESTS.clear()
fake.scripts = [{"text": "Got it, timer's done."}]
turn(history, "I'm here")
sent = system_sent(REQUESTS[0])
check("model is told the timer is FINISHED", "FINISHED: not running, not counting down" in sent, sent[-600:])
check("model sees the live timer list: nothing running", "overrides anything said earlier" in sent
      and "no timers or alarms are set" in sent)

REQUESTS.clear()
fake.scripts = [{"tool": ("cancel_timer", json.dumps({"label": "it", "confidence": 0.95}))},
                {"text": "That one already finished, nothing to cancel."}]
turn(history, "cancel it")
check("'cancel it' still has the timer tools offered", "cancel_timer" in tools_sent(REQUESTS[0]), tools_sent(REQUESTS[0]))
results = [b["content"] for m in history if isinstance(m["content"], list) for b in m["content"]
           if isinstance(b, dict) and b.get("type") == "tool_result"]
check("cancel_timer says it already finished", any("already went off and is finished" in r for r in results), results)
check("Jarvis says it's already finished", any("already finished" in s for s in SPOKEN), SPOKEN[-3:])

# ---------------------------------------------------------------- 2. short answers
print("2. short answers are speech")
rt.tts_end = time.time() - 5  # the agent finished talking a while ago
sure, unsure = {"logprob": -0.25, "no_speech": 0.05}, {"logprob": -0.95, "no_speech": 0.7}
for text in ("Yes.", "Yeah.", "Okay.", "Mhm.", "No.", "Stop."):
    rt.stt_confidence = sure
    check(f"{text!r} (confident) -> USER", sc.classify_audio(text, time.time() - 1)[0] == sc.USER, sc.classify_audio(text))
rt.stt_confidence = unsure
check("'Yes.' that recognition doubted -> NOISE", sc.classify_audio("Yes.", time.time() - 1)[0] == sc.NOISE)
rt.stt_confidence = sure
for text in ("Thank you.", "you", "Um.", ""):
    check(f"{text!r} -> still NOISE", sc.classify_audio(text, time.time() - 1)[0] == sc.NOISE)
rt.stt_confidence = {"score": 0.93}
check("Deepgram 'yes' with confidence 0.93 -> USER", sc.classify_audio("yes", time.time() - 1)[0] == sc.USER)
rt.recent_speech.append("Yeah, sounds good.")
rt.tts_end = time.time()
rt.stt_confidence = sure
check("'yeah' overlapping the agent's own 'Yeah' -> echo, not you",
      sc.classify_audio("Yeah.", time.time() - 0.1)[0] == sc.AGENT_ECHO)
check("a barge-in still ignores a bare 'yeah' backchannel", sc.is_filler("Yeah."))
rt.recent_speech.clear()

# ---------------------------------------------------------------- 3. location
print("3. weather without a stored location")
from room_agent.prompt import runtime_context  # noqa: E402

rt.session_location = ""
out = weather.get_weather()
check("no city: asks once, says it can't detect location", out.startswith("UNAVAILABLE") and "don't guess" in out, out)
check("context says location is unknown and can't be detected", "their_location: unknown" in runtime_context("weather"))
check("with location detection off, the capabilities say so", "knowing where they are right now (turned off in the settings)"
      in runtime_context("weather"))
asked = []


def fake_get(url, params=None, timeout=None):
    if "geocoding" in url:
        asked.append(params["name"])
        return NS(json=lambda: {"results": [{"latitude": 41.9, "longitude": -87.6, "name": "Chicago"}]})
    raise ConnectionError("forecast not needed for this test")


real_get, weather.requests.get = weather.requests.get, fake_get
for loc in ("Chicago", ""):
    try:
        weather.get_weather(loc)
    except ConnectionError:
        pass
weather.requests.get = real_get
check("after they name Chicago, 'and tomorrow?' reuses it without asking", asked == ["Chicago", "Chicago"], asked)
check("context tells the model they said Chicago", "They said Chicago" in runtime_context("weather"))
rt.session_location = ""

# ---------------------------------------------------------------- 4. context size
print("4. input tokens per request")
enc = tiktoken.get_encoding("o200k_base")


def size(req):
    return (sum(len(enc.encode(m.get("content") or "")) + 4 for m in req["messages"])
            + len(enc.encode(json.dumps(req.get("tools") or []))))


rt.last_ring = None
REQUESTS.clear()
fake.scripts = [{"text": "Pretty good, you?"}]
turn([], "how's it going?")
simple = size(REQUESTS[0])
BEFORE = 5017  # measured the same way before this change (17 tools, full rules, full capability details)
print(f"     simple turn: {BEFORE} -> {simple} input tokens ({(1 - simple / BEFORE) * 100:.0f}% fewer)")
check("simple turn sends at least 30% fewer input tokens", simple <= BEFORE * 0.7, simple)
check("simple turn still has memory, weather, search, quiet", {"recall", "remember", "get_weather", "web_search",
                                                              "go_quiet"} <= tools_sent(REQUESTS[0]))
for text, tool in [("talk a bit slower", "set_speaking_rate"), ("good morning", "daily_briefing"),
                   ("wake me up at 7", "set_alarm"), ("use a different voice", "set_voice")]:
    REQUESTS.clear()
    turn([], text)
    check(f"{text!r} gets {tool}", tool in tools_sent(REQUESTS[0]))
REQUESTS.clear()
turn([], "wake me up at 7")
check("timer rules come with the timer tools", "A timer can be any length" in system_sent(REQUESTS[0]))
REQUESTS.clear()
turn([], "how's it going?")
check("...and are left out of plain chat", "A timer can be any length" not in system_sent(REQUESTS[0]))

long = "OK: " + "headline words " * 200
h = [{"role": "user", "content": "any news?"},
     {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "web_search", "input": {"query": "news"}}]},
     {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": long}]},
     {"role": "assistant", "content": "Big storm up north, mostly."},
     {"role": "user", "content": "what about the second story?"}]
msgs = openai_backend.to_openai(h)
old = next(m for m in msgs if m["role"] == "tool")
check("an earlier long tool result is shortened", len(old["content"]) <= openai_backend.OLD_RESULT_CHARS + 3)
check("the follow-up still has the conversation (5 messages kept)", len(msgs) == 5 and msgs[-1]["content"].startswith("what about"))
cur = openai_backend.to_openai(h[:3])
check("the current turn's tool result is kept whole", next(m for m in cur if m["role"] == "tool")["content"] == long)

# ---------------------------------------------------------------- 5. two timers due together
print("5. two timers due at the same moment")
rings, real_ring = [], timers._ring
timers._ring = lambda item: (rings.append(item["label"]), time.sleep(0.5))
timers.set_timer(1, label="first", message="First one!")
timers.set_timer(1, label="second", message="Second one!")
time.sleep(2.2)
timers._ring = real_ring
check("only one keeps ringing (no two loops talking over each other)", len(rings) == 1, rings)
check("the other is still announced once", any(s in ("First one!", "Second one!") and s not in rings for s in SPOKEN), SPOKEN[-4:])
ctx = runtime_context("wake me up in 10 seconds")
check("context: a request to set one is a NEW one, never 'already set'", "never say it's already set" in ctx)
rt.last_ring = {"label": "x", "message": "m", "kind": "timer", "at": time.time(), "stopped": True}
check("context: never offer to stop a timer that already stopped", "nothing left to stop or cancel, so never offer to"
      in runtime_context("okay I'm awake"))

print("\nALL FIX TESTS PASSED" if not FAILS else f"\nFAILED: {FAILS}")
sys.stdout.flush()
os._exit(1 if FAILS else 0)
