"""What every test needs, in one place: a temp environment, a scripted model instead of a paid one, captured speech,
a way to run one conversation turn, and pass/fail bookkeeping.

    from tests.harness import setup_env
    TMP = setup_env(APPS_CACHE="...")          # before importing room_agent: env vars are read at import
    from tests.harness import Checker, Conversation
    t = Checker()
    convo = Conversation()                      # installs the scripted model, captures speech
    results, system, tools = convo.say("open spotify", [("open_app", {"app_name": "Spotify"})], "Spotify's up.")
    t.check("opened", results[0].startswith("OK"))
    t.done()                                    # prints the summary and exits 0 / 1

Nothing here costs money or touches real services: the model, Google etc. are fakes; the PC is only touched by the
tests whose names end in _live.
"""

import json
import os
import sys
import tempfile
import threading
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


INTERNAL_GROUPS = ("timers", "memory", "quiet", "voice", "learning", "undo")  # Jarvis's own state (temp files in tests)


def simulate_actions(keep=(), keep_groups=()):
    """For scripts that run the REAL model: every capability that changes the machine or the outside world (apps,
    windows, volume and media, smart home, calls, email, calendar, and any new group by default) reports success
    without running, so whatever the model decides, nothing on this PC or in your accounts changes. Reads stay real;
    Jarvis's own state (timers, memory, settings: temp files in tests) stays real. Left alone: capabilities the script
    simulates itself, `keep` (tests/sim_pc.SIMULATED) and `keep_groups` (backed by tests/fake_google.py).
    -> the names that were stubbed."""
    from room_agent.actions import core

    core.ensure_loaded()
    stubbed = []
    for cap in core.REGISTRY.values():
        if (not cap.changes_state or cap.group in INTERNAL_GROUPS or cap.name in keep or cap.group in keep_groups
                or not getattr(cap.execute, "__module__", "").startswith("room_agent")):
            continue
        cap.execute = lambda args, _n=cap.name: f"OK: {_n} done."
        cap.observe = cap.verify = cap.expect = cap.undo = None
        cap.skip_if_satisfied = False
        stubbed.append(cap.name)
    return stubbed


def setup_env(**extra):
    """Temp files for everything that persists, fake keys, no tracing. Call before importing room_agent."""
    tmp = tempfile.mkdtemp()
    env = dict(
        OPENAI_API_KEY="sk-test-not-real", ANTHROPIC_API_KEY="sk-ant-test-not-real", LLM_PROVIDER="claude",
        LLM_DEFAULT="openai", OPENAI_MODEL="gpt-5-mini", MEMORY_DB=os.path.join(tmp, "m.db"),
        MEMORY_FILE=os.path.join(tmp, "x.json"), RECENT_FILE=os.path.join(tmp, "y.json"),
        REMINDERS_FILE=os.path.join(tmp, "r.json"), SPEND_FILE=os.path.join(tmp, "s.json"),
        SETTINGS_FILE=os.path.join(tmp, "set.json"), LEARNING_DB=os.path.join(tmp, "learning.db"),
        CONNECTIONS_FILE=os.path.join(tmp, "connections.json"), APPS_CACHE=os.path.join(tmp, "apps.json"),
        EXPERIENCE_DB=os.path.join(tmp, "experience.db"),
        HA_URL="", HA_TOKEN="", TRACE="0", AUDIO_DEBUG="0", PYTHONIOENCODING="utf-8",
        PHONE_TUNNEL="", ZIGBEE="0",  # (never a real public tunnel or real Zigbee devices from a test, whatever .env says)
        SOCIAL_MEANING="0")  # (the meaning reader loads in the background: a test that wants it opts in and waits for it)
    env.update({k: str(v) for k, v in extra.items()})
    os.environ.update(env)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    return tmp


class Checker:
    def __init__(self):
        self.fails = []
        self.passed = 0

    def check(self, name, ok, detail=""):
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"   ({str(detail)[:300]})" if detail and not ok else ""))
        if ok:
            self.passed += 1
        else:
            self.fails.append(name)
        return ok

    def done(self, title="TESTS"):
        print(f"\nALL {title} PASSED" if not self.fails else f"\nFAILED: {self.fails}")
        sys.stdout.flush()
        os._exit(1 if self.fails else 0)


# ---------------------------------------------------------------- a scripted model (OpenAI-shaped, no cost)
def _chunk(content=None, call=None, usage=None):
    tcs = [NS(index=call[0], id=f"call_{call[0]}", function=NS(name=call[1], arguments=call[2]))] if call else None
    return NS(choices=[] if usage else [NS(delta=NS(content=content, tool_calls=tcs), finish_reason=None)], usage=usage)


class FakeModel:
    """Plays back `scripts`, one per model request: {"text": "..."} and/or {"tools": [(name, args), ...]}
    ({"tool": (name, args)} also works). Every request is kept in `requests` (to check what the model was shown)."""

    def __init__(self):
        self.scripts, self.requests = [], []
        self.chat = NS(completions=self)

    def create(self, **kw):
        self.requests.append(kw)
        usage = NS(prompt_tokens=3000, completion_tokens=30, prompt_tokens_details=NS(cached_tokens=0))
        if not kw.get("stream"):  # (background memory calls)
            return NS(choices=[NS(message=NS(content="SKIP", tool_calls=[NS(function=NS(
                arguments='{"profile": {}, "add_facts": [], "remove_fact_ids": []}'))]))], usage=usage)
        s = self.scripts.pop(0) if self.scripts else {"text": "Okay."}
        chunks = [_chunk(content=s["text"])] if s.get("text") else []
        calls = list(s.get("tools", [])) + ([s["tool"]] if s.get("tool") else [])
        for i, (name, args) in enumerate(calls):
            chunks.append(_chunk(call=(i, name, args if isinstance(args, str) else json.dumps(args))))
        return iter(chunks + [_chunk(usage=usage)])


class Conversation:
    """Installs the scripted model, captures what Jarvis says, and runs turns through the real conversation code."""

    def __init__(self, engine=None):
        from room_agent import runtime as rt
        from room_agent.audio import fillers
        from room_agent.llm import openai_backend
        import room_agent.llm.loop as loop_mod

        self.rt = rt
        self.model = FakeModel()
        openai_backend._client = self.model
        rt.engine, rt.tts_enabled = engine, True
        self.spoken = []
        threading.Thread(target=self._drain, daemon=True).start()
        fillers.filler = loop_mod.filler = lambda *a, **k: None  # (stock clips need TTS)
        self.history = []

    @property
    def requests(self):
        return self.model.requests

    def _drain(self):
        while True:
            item = self.rt.speak_q.get()
            if hasattr(item, "text") or isinstance(item, str):
                self.spoken.append(str(getattr(item, "text", item)))
            self.rt.speak_q.task_done()

    def said(self):
        """What was spoken (without internal markers like the thinking loop)."""
        return [s for s in self.spoken if not s.startswith("<")]

    def say(self, text, calls=None, reply="Okay.", scripts=None, history=None, final=True):
        """One user turn. The model asks for `calls` (all in one reply), then says `reply` (or plays `scripts`).
        -> (tool results of this turn, the system prompt it was shown, the tool names it was offered)"""
        from room_agent.conversation.turn import take_turn

        history = self.history if history is None else history
        self.model.requests.clear()
        self.spoken.clear()
        self.model.scripts = list(scripts) if scripts is not None else (([{"tools": calls}] if calls else [])
                                                                         + [{"text": reply}])
        seen = {id(m) for m in history}  # (history gets trimmed: new messages are found by identity)
        take_turn(history, text, final=final)
        results = [b["content"] for m in history if id(m) not in seen and isinstance(m["content"], list)
                   for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
        first = self.model.requests[0] if self.model.requests else {}
        system = "\n".join(m["content"] for m in first.get("messages", []) if m["role"] == "system")
        tools = {t["function"]["name"] for t in first.get("tools") or []}
        return results, system, tools
