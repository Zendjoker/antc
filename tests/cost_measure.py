"""Token and cost measurement of everything the agent sends to Claude, using the FREE count_tokens endpoint only:
python tests/cost_measure.py   (no completions are requested, nothing is billed)"""
import json
import os
import pathlib
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.conversation.history import start_history  # noqa: E402
from room_agent.llm.client import client  # noqa: E402
from room_agent.memory.writer import EXTRACT_SYSTEM, SUMMARY_SYSTEM, UPDATE_TOOL  # noqa: E402
from room_agent.prompt import fixed_prompt, runtime_context, system_prompt  # noqa: E402
SYSTEM = fixed_prompt()
from room_agent.tools.registry import active_tools  # noqa: E402

PRICE_IN, PRICE_OUT, PRICE_WRITE, PRICE_READ = 1.00, 5.00, 1.25, 0.10  # $ per million tokens, Claude Haiku 4.5
MODEL = config.MODEL
rt.tts_enabled = True
USER_TEXT = "What's the weather like today?"
rt.turn_text = USER_TEXT
rt.must_answer = False


def count(system=None, tools=None, messages=None):
    kw = {"model": MODEL, "messages": messages or [{"role": "user", "content": "hi"}]}
    if system:
        kw["system"] = system
    if tools:
        kw["tools"] = tools
    return client().messages.count_tokens(**kw).input_tokens


def cents(tokens_in, tokens_out=0):
    return (tokens_in * PRICE_IN + tokens_out * PRICE_OUT) / 1e6 * 100


tools = active_tools()
base = count()
t_tools = count(tools=tools) - base
t_system = count(system=SYSTEM) - base
ctx = runtime_context(USER_TEXT)
t_ctx = count(system=ctx) - base
t_full_system = count(system=system_prompt()) - base
print(f"model {MODEL}; {len(tools)} tools offered; {len(rt.memory.load_recent(config.RECENT_HOURS))} messages restored from the last {config.RECENT_HOURS:g} h")
print("\nWHAT GETS SENT WITH EVERY MAIN REQUEST")
print(f"  tool definitions ({len(tools)})           {t_tools:6d} tokens")
print(f"  SYSTEM prompt (static text)        {t_system:6d} tokens")
print(f"  runtime context (changes each request) {t_ctx:6d} tokens")
fixed = count(system=system_prompt(), tools=tools) - base
print(f"  tools + system + context together  {fixed:6d} tokens  = {cents(fixed):.2f} cents per request before any history")
print(f"  static prefix that could be cached (tools + SYSTEM) = {count(system=SYSTEM, tools=tools) - base} tokens (Haiku 4.5 minimum to cache: 4096)")

hist_real = start_history()
print(f"\nrestored history: {len(hist_real)} messages")
sample = []
for i in range(20):
    sample += [{"role": "user", "content": ["What's the weather like in Chicago today?", "Set a timer for ten minutes.", "Thanks, that's great.",
                                             "Can you tell me a joke?", "What time is it in Tokyo right now?"][i % 5]},
               {"role": "assistant", "content": ["It's about sixty and cloudy there, with a bit of wind this afternoon.", "Done, ten minutes starting now. Say anything to stop it when it rings.",
                                                  "Anytime! Anything else on your mind?", "Why did the scarecrow win an award? He was outstanding in his field.",
                                                  "It's just past noon there tomorrow, so about thirteen hours ahead of you."][i % 5]}]


def history_tokens(n):
    h = (hist_real if len(hist_real) >= n else sample)[-n:]
    while h and h[0]["role"] != "user":
        h = h[1:]
    h = h + [{"role": "user", "content": USER_TEXT}]
    return count(messages=h) - base - 3, len(h)


print("\nHISTORY (typical short voice exchanges)")
hist_rows = {}
for n in (4, 16, 40):
    t, k = history_tokens(n)
    hist_rows[n] = t
    print(f"  last {n:2d} messages: {t:5d} tokens ({cents(t):.2f} cents)")
real_full = count(system=system_prompt(), tools=tools, messages=([m for m in hist_real[-config.MAX_HISTORY:]] or []) + [{"role": "user", "content": USER_TEXT}])
print(f"  the actual first request now (restored history, {min(len(hist_real), config.MAX_HISTORY)} msgs + question): {real_full} tokens = {cents(real_full):.2f} cents")

# one tool round
tool_msgs = [{"role": "user", "content": USER_TEXT},
             {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_01A", "name": "get_weather", "input": {"location": ""}}]},
             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01A",
                                           "content": "OK: Chicago: 61F cloudy, wind 12 mph. Today high 66, low 52, showers late. Tomorrow high 63, partly sunny."}]}]
second = count(system=system_prompt(), tools=tools, messages=tool_msgs)
first = count(system=system_prompt(), tools=tools, messages=[{"role": "user", "content": USER_TEXT}])
print(f"\nA TOOL TURN (e.g. weather): first request {first} tokens, second request (after the tool result) {second} tokens")

# memory extraction
snap = rt.memory.snapshot()
known = "\n".join(f"[{f['id']}] {f['fact']}" for f in snap["facts"]) or "(none)"
profile = json.dumps(snap["profile"], ensure_ascii=False) if snap["profile"] else "(empty)"
prompt = (f"Today is Tuesday October 06, 2026.\nProfile: {profile}\nRemembered facts:\n{known}\n\n"
          f"Latest exchange:\nAssistant (just before): (nothing)\n{config.USER_NAME}: {USER_TEXT}\nAssistant: It's 61 and cloudy in Chicago, showers later.")
extract = count(system=EXTRACT_SYSTEM.format(user=config.USER_NAME), tools=[UPDATE_TOOL], messages=[{"role": "user", "content": prompt}])
print(f"MEMORY EXTRACTION (one per non-trivial turn): {extract} tokens in ({len(snap['facts'])} facts + {len(snap['profile'])} profile keys stored)")
convo = "\n".join(f"{config.USER_NAME}: {sample[i]['content']}\nAssistant: {sample[i + 1]['content']}" for i in range(0, 20, 2))
summ = count(system=SUMMARY_SYSTEM.format(user=config.USER_NAME), messages=[{"role": "user", "content": convo}])
print(f"CONVERSATION SUMMARY (one per conversation, 10 exchanges): {summ} tokens in")

OUT_CHAT, OUT_TOOL_CALL, OUT_TOOL_ANSWER, OUT_EXTRACT, OUT_SUMMARY = 45, 40, 45, 50, 60
chat = cents(first, OUT_CHAT)
tool_turn = cents(first, OUT_TOOL_CALL) + cents(second, OUT_TOOL_ANSWER)
ext = cents(extract, OUT_EXTRACT)
print("\nPER USER TURN AT HAIKU 4.5 PRICES ($1 / $5 per million tokens in / out), history of a fresh conversation")
print(f"  plain chat turn   1 request  {chat:.2f} c  + extraction {ext:.2f} c = {chat + ext:.2f} c")
print(f"  tool turn         2 requests {tool_turn:.2f} c  + extraction {ext:.2f} c = {tool_turn + ext:.2f} c")
full_chat = cents(real_full, OUT_CHAT)
print(f"  plain chat turn with today's restored history ({min(len(hist_real), config.MAX_HISTORY)} msgs): {full_chat:.2f} c + extraction {ext:.2f} c = {full_chat + ext:.2f} c")
day = 100 * (0.6 * (full_chat + ext) + 0.35 * (tool_turn + (full_chat - chat) * 2 + ext) + 0.05 * full_chat)
print(f"  100 exchanges a day (60% chat, 35% tool, 5% trivial): about ${day / 100:.2f}")
json.dump({"tools": t_tools, "system": t_system, "ctx": t_ctx, "fixed": fixed, "hist": hist_rows, "first": first, "second": second,
           "extract": extract, "summary": summ, "real_full": real_full, "day_cents": day}, open(ROOT / "debug" / "cost_before.json", "w"))
