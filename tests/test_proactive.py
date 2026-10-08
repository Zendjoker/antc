"""Proactive policy, offline: when an event in the room is spoken about, held, noted silently or ignored. Repeated door
events, a flapping sensor, overlapping conversations, quiet mode, quiet hours, cooldowns that survive a restart,
unavailable devices, waiting events that go stale. No audio, no model, no PC actions.

Run:  .venv\\Scripts\\python -m tests.test_proactive
"""

import json
import time

from tests.harness import setup_env

setup_env(GREET_COOLDOWN_MIN="30")

from room_agent import config  # noqa: E402
from room_agent import control  # noqa: E402
from room_agent import proactive as P  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.conversation.states import State  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
rt.state.go(State.WAKE_WORD_ONLY, "test")
now = time.time()


def door(at, name="Door sensor"):
    return P.Event("arrival", f"door:{name}", f"{name} opened", at=at)


print("Speak, wait, silent, ignore:")
t.check("door opens, Jarvis free -> speak (greet)", P.decide(door(now))[0] == P.SPEAK_NOW)
t.check("the same door again 5 s later (flapping / noisy sensor) -> ignored", P.decide(door(now + 5))[0] == P.IGNORE)
d = P.decide(door(now + 300))
t.check("door again 5 min later -> no second greeting (cooldown), just a note", d[0] == P.SILENT and "already spoke" in d[1], d)
P.reset()
rt.state.go(State.SPEAKING, "test")
d = P.decide(door(now))
t.check("door opens while Jarvis is speaking -> doesn't interrupt itself; recorded silently", d[0] == P.SILENT and "conversation" in d[1], d)
t.check("...and the dashboard gets a silent notification", any("not spoken" in a["text"] for a in control._activity[-3:]))
rt.state.go(State.QUIET, "test")
P.reset()
t.check("quiet mode -> nothing spoken", P.decide(door(now))[0] == P.SILENT)
rt.state.go(State.WAKE_WORD_ONLY, "test")
P.reset()
config.QUIET_HOURS = f"{time.localtime(now).tm_hour}-{(time.localtime(now).tm_hour + 1) % 24}"
t.check("quiet hours -> nothing spoken", P.decide(door(now)) == (P.SILENT, "quiet hours"))
config.QUIET_HOURS = ""
P.reset()
t.check("greetings turned off in settings -> ignored", P.decide(door(now), enabled=False)[0] == P.IGNORE)
t.check("an urgent event (security) is spoken even mid-conversation", (rt.state.go(State.LISTENING, "t"), P.decide(
    P.Event("security", "sec:1", "door opened while away", at=now)))[1][0] == P.SPEAK_NOW)
rt.state.go(State.WAKE_WORD_ONLY, "test")

print("Waiting for the conversation to end:")
P.reset()
rt.state.go(State.LISTENING, "talking")
ev = P.Event("task_attention", "task:1", "your download finished", at=now)
t.check("something that needs attention, mid-conversation -> held", P.decide(ev)[0] == P.WAIT and P.pending())
rt.state.go(State.WAKE_WORD_ONLY, "done talking")
out = P.on_idle(now + 30)
t.check("conversation ends -> decided again, now spoken", out and out[0][1] == P.SPEAK_NOW, out)
rt.state.go(State.LISTENING, "talking")
P.decide(P.Event("task_attention", "task:2", "another thing", at=now))
rt.state.go(State.WAKE_WORD_ONLY, "done")
t.check("...but one that waited too long is dropped (no longer relevant)", P.on_idle(now + 3600) == [])

print("Restarts and devices:")
P.reset()
P.decide(door(now))
saved = json.loads(config.PROACTIVE_STATE_FILE.read_text(encoding="utf-8"))
P._state["seen"].clear(), P._state["spoken"].clear()
P._loaded["done"] = False  # (a fresh start: reload from disk)
t.check("cooldowns survive a restart (no re-greeting right after starting)", "arrival" in saved["spoken"]
        and P.decide(door(now + 60))[0] != P.SPEAK_NOW)
from room_agent.tools import zigbee  # noqa: E402

hub = zigbee.hub
hub.handle("zigbee2mqtt/bridge/devices", json.dumps([{"friendly_name": "Door sensor", "ieee_address": "0x1", "power_source": "Battery",
    "definition": {"model": "MCCGQ11LM", "description": "Door", "exposes": [{"property": "contact"}]}}]).encode())
n = len(control._activity)
hub.handle("zigbee2mqtt/Door sensor/availability", b'{"state": "offline"}')
hub.handle("zigbee2mqtt/Door sensor/availability", b'{"state": "offline"}')
notes = [a for a in control._activity[n:] if "offline" in a["text"]]
t.check("a device going offline -> one silent notice (not repeated)", len(notes) == 1, control._activity[n:])
t.check("...recorded in the event history", hub.last_event("Door sensor", "went offline") is not None)
t.done("PROACTIVE")
