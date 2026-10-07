"""Cost-reduction tests. No real API calls: Claude and OpenAI are replaced by fakes that count every call.

Run:  .venv\\Scripts\\python -m tests.test_cost
"""

import os
import queue
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update(
    OPENAI_API_KEY="sk-test-not-real", ANTHROPIC_API_KEY="sk-ant-test-not-real", LLM_PROVIDER="claude", LLM_DEFAULT="openai",
    OPENAI_MODEL="gpt-5-mini", MEMORY_DB=os.path.join(TMP, "m.db"), MEMORY_FILE=os.path.join(TMP, "x.json"),
    RECENT_FILE=os.path.join(TMP, "y.json"), REMINDERS_FILE=os.path.join(TMP, "r.json"), SPEND_FILE=os.path.join(TMP, "s.json"),
    SETTINGS_FILE=os.path.join(TMP, "set.json"), DAILY_BUDGET_USD="1.00", BUDGET_FALLBACK="stop", HA_URL="", HA_TOKEN="",
    TRACE="0", AUDIO_DEBUG="0")
sys.path.insert(0, ROOT)

from room_agent import runtime as rt  # noqa: E402
from room_agent.llm import client as claude_client_mod, openai_backend, router  # noqa: E402
from room_agent.llm.budget import budget  # noqa: E402

CALLS = []  # ("claude"|"openai", kwargs) for every request that would have cost money


# ---------------- fake Claude ----------------
class FakeClaudeStream:
    def __init__(self, script):
        self.script = script

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False

    @property
    def text_stream(self):
        return iter(self.script.get("text", []))

    def get_final_message(self):
        blocks = [NS(type="text", text="".join(self.script.get("text", [])))] if self.script.get("text") else []
        for i, (name, args) in enumerate(self.script.get("tools", [])):
            blocks.append(NS(type="tool_use", id=f"toolu_{i}", name=name, input=args))
        usage = NS(input_tokens=1200, cache_read_input_tokens=4700, cache_creation_input_tokens=0, output_tokens=40)
        return NS(content=blocks, stop_reason="tool_use" if self.script.get("tools") else "end_turn", usage=usage)

    current_message_snapshot = NS(usage=None)


class FakeClaude:
    def __init__(self):
        self.scripts = []
        self.messages = self

    def stream(self, **kw):
        CALLS.append(("claude", kw))
        return FakeClaudeStream(self.scripts.pop(0) if self.scripts else {"text": ["Okay."]})

    def create(self, **kw):  # memory calls
        CALLS.append(("claude", kw))
        return NS(content=[NS(type="tool_use", input={"profile": {}, "add_facts": [], "remove_fact_ids": []})],
                  usage=NS(input_tokens=1300, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=30))


# ---------------- fake OpenAI ----------------
def chunk(content=None, tool=None, usage=None):
    tcs = [NS(index=tool[0], id=tool[1], function=NS(name=tool[2], arguments=tool[3]))] if tool else None
    choices = [] if usage else [NS(delta=NS(content=content, tool_calls=tcs), finish_reason=None)]
    return NS(choices=choices, usage=usage)


class FakeOpenAIStream:
    def __init__(self, chunks):
        self._it = iter(chunks)

    def __iter__(self):
        return self._it

    def close(self):
        pass


class FakeOpenAI:
    def __init__(self):
        self.scripts = []
        self.fail_next = False
        self.chat = NS(completions=self)

    def create(self, **kw):
        CALLS.append(("openai", kw))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated OpenAI outage")
        usage = NS(prompt_tokens=6000, completion_tokens=60,
                   prompt_tokens_details=NS(cached_tokens=4800), completion_tokens_details=None)
        if not kw.get("stream"):  # memory calls
            return NS(choices=[NS(message=NS(content="SKIP", tool_calls=[NS(function=NS(
                arguments='{"profile": {}, "add_facts": [{"content": "Plays guitar", "category": "fact"}], "remove_fact_ids": []}'))]))],
                usage=usage)
        script = self.scripts.pop(0) if self.scripts else {"text": ["Sure."]}
        chunks = [chunk(content=t) for t in script.get("text", [])]
        for i, (name, args) in enumerate(script.get("tools", [])):
            chunks.append(chunk(tool=(i, f"call_{i}", name, args[:10])))
            chunks.append(chunk(tool=(i, None, None, args[10:])))  # arguments arrive in pieces
        chunks.append(chunk(usage=usage))
        return FakeOpenAIStream(chunks)


fake_claude, fake_openai = FakeClaude(), FakeOpenAI()
claude_client_mod._client = fake_claude
openai_backend._client = fake_openai

# speech goes into a list instead of the speaker
SPOKEN = []
rt.tts_enabled = True
rt.engine = None


def _drain():
    while True:
        item = rt.speak_q.get()
        if hasattr(item, "text") or isinstance(item, str):
            SPOKEN.append(str(getattr(item, "text", item)))
        rt.speak_q.task_done()


threading.Thread(target=_drain, daemon=True).start()

from room_agent.audio import fillers  # noqa: E402

fillers.filler = lambda *a, **k: None  # (stock clips need TTS; not what's tested here)
import room_agent.llm.loop as loop_mod  # noqa: E402

loop_mod.filler = lambda *a, **k: None
from room_agent.conversation.turn import take_turn  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"   ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def reset():
    CALLS.clear()
    SPOKEN.clear()
    fake_claude.scripts.clear()
    fake_openai.scripts.clear()


def turn(history, text):
    rt.turn_text = text
    return take_turn(history, text, final=True)


print("routing (decided in code, no model call):")
for text, want in [("what time is it", "openai"), ("set a timer for 5 minutes", "openai"),
                   ("ask Claude what to name my dog", "claude"), ("think hard about this: should I quit my job", "claude"),
                   ("help me plan a trip to Japan", "claude"), ("what's the weather", "openai"),
                   (" ".join(["word"] * 40), "claude")]:
    check(f"{text[:40]!r} -> {want}", router.choose(text)[0] == want, router.choose(text))

print("\nan everyday turn goes to the cheap model only:")
reset()
h = []
fake_openai.scripts.append({"text": ["It's ", "seven ", "thirty."]})
turn(h, "what time is it?")
check("one OpenAI call, no Claude call", [c[0] for c in CALLS] == ["openai"], [c[0] for c in CALLS])
check("reply spoken", SPOKEN == ["It's seven thirty."], SPOKEN)
kw = CALLS[0][1]
HEADER = "RUNTIME CONTEXT (generated by code"
check("fixed rules sent first, changing context second", kw["messages"][0]["role"] == "system"
      and HEADER not in kw["messages"][0]["content"] and HEADER in kw["messages"][1]["content"])
check("reply length capped", kw["max_completion_tokens"] <= 3 * 450, kw["max_completion_tokens"])

print("\n'ask Claude' goes to Claude, with the fixed part cached:")
reset()
fake_claude.scripts.append({"text": ["Name ", "it ", "Biscuit."]})
turn(h, "ask Claude what I should name my dog")
check("one Claude call, no OpenAI call", [c[0] for c in CALLS] == ["claude"], [c[0] for c in CALLS])
system = CALLS[0][1]["system"]
check("cache marker on the fixed block, context after it",
      isinstance(system, list) and system[0].get("cache_control") == {"type": "ephemeral"}
      and HEADER in system[1]["text"] and HEADER not in system[0]["text"] and "cache_control" not in system[1])
check("Claude reply capped", CALLS[0][1]["max_tokens"] <= 450)

print("\nOpenAI failing falls back to Claude (no repeated speech):")
reset()
fake_openai.fail_next = True
fake_claude.scripts.append({"text": ["Still here."]})
turn(h, "what's up")
check("OpenAI tried, Claude answered", [c[0] for c in CALLS] == ["openai", "claude"], [c[0] for c in CALLS])
check("spoken once", SPOKEN == ["Still here."], SPOKEN)

print("\nclaim guard works with the cheap model:")
reset()
fake_openai.scripts += [{"text": ["Got it, I've saved that."]},  # claims a save without doing it
                        {"tools": [("remember", '{"content": "Learning guitar since October 2026"}')]},
                        {"text": ["Saved."]}]
turn(h, "remember that I'm learning guitar")
check("unverified claim never spoken", not any("saved that" in s for s in SPOKEN), SPOKEN)
check("real save, then confirmation spoken", "Saved." in SPOKEN and any("guitar" in f["content"].lower() for f in rt.memory.facts()),
      (SPOKEN, [f["content"] for f in rt.memory.facts()]))
check("tool call stored in history in a format both models read", any(
    isinstance(m["content"], list) and any(isinstance(b, dict) and b.get("type") == "tool_use" for b in m["content"]) for m in h))
from room_agent.llm.openai_backend import to_openai  # noqa: E402

msgs = to_openai(h)
ids = [tc["id"] for m in msgs if m.get("tool_calls") for tc in m["tool_calls"]]
check("tool calls and results stay paired when converted", ids and all(
    any(m.get("role") == "tool" and m.get("tool_call_id") == i for m in msgs) for i in ids))

print("\nquiet mode and noise make zero calls:")
from room_agent.audio.speech_check import NOISE, classify_audio  # noqa: E402
from room_agent.text import is_quiet_command  # noqa: E402

reset()
check("'stay quiet until I call you' handled in code", is_quiet_command("stay quiet until I call you"))
check("Whisper's 'Thank you.' from silence is noise", classify_audio("Thank you.", None)[0] == NOISE)
import builtins  # noqa: E402

from room_agent.conversation import loops  # noqa: E402

lines = iter(["stay quiet until I call you", "what's 5 times 10", "can you hear me", "quit"])
builtins_input = builtins.input
builtins.input = lambda *a: next(lines)
loops.text_loop()
builtins.input = builtins_input
check("quiet mode in text mode: zero API calls", CALLS == [], [c[0] for c in CALLS])

print("\nmemory: no calls for small talk, one call per conversation:")
from room_agent.memory.writer import MemoryWriter, worth_learning  # noqa: E402

for text, asked, want in [("what's the weather", "", False), ("tell me a joke", "", False), ("thanks", "", False),
                          ("what's my name?", "", False), ("my sister is Sara", "", True), ("I moved to Oakland", "", True),
                          ("Chicago", "What city are you in?", True), ("call me Adam", "", True)]:
    check(f"worth learning {text!r}: {want}", worth_learning(text, asked) == want)
reset()
calls = []
w = MemoryWriter(rt.memory, "Adam", lambda s, p, t: calls.append(p) or None, lambda s, p: calls.append(p) or "SKIP", defer=True)
for u in ["my sister is Sara", "what's the weather", "I started guitar two weeks ago", "thanks"]:
    w.observe(u, "ok", asked="")
check("no memory calls during the conversation", calls == [])
w.conversation_ended(0)
w.flush(5)
check("one extraction call + one summary for the whole conversation", len(calls) == 2, len(calls))
check("both personal facts in the single extraction", "Sara" in calls[0] and "guitar" in calls[0])
calls.clear()
w.observe("hi", "hey", asked="")
w.conversation_ended(w.mark() - 1)
w.flush(5)
check("trivial conversation: no extraction, no summary", calls == [], calls)

print("\nhistory is kept short:")
from room_agent.config import MAX_HISTORY  # noqa: E402
from room_agent.conversation.history import trim  # noqa: E402

long = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(60)]
trim(long)
check(f"trimmed to {MAX_HISTORY} messages, starting with you", len(long) <= MAX_HISTORY and long[0]["role"] == "user", len(long))

print("\ndaily budget:")
reset()
budget.limit = 0.01
budget.record("openai", "gpt-5-mini", fresh_in=50_000)  # pretend today's spend crossed the limit
check("budget exceeded", budget.exceeded(), budget.total())
turn(h, "what's the weather")
check("over budget: zero API calls", CALLS == [], [c[0] for c in CALLS])
check("over budget: says so truthfully", any("spending limit" in s for s in SPOKEN), SPOKEN)
from room_agent.llm.memory_calls import memory_call_tool  # noqa: E402

check("over budget: memory calls skipped", memory_call_tool("s", "p", {"name": "x", "description": "", "input_schema": {}}) is None
      and CALLS == [])
budget.limit = 1.0

print("\nper-turn cost line:")
budget.start_turn()
budget.record("openai", "gpt-5-mini", fresh_in=1200, cached_in=4800, out=60)
print("   ", budget.turn_line())
check("cost line shows the turn and today", budget.turn_line().startswith("turn cost:"))

print("\n" + ("ALL COST TESTS PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
shutil.rmtree(TMP, ignore_errors=True)
sys.stdout.flush()
os._exit(1 if FAILS else 0)
