"""Multi-turn collection with the REAL model (whatever .env's Ollama model is), against a SIMULATED Google: nothing reaches
a real mailbox or calendar. Costs a few model calls.

    .venv\\Scripts\\python -m tests.pending_live
"""

import json
import logging
import os
import sys
from types import SimpleNamespace as NS

from dotenv import dotenv_values

from tests.harness import ROOT, Checker, setup_env

real = dotenv_values(os.path.join(ROOT, ".env"))
TMP = setup_env(JARVIS_VAULT="memory", GOOGLE_CLIENT_ID="test-client.apps.googleusercontent.com",
                GOOGLE_CLIENT_SECRET="GOCSPX-test-secret-123", LLM_PROVIDER="ollama",
                OLLAMA_MODEL=real.get("OLLAMA_MODEL", "qwen3:4b").split("#")[0].strip())
logging.basicConfig(level=logging.WARNING)

from room_agent import runtime as rt  # noqa: E402
from room_agent.integrations import provider  # noqa: E402
from tests.fake_google import FakeGoogle, Mailbox  # noqa: E402

FG = FakeGoogle()
G = provider("google")
G.http = FG
G.connect(levels={"gmail": ["read", "compose"], "calendar": ["read", "write"]}, open_browser=FG.open_browser, timeout=5)
box = FG.mailboxes.setdefault("adam@gmail.test", Mailbox("adam@gmail.test"))
rt.writer = NS(observe=lambda *a, **k: None, conversation_ended=lambda *a: None, forgot=lambda *a: None)
rt.tts_enabled, rt.engine = False, None
SPOKEN = []
import room_agent.audio.speaker as speaker  # noqa: E402
import room_agent.conversation.turn as turn  # noqa: E402

turn.say = speaker.say = lambda s: SPOKEN.append(s)
import room_agent.llm.ollama as oll  # noqa: E402

oll.say = lambda s: SPOKEN.append(s)
import room_agent.llm.guard as guard  # noqa: E402

guard.say = lambda s: SPOKEN.append(s)
from room_agent.conversation.turn import take_turn  # noqa: E402
from tests.harness import simulate_actions  # noqa: E402

simulate_actions(keep_groups=("gmail", "calendar"))  # (Google is the fake one; nothing else the model calls really runs)

t = Checker()
H = []


def say(text):
    SPOKEN.clear()
    take_turn(H, text, final=True)
    print(f"  you: {text}\n  jarvis: {' '.join(SPOKEN)}   [{rt.pending!r}]")
    return " ".join(SPOKEN)


print(f"model: {os.environ['OLLAMA_MODEL']}")
say("Send an email to adam at gmail dot com.")
p = rt.pending
t.check("the real model starts a pending action with the spoken address", p is not None and p["tool"] == "gmail_create_draft"
        and p["args"].get("to") == "adam@gmail.com", p)
say("Subject is test.")
t.check("subject filled in the same request", rt.pending is p and p["args"].get("subject", "").lower() == "test", p)
say("What time is it?")
t.check("a question in between doesn't become the text; the request is kept", rt.pending is p and "body" not in p["args"], p)
say("Actually send it to sam at gmail dot com.")
t.check("recipient corrected", rt.pending is p and p["args"].get("to") == "sam@gmail.com", p)
say("Never mind.")
t.check("cancelled; nothing drafted", rt.pending is None and not box.drafts)
say("Schedule a meeting with Sarah.")
p = rt.pending
t.check("calendar: pending, waiting for when", p is not None and p["tool"] == "calendar_create_event" and "start" in p["missing"], p)
say("Friday at 3.")
made = [e for e in FG.calendars.get("adam@gmail.test", {}).get("events", {}).values()]
t.check("calendar: created with the collected title and time (in the SIMULATED calendar)", made
        and "T15:00" in made[-1]["start"]["dateTime"] and "sarah" in made[-1]["summary"].lower(), made)
t.done("LIVE PENDING TESTS")
