"""Latency safeguards, offline: speech starts at natural boundaries (never token fragments, a long first sentence at a
clause), the stable part of the prompt stays first (so the provider can reuse it), simple commands need no model call,
and a verified action needs only one. Timing itself is measured by tests/latency_profile.py, not asserted here.

Run:  .venv\\Scripts\\python -m tests.test_latency
"""

from tests.harness import setup_env

setup_env()

from room_agent import prompt  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core  # noqa: E402
from room_agent.speech import chunking  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

t = Checker()
core.ensure_loaded()


def stream(text, step=5):
    buf, chunks, first_at = "", [], None
    for i in range(0, len(text), step):
        buf += text[i:i + step]
        ready, buf = chunking.ready(buf, first=not chunks)
        if ready and first_at is None:
            first_at = i + step
        chunks += ready
    return chunks + ([buf.strip()] if buf.strip() else []), first_at


print("Speech boundaries:")
reply = ("So the build failed because the test runner couldn't find the config file, which usually happens when you start "
         "it from the wrong folder. Try the project root. Dr. Smith said 3.5 is fine... honestly.")
chunks, first_at = stream(reply)
t.check("a long first sentence starts at its first clause (sooner audio)", chunks[0].endswith("config file,") and first_at < 90,
        (chunks[0], first_at))
t.check("never token fragments (every chunk is a phrase)", all(len(c.split()) >= 3 for c in chunks), chunks)
t.check("abbreviations, decimals and a mid-sentence '...' aren't cut", chunks[-1] == "Dr. Smith said 3.5 is fine... honestly.", chunks)
t.check("nothing lost or duplicated", " ".join(chunks) == reply)
short, _ = stream("Yeah. It's on now.")
t.check("short replies still go sentence by sentence", short == ["Yeah.", "It's on now."], short)

print("Prompt stability:")
from room_agent.llm.openai_backend import OpenAICall  # noqa: E402
from room_agent.tools.registry import active_tools  # noqa: E402

heads = []
for text in ("how's it going?", "set a timer for 5 minutes", "turn the volume down"):
    rt.new_turn(text)
    heads.append(OpenAICall([{"role": "user", "content": text}], *prompt.system_parts(), active_tools()).messages[0]["content"])
common = 0
while common < min(map(len, heads)) and len({h[common] for h in heads}) == 1:
    common += 1
t.check("persona, conversation and truth rules open every prompt identically (cacheable), whatever the tools",
        heads[0][:common].rstrip().endswith(prompt.TRUTH.rstrip()[-80:]) or prompt.TRUTH in heads[0][:common], common)

print("Model calls:")
convo = Conversation()
convo.say("set a timer for 4 minutes", calls=[("set_timer", {"seconds": 240})], reply="SHOULD NOT BE NEEDED")
t.check("a verified simple action: one model call, spoken from the verified result", len(convo.requests) == 1
        and convo.said() and "4 minutes" in convo.said()[-1], (len(convo.requests), convo.said()))
convo.say("set a timer for 2 minutes and tell me a joke", calls=[("set_timer", {"seconds": 120})], reply="Done. Why did the...")
t.check("a request with more in it still gets the second call", len(convo.requests) == 2, len(convo.requests))
t.done("LATENCY")
