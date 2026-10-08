"""Input tokens per model call for typical requests (scripted model: free, no PC actions). Measurement script, not a
test suite.   Run:  .venv\\Scripts\\python -m tests.token_measure
"""

import json

from tests.harness import setup_env

setup_env()

import tiktoken  # noqa: E402

from room_agent import runtime as rt  # noqa: E402
from tests.harness import Conversation  # noqa: E402

enc = tiktoken.get_encoding("o200k_base")


def size(req):
    msgs = sum(len(enc.encode(m.get("content") or "")) + 4 for m in req["messages"] if isinstance(m.get("content"), str))
    tools = len(enc.encode(json.dumps(req.get("tools") or [])))
    system = sum(len(enc.encode(m["content"])) for m in req["messages"] if m["role"] == "system")
    return msgs + tools, system, tools, len(req.get("tools") or [])


CASES = [
    ("chat", "how's it going?", dict(reply="Pretty good, you?")),
    ("time", "what time is it?", dict(reply="It's just after eleven.")),
    ("timer (tool)", "set a timer for 5 minutes", dict(calls=[("set_timer", {"seconds": 300})], reply="Five minutes, starting now.")),
    ("can you call me", "can you call my phone?", dict(reply="I can't call you on request, sorry.")),
    ("session: thanks", "ok thanks", dict(reply="Anytime.")),
    ("session: time", "what time is it", dict(reply="It's 11:20.")),
]


# a conversation like a real evening: earlier replies talked about volume, timers, the phone, apps and the door
SESSION = [
    {"role": "user", "content": "can you turn the music down a bit"},
    {"role": "assistant", "content": "Done, it's at 30 now. Want me to pause it or skip to the next song instead?"},
    {"role": "user", "content": "no it's fine. set an alarm for 7"},
    {"role": "assistant", "content": "Alarm's set for 7 AM. I can also set a timer or remind you about your meeting."},
    {"role": "user", "content": "can you call me when it goes off"},
    {"role": "assistant", "content": "I can't call your phone on request; the alarm rings here in the room. I could open "
                                     "Spotify, change the volume, check if the door is open, or set more timers instead."},
]


def measure():
    rows = []
    for name, text, kw in CASES:
        convo = Conversation()
        rt.last_ring = None
        convo.say(text, history=[dict(m) for m in SESSION] if name.startswith("session") else None, **kw)
        sizes = [size(r) for r in convo.requests]
        rows.append((name, len(sizes), sizes))
    return rows


if __name__ == "__main__":
    for name, calls, sizes in measure():
        print(f"{name:18} model calls: {calls}   " + "   ".join(f"[total {t}, system {s}, tools {tt} ({n} tools)]" for t, s, tt, n in sizes), flush=True)
    import os

    os._exit(0)
