"""Multi-step tasks and the action lifecycle, offline (scripted model, simulated Zigbee2MQTT): the lights + temperature
example, partial failure reported honestly, no double execution after a retry, cancellation mid-task, confirmation for
risky actions, and recovery after a restart. No PC actions, no API calls.

Run:  .venv\\Scripts\\python -m tests.test_tasks
"""

import json
import threading
import time

from tests.harness import setup_env

setup_env(UNITS="imperial")

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import journal  # noqa: E402
from room_agent.tools import timers, zigbee  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

t = Checker()
B = "zigbee2mqtt"


class FakeZ2M:
    def __init__(self):
        self.fail_color = False

    def publish(self, topic, payload):
        name, cmd = topic[len(B) + 1:-len("/set")], json.loads(payload)
        if self.fail_color and "color" in cmd:
            return  # (the strip doesn't answer this one)
        new = dict(cmd)
        if "color" in cmd:
            new.update(color_mode="xy", color={"x": 0.14, "y": 0.1})
        threading.Timer(0.03, lambda: zigbee.hub.handle(f"{B}/{name}", json.dumps(new).encode())).start()


fz = FakeZ2M()
hub = zigbee.hub
hub.client = fz
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps([
    {"friendly_name": "LED strip", "ieee_address": "0x4", "power_source": "Mains", "definition": {"model": "LGYCDD01LM",
     "description": "LED Strip T1", "exposes": [{"type": "light", "features": [{"property": "state"},
     {"property": "brightness", "value_max": 254}, {"name": "color_xy", "property": "color"}]}]}},
    {"friendly_name": "Room temperature", "ieee_address": "0x2", "power_source": "Battery", "definition": {"model": "WSDCGQ11LM",
     "description": "Temperature and humidity sensor", "exposes": [{"property": "temperature"}, {"property": "humidity"}]}}]).encode())
hub.handle(f"{B}/LED strip", b'{"state": "ON", "brightness": 254}')
hub.handle(f"{B}/Room temperature", b'{"temperature": 21.5, "humidity": 52}')
convo = Conversation()

print("Dim to 30%, make it blue, and tell me the temperature:")
convo.say("Dim the lights to 30%, change them to blue, and tell me the room temperature.",
          scripts=[{"tools": [("set_light", {"brightness": 30}), ("set_light", {"color": "blue"}), ("home_sensors", {"device": "temperature"})]},
                   {"text": "Done: the lights are at 30% and blue, and it's 71 degrees in here."}])
acts = journal.recent(3)
t.check("three actions, each with a full lifecycle ending COMPLETED", [e["state"] for e in acts] == ["COMPLETED"] * 3
        and acts[0]["history"][:4] == ["REQUESTED", "PLANNED", "EXECUTING", "VERIFYING"], [(e["action"], e["history"]) for e in acts])
t.check("the light really changed (brightness 30%, blue) and the reading is real", hub.state["LED strip"].get("brightness") == 76
        and hub.state["LED strip"].get("color_mode") == "xy")
t.check("the outcome is reported", "71 degrees" in " ".join(convo.said()), convo.said())

print("Partial failure:")
fz.fail_color = True
convo.say("Dim the lights to 50% and make them blue.",
          scripts=[{"tools": [("set_light", {"brightness": 50}), ("set_light", {"color": "blue"})]},
                   {"text": "All done!"},
                   {"text": "The lights are at 50%, but the color change didn't go through: the strip didn't answer."}])
acts = journal.recent(2)
t.check("one action COMPLETED, the other FAILED (not marked done)", [e["state"] for e in acts] == ["COMPLETED", "FAILED"],
        [(e["action"], e["state"]) for e in acts])
said = " ".join(convo.said())
t.check("'All done!' is never spoken when one part failed; the honest report is", "All done" not in said
        and "didn't go through" in said, convo.said())
fz.fail_color = False

print("No double execution:")
convo.say("set a timer for 5 minutes",
          scripts=[{"tools": [("set_timer", {"seconds": 300})]}, {"tools": [("set_timer", {"seconds": 300})]},
                   {"text": "Five minutes, on."}])
n = sum(1 for i in timers._items.values() if abs((i["due"] - time.time()) - 300) < 5)
t.check("the model asking again in a later round (retry / timeout) doesn't make a second timer", n == 1, n)
convo.say("set two timers for 1 minute", scripts=[{"tools": [("set_timer", {"seconds": 60}), ("set_timer", {"seconds": 60})]},
                                                   {"text": "Two one-minute timers."}])
n = sum(1 for i in timers._items.values() if abs((i["due"] - time.time()) - 60) < 5)
t.check("...but two asked for in the same round are both made (deliberate)", n == 2, n)

print("Cancellation, confirmation, restart:")


class Interrupter:
    interrupted = threading.Event()


calls = []
real = zigbee.Hub.set


def slow_set(self, name, payload, wait=4.0):
    calls.append(payload)
    rt.turn.cancel.set()  # (they talk over it while the first action runs)
    return real(self, name, payload, wait)


zigbee.Hub.set = slow_set
convo.say("turn the lights red and then off", scripts=[{"tools": [("set_light", {"color": "red"}), ("set_light", {"on": False})]},
                                                      {"text": "Okay."}])
zigbee.Hub.set = real
t.check("interrupted mid-task -> the remaining action doesn't run", len(calls) == 1, calls)
rt.new_turn("cancel the timer")
from room_agent.actions import executor  # noqa: E402

r = executor.execute("cancel_timer", {"label": "all", "confidence": 0.3})
t.check("a risky action without clear intent waits for a yes (WAITING), nothing done", not r.success
        and journal.recent(1)[0]["state"] == "WAITING" and timers._items, journal.recent(1)[0])
journal.state(journal.EXECUTING, jid=journal.requested("set_light", {"color": "green"}))
journal._save()
journal._entries.clear()
cut = journal.load()
t.check("after a restart, an action that was mid-run is marked interrupted (outcome unknown), not completed",
        cut and cut[0]["action"] == "set_light" and cut[0]["state"] == "CANCELED", cut)
lines = journal.context_lines("hi")
t.check("...and the model is told once not to claim it or blindly redo it", lines and "UNKNOWN" in lines[0]
        and journal.context_lines("hi") == [], lines)
t.done("TASKS")
