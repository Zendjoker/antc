"""Smart-home device state, offline (simulated Zigbee2MQTT): aliases, 'it' = the light just used, ambiguity asks once,
offline devices fail fast, rapid commands each verified against their own result, reconnects, stale readings, and
'what happened when I entered' without claiming who it was. No PC actions, no network.

Run:  .venv\\Scripts\\python -m tests.test_smart_home
"""

import json
import threading
import time

from tests.harness import setup_env

setup_env(ZIGBEE_ALIASES="desk light=Desk lamp, bed=Vibration sensor")

from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor  # noqa: E402
from room_agent.tools import zigbee  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
core.ensure_loaded()
B = "zigbee2mqtt"


class FakeZ2M:
    def __init__(self):
        self.delay = {"LED strip": 0.03, "Desk lamp": 0.03}

    def publish(self, topic, payload):
        name, cmd = topic[len(B) + 1:-len("/set")], json.loads(payload)
        threading.Timer(self.delay.get(name, 0.03), lambda: zigbee.hub.handle(f"{B}/{name}", json.dumps(cmd).encode())).start()


fz = FakeZ2M()
hub = zigbee.hub
hub.client = fz
LIGHT = lambda name, ieee: {"friendly_name": name, "ieee_address": ieee, "power_source": "Mains", "definition": {
    "model": "LGYCDD01LM", "description": "LED light", "exposes": [{"type": "light", "features": [
        {"property": "state"}, {"property": "brightness", "value_max": 254}]}]}}
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps([LIGHT("LED strip", "0x4"), LIGHT("Desk lamp", "0x5"),
    {"friendly_name": "Door sensor", "ieee_address": "0x1", "power_source": "Battery", "definition": {
        "model": "MCCGQ11LM", "description": "Door sensor", "exposes": [{"property": "contact"}]}},
    {"friendly_name": "Room temperature", "ieee_address": "0x2", "power_source": "Battery", "definition": {
        "model": "WSDCGQ11LM", "description": "Temperature sensor", "exposes": [{"property": "temperature"}, {"property": "humidity"}]}}]).encode())
for n in ("LED strip", "Desk lamp"):
    hub.handle(f"{B}/{n}", b'{"state": "OFF", "brightness": 100}')
hub.handle(f"{B}/Door sensor", b'{"contact": true}')
hub.handle(f"{B}/Room temperature", b'{"temperature": 21.0, "humidity": 50}')


def do(said, args):
    rt.new_turn(said)
    return executor.execute("set_light", args)


print("Natural references:")
r = do("turn on the desk light", {"device": "desk light", "on": True})
t.check("an alias ('desk light') reaches the right device", r.success and hub.state["Desk lamp"]["state"] == "ON", r.message)
r = do("turn on the lights", {"on": True})
t.check("two lights, none named -> asks which (the only clarification needed)", not r.success and "which light?" in r.message, r.message)
do("make the strip brighter", {"device": "strip", "brightness": 80})
r = do("now turn it off", {"on": False})
t.check("'turn it off' -> the light just used (the strip), not the other one", r.success and hub.state["LED strip"]["state"] == "OFF"
        and hub.state["Desk lamp"]["state"] == "ON", (r.message, hub.state["LED strip"]["state"], hub.state["Desk lamp"]["state"]))

print("Offline, rapid, reconnect, stale:")
hub.handle(f"{B}/Desk lamp/availability", b'{"state": "offline"}')
t0 = time.time()
r = do("turn off the desk light", {"device": "desk light", "on": False})
t.check("an offline light fails at once, never claimed", not r.success and time.time() - t0 < 1, (r.message, time.time() - t0))
hub.handle(f"{B}/Desk lamp/availability", b'{"state": "online"}')
fz.delay["LED strip"] = 0.3  # (slow answers: the second command is sent before the first is confirmed)
results = []
th = threading.Thread(target=lambda: results.append(("a", do("strip 20", {"device": "strip", "brightness": 20}))))
th.start()
time.sleep(0.05)
results.append(("b", do("strip 60", {"device": "strip", "brightness": 60})))
th.join()
ok = {k: v.success for k, v in results}
t.check("two rapid commands: each verified against its own result, final state is the last one",
        all(ok.values()) and hub.state["LED strip"]["brightness"] == 152, (ok, hub.state["LED strip"].get("brightness")))
hub.online = False
t.check("broker connection lost -> devices not ready, nothing claimed", not hub.ready())
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
t.check("reconnected -> ready again with the last known state", hub.ready() and hub.state["LED strip"]["brightness"] == 152)
hub.changed["Room temperature"]["updated"] = time.time() - 5 * 3600
t.check("an old temperature reading is marked as possibly out of date", "may be out of date" in hub.describe("Room temperature"),
        hub.describe("Room temperature"))

print("What happened when I came in:")
hub.handle(f"{B}/Door sensor", b'{"contact": false}')
hub.handle(f"{B}/Door sensor", b'{"contact": true}')
ctx = "\n".join(core.context_lines("what happened when I entered?"))
t.check("the door events are in front of the model", "Door sensor opened at" in ctx and "Door sensor closed at" in ctx, ctx[-400:])
t.check("...with the rule that a door sensor never shows who it was", "never who it was" in ctx)
t.check("unsupported devices aren't claimed (no window / AC / lock control listed)", "no window, blinds, AC" in " ".join(
    core.rules(["set_light"])))
t.done("SMART HOME")
