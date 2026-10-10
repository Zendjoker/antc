"""Zigbee devices, offline: a simulated Zigbee2MQTT (device list + devices that answer commands), no broker, no dongle,
no real lights. Checks what Jarvis knows (door, temperature, bed), what it says, light control through the executor
(verified, undoable), the fast path for "turn on the strip", the dashboard and the phone alert.

Run:  .venv\\Scripts\\python -m tests.test_zigbee
"""

import json
import threading
import time

from tests.harness import setup_env

setup_env(UNITS="imperial")

from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor  # noqa: E402
from room_agent.actions.events import events  # noqa: E402
from room_agent.tools.zigbee import hub  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
B = "zigbee2mqtt"
LIGHT_EXPOSES = [{"type": "light", "features": [
    {"name": "state", "property": "state"}, {"name": "brightness", "property": "brightness", "value_max": 254},
    {"name": "color_temp", "property": "color_temp", "value_min": 153, "value_max": 370},
    {"name": "color_xy", "property": "color", "type": "composite"}]},
    {"name": "effect", "property": "effect", "values": ["breathing", "rainbow1", "rainbow2", "chasing"]},
    # real exposes for the Aqara LED Strip T1 (lumi.light.acn132), confirmed from zigbee-herdsman-converters
    # dist/lib/lumi.js (lumiRGBEffectSpeed): numeric, 0-100%, 0 when no effect is active.
    {"name": "effect_speed", "property": "effect_speed", "value_min": 0, "value_max": 100}]
DEVICES = [
    {"type": "Coordinator", "friendly_name": "Coordinator", "ieee_address": "0x0"},
    {"friendly_name": "Door sensor", "ieee_address": "0x1", "power_source": "Battery",
     "definition": {"model": "MCCGQ11LM", "vendor": "Aqara", "description": "Door and window sensor",
                    "exposes": [{"property": "contact"}, {"property": "battery"}]}},
    {"friendly_name": "Room temperature", "ieee_address": "0x2", "power_source": "Battery",
     "definition": {"model": "WSDCGQ11LM", "vendor": "Aqara", "description": "Temperature and humidity sensor",
                    "exposes": [{"property": "temperature"}, {"property": "humidity"}, {"property": "pressure"}]}},
    {"friendly_name": "Vibration sensor", "ieee_address": "0x3", "power_source": "Battery",
     "definition": {"model": "DJT11LM", "vendor": "Aqara", "description": "Vibration sensor",
                    "exposes": [{"property": "vibration"}, {"property": "action"}, {"property": "battery"}]}},
    {"friendly_name": "LED strip", "ieee_address": "0x4", "power_source": "Mains (single phase)",
     "definition": {"model": "LGYCDD01LM", "vendor": "Aqara", "description": "LED Strip T1", "exposes": LIGHT_EXPOSES}},
]


class FakeZ2M:
    """Stands in for Zigbee2MQTT + the devices: a command comes back as the device's new state a moment later."""

    def __init__(self):
        self.sent, self.answer = [], True

    def publish(self, topic, payload):
        name = topic[len(B) + 1:-len("/set")]
        cmd = json.loads(payload)
        self.sent.append((name, cmd))
        if not self.answer:
            return
        new = dict(cmd)
        if "color" in cmd:
            new.update(color_mode="xy", color={"x": 0.14, "y": 0.1} if cmd["color"].get("hex") == "#0066FF" else
                       {"x": 0.69, "y": 0.3} if cmd["color"].get("hex") == "#FF0000" else cmd["color"])
        if "color_temp" in cmd:
            new["color_mode"] = "color_temp"
        threading.Timer(0.05, lambda: hub.handle(f"{B}/{name}", json.dumps(new).encode())).start()


fake = FakeZ2M()
hub.client = fake
seen = []
events.on("door.*", lambda e: seen.append((e["name"], e.get("device"))))
events.on("vibration.*", lambda e: seen.append((e["name"], e.get("device"))))

print("Devices and readings:")
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())
kinds = {n: d["kind"] for n, d in hub.devices.items()}
t.check("device list understood (coordinator skipped)", kinds == {"Door sensor": "contact", "Room temperature": "climate",
                                                                  "Vibration sensor": "vibration", "LED strip": "light"}, kinds)
t.check("light abilities read from its exposes", hub.devices["LED strip"]["light"].get("color") and
        hub.devices["LED strip"]["light"]["mireds"] == (153, 370) and "rainbow1" in hub.devices["LED strip"]["light"]["effects"])
for name, st in (("Door sensor", {"contact": True, "battery": 100}), ("Room temperature", {"temperature": 22.9, "humidity": 58.3, "pressure": 995}),
                 ("LED strip", {"state": "OFF", "brightness": 200, "color_mode": "color_temp", "color_temp": 300})):
    hub.handle(f"{B}/{name}", json.dumps(st).encode())
t.check("ready once devices and readings are in", hub.ready())
t.check("no live line for unrelated requests while nothing's happening (no wasted tokens)",
        not any(l.startswith("Home devices") for l in __import__("room_agent.actions.core", fromlist=["x"]).context_lines("tell me a joke")))
t.check("temperature in your units (imperial: °F)", hub.describe("Room temperature") == "Room temperature: 73°F, 58% humidity, 995 hPa",
        hub.describe("Room temperature"))
hub.handle(f"{B}/Door sensor", b'{"contact": false}')
t.check("door opening is announced on the event bus", ("door.opened", "Door sensor") in seen, seen)
t.check("...and described as open, with the time", hub.describe("Door sensor").startswith("Door sensor: OPEN (since "))
hub.handle(f"{B}/Door sensor", b'{"contact": true}')
t.check("door closing announced too", ("door.closed", "Door sensor") in seen and "last opened just now" in hub.describe("Door sensor"),
        hub.describe("Door sensor"))
hub.handle(f"{B}/Vibration sensor", b'{"action": "vibration", "vibration": true}')
t.check("bed movement announced and remembered", ("vibration.detected", "Vibration sensor") in seen
        and "last movement just now" in hub.describe("Vibration sensor"))
n = len(seen)
hub.handle(f"{B}/Vibration sensor", b'{"action": "vibration", "vibration": true, "linkquality": 200}')
t.check("a repeated report of the same movement isn't announced twice", len(seen) == n)
hub.handle(f"{B}/Room temperature", b'{"battery": 12}')
t.check("low battery is mentioned", "battery low: 12%" in hub.describe("Room temperature"), hub.describe("Room temperature"))

print("What Jarvis knows:")
core.ensure_loaded()
rt.new_turn("is the door open?")
offered = {c["name"] for c in core.offered()}
t.check("door / light requests offer the Zigbee tools", {"home_sensors", "set_light"} <= offered, sorted(offered)[:12])
ctx = core.context_lines("is the door open?")
t.check("the live device line is in the context for home questions", any(l.startswith("Home devices (live):") for l in ctx), ctx[-2:])
r = executor.execute("home_sensors", {"device": "door"})
t.check("home_sensors: the door", r.success and "Door sensor: closed" in r.message, r.message)
r = executor.execute("home_sensors", {})
t.check("home_sensors: everything", r.success and all(k in r.message for k in ("Door sensor", "73°F", "Vibration sensor", "LED strip")), r.message)

print("Lights (through the executor):")
rt.new_turn("turn on the led strip")
r = executor.execute("set_light", {"on": True})
t.check("on: sent, read back, verified", r.success and r.verified and fake.sent[-1] == ("LED strip", {"state": "ON"})
        and hub.state["LED strip"]["state"] == "ON", (r.message, fake.sent[-1:]))
rt.new_turn("make it blue at 30 percent")
r = executor.execute("set_light", {"color": "blue", "brightness": 30})
t.check("color + brightness in one command", r.success and fake.sent[-1][1] == {"state": "ON", "brightness": 76, "color": {"hex": "#0066FF"}}
        and "color blue" in r.message, (r.message, fake.sent[-1]))
r = executor.execute("set_light", {"white": "warm"})
t.check("warm white -> the light's own warm end", r.success and fake.sent[-1][1]["color_temp"] == 337, fake.sent[-1])
r = executor.execute("set_light", {"effect": "rainbow"})
t.check("effects matched by name", r.success and fake.sent[-1][1]["effect"] == "rainbow1", fake.sent[-1])
r = executor.execute("set_light", {"effect": "sparkle-tornado"})
t.check("unsupported effect name: rejected before anything is sent, lists real ones", not r.success
        and "sparkle-tornado" in r.message and "breathing" in r.message and fake.sent[-1][1]["effect"] == "rainbow1",
        (r.message, fake.sent[-1]))
r = executor.execute("set_light", {"speed": 75})
t.check("effect speed: the real exposed key (effect_speed), value passed through within range",
        r.success and fake.sent[-1][1]["effect_speed"] == 75, fake.sent[-1])
r = executor.execute("set_light", {"speed": 150})
t.check("effect speed: out-of-range is refused before anything is sent (same schema-validated behavior as "
        "brightness/other bounded params), not silently clamped or invented", not r.success
        and fake.sent[-1][1]["effect_speed"] == 75, (r.message, fake.sent[-1]))
payload, why = hub.light_payload({"name": "Plain light", "light": {"max": 254}}, speed=50)
t.check("a light that doesn't report supporting effect_speed: speed is refused, nothing invented", payload is None
        and "effect speed" in why, why)
r = executor.execute("set_light", {"color": "plaid"})
t.check("unknown color: refused, nothing sent", not r.success and "plaid" in r.message and fake.sent[-1][1].get("effect_speed") == 75)
rt.new_turn("lights off")
executor.execute("set_light", {"on": False})
before_undo = hub.state["LED strip"]["state"]
r = executor.undo()
t.check("undo puts the light back as it was", before_undo == "OFF" and "OK" in r and hub.state["LED strip"]["state"] == "ON", r)
fake.answer = False
r = executor.execute("set_light", {"on": False})
t.check("a light that doesn't answer is never claimed as done", not r.success and "didn't answer" in r.message, r.message)
fake.answer = True

print("Fast path (no model call):")
from room_agent.cognition import reflex  # noqa: E402

for said, want in (("turn on the led strip", True), ("Jarvis, turn the lights off please", False), ("strip on", True)):
    m = reflex.match(said)
    t.check(f"reflex: {said!r}", m and m[0].name == "set_light" and m[1].get("on") is want, m and (m[0].name, m[1]))
t.check("reflex leaves real questions to the model", reflex.match("why is the light so dim") is None)

print("Dashboard + phone:")
from room_agent import control  # noqa: E402

snap = control._home()
door = next(d for d in snap["devices"] if d["kind"] == "contact")
t.check("dashboard snapshot: devices with live state", snap["online"] and door["open"] is False and door["opened"]
        and any(d["kind"] == "light" and d["color"] for d in snap["devices"]), snap)
out = control.do({"do": "light", "device": "LED strip", "color": "red"})
t.check("dashboard button -> the same verified light change", out["ok"] and fake.sent[-1][1]["color"] == {"hex": "#FF0000"}, out)
from room_agent.phone import state as phone_state  # noqa: E402
from room_agent.phone.watcher import Watcher  # noqa: E402

calls = []
w = Watcher(lambda key, greeting, item: calls.append((key, greeting)))
phone_state.driving = None
w.on_door({"device": "Door sensor", "at": time.time()})
t.check("door opening while you're home: no call", not calls)
phone_state.driving = {"since": time.time(), "source": "test"}
w.on_door({"device": "Door sensor", "at": time.time()})
t.check("door opening while you're driving: Jarvis calls you", calls and "door sensor just opened" in calls[0][1].lower(), calls)
phone_state.driving = None
t.done("ZIGBEE")
