"""Step 1: measure real input-token costs with the free count_tokens endpoint. Makes NO billed API calls.

Run: .venv\\Scripts\\python tests\\measure_cost.py
"""

import json

from room_agent import runtime as rt
from room_agent.config import MODEL
from room_agent.llm.client import client
from room_agent.memory.writer import EXTRACT_SYSTEM, SUMMARY_SYSTEM, UPDATE_TOOL
from room_agent.prompt import system_prompt
from room_agent.tools.registry import active_tools

PRICES = {  # $ per million tokens, Claude Haiku 4.5
    "base_input": 1.0,
    "cache_write_5m": 1.25,
    "cache_read": 0.10,
    "output": 5.0,
}


def count(**kw):
    kw.setdefault("model", MODEL)
    r = client().messages.count_tokens(**kw)
    return r.input_tokens


def fake_history(n_pairs=8):
    """A representative conversation: n_pairs of user/assistant turns, plus one tool round-trip."""
    h = []
    for i in range(n_pairs):
        h.append({"role": "user", "content": f"hey, what's the weather like today, question number {i}?"})
        h.append({"role": "assistant", "content": "it's sunny and about 68 degrees, should be a nice one."})
    h.append({"role": "user", "content": "set a timer for 10 minutes for the pasta"})
    h.append({"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "set_timer",
         "input": {"seconds": 600, "label": "pasta", "message": "hey, your pasta's ready!"}},
    ]})
    h.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "OK: timer 'pasta' set for 10 minutes."},
    ]})
    return h


def main():
    rt.turn_text = "set a timer for 10 minutes for the pasta"
    rt.must_answer = True
    system = system_prompt()
    tools = active_tools()
    history = fake_history()

    tools_only = count(tools=tools, messages=[{"role": "user", "content": "hi"}])
    system_and_tools = count(system=system, tools=tools, messages=[{"role": "user", "content": "hi"}])
    full_request = count(system=system, tools=tools, messages=history)
    history_only = count(messages=history)

    system_static_len = len(system)
    print("=== Claude Haiku 4.5 cost measurement (count_tokens, free, no completions billed) ===")
    print(f"system prompt chars: {system_static_len}")
    print(f"tool definitions: {len(tools)} tools")
    print()
    print(f"{'component':35s} {'tokens':>10s}")
    print(f"{'tools only (+1 msg overhead)':35s} {tools_only:10d}")
    print(f"{'system + tools (+1 msg overhead)':35s} {system_and_tools:10d}")
    print(f"{'history only, no system/tools':35s} {history_only:10d}")
    print(f"{'full request (system+tools+history)':35s} {full_request:10d}")
    print()

    # requests per turn: 1 normal, 2 if claim-guard correction fires, 2+ per tool-use round trip
    cost_per_mtok_in = PRICES["base_input"]
    cost_per_mtok_out = PRICES["output"]
    turn_cost_no_cache = full_request / 1e6 * cost_per_mtok_in + 150 / 1e6 * cost_per_mtok_out
    print(f"approx cost per turn (no caching, 1 request, ~150 output tokens): ${turn_cost_no_cache:.5f}")
    print(f"approx cost per 100 exchanges/day (no caching): ${turn_cost_no_cache * 100:.3f}/day")
    print()
    print("Haiku 4.5 minimum cacheable prefix: 4096 tokens.")
    print(f"system+tools alone is {system_and_tools} tokens -> {'MEETS' if system_and_tools >= 4096 else 'BELOW'} the cache minimum.")

    print()
    print("=== Memory writer calls (separate from the conversation; one extract call per turn) ===")
    extract_prompt = (f"Today is Monday January 1, 2026.\nProfile: (empty)\nRemembered facts:\n(none)\n\n"
                       f"Latest exchange:\nAssistant (just before): (nothing)\n"
                       f"Adam: what's the weather like today?\nAssistant: it's sunny and about 68 degrees.")
    extract_tokens = count(system=EXTRACT_SYSTEM.format(user="Adam"), tools=[UPDATE_TOOL],
                           tool_choice={"type": "tool", "name": UPDATE_TOOL["name"]},
                           messages=[{"role": "user", "content": extract_prompt}])
    summary_prompt = "Adam: what's the weather like today?\nAssistant: it's sunny and about 68 degrees."
    summary_tokens = count(system=SUMMARY_SYSTEM.format(user="Adam"),
                           messages=[{"role": "user", "content": summary_prompt}])
    print(f"{'memory extract call (per turn, unless small talk)':45s} {extract_tokens:10d} tokens  (max_tokens=1024 out cap)")
    print(f"{'conversation summary call (once per conversation)':45s} {summary_tokens:10d} tokens  (max_tokens=300 out cap)")
    extract_cost = extract_tokens / 1e6 * cost_per_mtok_in + 60 / 1e6 * cost_per_mtok_out
    print(f"approx cost per memory-extract call: ${extract_cost:.5f} (this fires on almost every turn today)")


if __name__ == "__main__":
    main()
