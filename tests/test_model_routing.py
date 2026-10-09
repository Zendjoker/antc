"""Model routing (llm/router.py), offline: which model each kind of turn gets, through the real turn pipeline (reflex,
cognition levels, policy, router, OpenAI call construction). The OpenAI model is scripted (tests/harness.FakeModel
records each request's model and thinking effort); Claude is stubbed; every capability that would change the PC or
the outside world is simulated (harness.simulate_actions), and the Zigbee hub is a fake with one LED strip.

    cheap path         reflex (no model call) or OPENAI_MODEL (gpt-5-mini)
    conversation path  OPENAI_CONVERSATION_MODEL (gpt-5)
    deep path          LLM_SMART (Claude)

Run:  .venv\\Scripts\\python -m tests.test_model_routing
"""

import json

from tests.harness import setup_env

setup_env()

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.llm import openai_backend, router  # noqa: E402
from room_agent.tools.zigbee import hub  # noqa: E402
from tests.harness import Checker, Conversation, simulate_actions  # noqa: E402

t = Checker()
B = "zigbee2mqtt"


class FakeZ2M:
    def publish(self, topic, payload):
        pass


hub.client = FakeZ2M()
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps([
    {"friendly_name": "Door sensor", "ieee_address": "0x1", "power_source": "Battery",
     "definition": {"model": "MCCGQ11LM", "vendor": "Aqara", "description": "Door and window sensor",
                    "exposes": [{"property": "contact"}]}},
    {"friendly_name": "LED strip", "ieee_address": "0x4", "power_source": "Mains (single phase)",
     "definition": {"model": "LGYCDD01LM", "vendor": "Aqara", "description": "LED Strip T1",
                    "exposes": [{"type": "light", "features": [{"name": "state", "property": "state"}]}]}}]).encode())
hub.handle(f"{B}/Door sensor", b'{"contact": true}')
hub.handle(f"{B}/LED strip", b'{"state": "ON"}')
simulate_actions()

CLAUDE = []


def fake_claude(history):
    CLAUDE.append(rt.turn_text)
    history.append({"role": "assistant", "content": "Okay."})


router.ask_claude = fake_claude
convo = Conversation()
CHEAP, TALK = config.OPENAI_MODEL, config.OPENAI_CONVERSATION_MODEL


def reset():
    """A fresh conversation: nothing pending, no question just asked, no conversation going on."""
    rt.pending, rt.last_reply, rt.conversation_turn = None, "", None


def route(text, history=None):
    """-> (path, model, reasoning effort) for one turn: 'reflex', 'openai' or 'claude'."""
    CLAUDE.clear()
    rt.turn.via = ""
    convo.say(text, history=[] if history is None else history)
    if CLAUDE:
        return "claude", config.MODEL, None
    if not convo.requests:
        return ("reflex" if getattr(rt.turn, "via", "") == "reflex" else "none"), None, None
    first = convo.requests[0]
    return "openai", first.get("model"), first.get("reasoning_effort")


print("\nConfiguration")
t.check("the cheap model is gpt-5-mini and the conversation model is gpt-5 (both OpenAI)", (CHEAP, TALK) ==
        ("gpt-5-mini", "gpt-5"), (CHEAP, TALK))
t.check("conversation thinking effort: minimal (OPENAI_CONVERSATION_REASONING)",
        config.OPENAI_CONVERSATION_REASONING == "minimal", config.OPENAI_CONVERSATION_REASONING)

print("\nScenarios")
CASES = [
    ("Turn off the lights", "cheap"),
    ("Stop the music", "cheap"),
    ("Is the door closed?", "cheap"),
    ("I think we could make money building websites", "talk"),
    ("Do you remember what we decided yesterday?", "talk"),
    ("I'm struggling with my business", "talk"),
    ("Actually, I changed my mind", "talk"),
    ("Can you please turn off the living room lights?", "cheap"),
    ("Research three businesses and create a detailed plan", "deep"),
    ("Why do I feel so stuck?", "talk"),
    ("Remember that my dentist appointment is on Friday", "cheap"),
]
for text, want in CASES:
    reset()
    path, model, effort = route(text)
    ok = {"cheap": path == "reflex" or (path == "openai" and model == CHEAP),
          "talk": path == "openai" and model == TALK and effort == config.OPENAI_CONVERSATION_REASONING,
          "deep": path == "claude"}[want]
    t.check(f"{text!r} -> {want}: {path} {model or ''} {effort or ''}".rstrip(), ok, (path, model, effort))

print("\nAnswers to Jarvis's questions keep the conversation going")
reset()
hist = []
route("I think we could make money building websites for restaurants", hist)
hist.append({"role": "assistant", "content": "That's worth exploring. Want to focus on Daly City?"})
rt.last_reply = "That's worth exploring. Want to focus on Daly City?"
path, model, effort = route("Yeah, Daly City", hist)
t.check("an answer to a question Jarvis asked in a conversation stays on the conversation model", path == "openai"
        and model == TALK, (path, model))
t.check("...with the whole conversation in the request (the idea is still in context)",
        any("websites for restaurants" in str(m.get("content")) for m in convo.requests[0]["messages"]))

reset()
hist = []
convo.say("set a timer", history=hist, calls=[("set_timer", {})], reply="For how long?")
rt.last_reply = "For how long?"
pending_tool = (rt.pending or {}).get("tool") if rt.pending else None
path, model, effort = route("10 minutes", hist)
tools = {x["function"]["name"] for x in convo.requests[0].get("tools") or []} if convo.requests else set()
t.check("an answer to a task question (a pending request) stays on the cheap model with the pending tool offered",
        pending_tool == "set_timer" and path == "openai" and model == CHEAP and "set_timer" in tools,
        (pending_tool, path, model, sorted(tools)[:8]))

print("\nAPI call arguments (installed SDK, no request sent)")
import inspect  # noqa: E402

import openai  # noqa: E402
from openai.resources.chat.completions import Completions  # noqa: E402

params = inspect.signature(Completions.create).parameters
t.check(f"openai {openai.__version__}: chat.completions.create takes reasoning_effort, max_completion_tokens, "
        "prompt_cache_key, stream_options, tools", all(p in params for p in (
            "reasoning_effort", "max_completion_tokens", "prompt_cache_key", "stream_options", "tools")))
try:
    from typing import get_args

    from openai.types import ChatModel, ReasoningEffort

    models = set(get_args(ChatModel))
    efforts = {x for arg in get_args(ReasoningEffort) for x in (get_args(arg) or (arg,)) if isinstance(x, str)}
except ImportError:
    models, efforts = set(), set()
t.check("the SDK knows both model IDs and the thinking effort", {CHEAP, TALK} <= models
        and config.OPENAI_CONVERSATION_REASONING in efforts and config.OPENAI_REASONING in efforts,
        (sorted(m for m in models if m.startswith("gpt-5"))[:12], efforts))
reset()
route("I think we could make money building websites")
first = convo.requests[0]
t.check("the conversation request carries exactly those arguments", first["model"] == TALK and first[
    "reasoning_effort"] == "minimal" and first.get("prompt_cache_key") == "jarvis" and first.get("stream") is True,
        {k: v for k, v in first.items() if k not in ("messages", "tools")})

print("\nTool areas come from their words, not from code's notes")
from room_agent.conversation.turn import _NEED_NOTE  # noqa: E402

for text in ("Turn off the music.", "Is the door closed?", "Close this tab.", "Put volume 50%."):
    plain = {x["name"] for x in openai_backend.relevant_tools(openai_backend.active_tools() if hasattr(
        openai_backend, "active_tools") else __import__("room_agent.actions.core", fromlist=["x"]).offered(),
        [{"role": "user", "content": text}])}
    from room_agent.actions import core  # noqa: E402

    noted = {x["name"] for x in openai_backend.relevant_tools(core.offered(), [{"role": "user", "content": text + _NEED_NOTE}])}
    t.check(f"{text!r}: the need note adds no tool area", plain == noted, sorted(noted - plain))
for note in (" (System note from code: if they're asking for calendar_create_event, call it now with only what they "
             "said; it collects the rest.)",):
    plain = {x["name"] for x in openai_backend.relevant_tools(core.offered(), [{"role": "user", "content": "Put it at 20%."}])}
    noted = {x["name"] for x in openai_backend.relevant_tools(core.offered(),
                                                             [{"role": "user", "content": "Put it at 20%." + note}])}
    t.check("a code note naming calendar_create_event doesn't offer the calendar tools", plain == noted,
            sorted(noted - plain))
t.check("the system's own check messages and the barge-in prefix aren't read as their words",
        openai_backend._own_words("(Automatic check from the system, not from Adam: send the email") == ""
        and openai_backend._own_words("(I cut you off mid-reply) reply to Sara") == "reply to Sara"
        and openai_backend._own_words("make it blue (answer now, don't wait for more)") == "make it blue")
t.done("MODEL ROUTING")
