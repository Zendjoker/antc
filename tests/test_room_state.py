"""Room context (room_agent/room_state.py), offline: a simulated Zigbee2MQTT feed (no broker, no dongle) drives the
real `hub` singleton exactly like test_zigbee.py does, then room_state.current() is checked against it. Deterministic:
every "how long ago" is controlled by passing an explicit `now` to current(), never a real sleep().

Run:  .venv\\Scripts\\python -m tests.test_room_state
"""

import json
import time

from tests.harness import setup_env

setup_env()

from room_agent import room_state, triggers  # noqa: E402
from room_agent.room_state import ActivityHint, RoomState  # noqa: E402
from room_agent.tools.zigbee import hub  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
B = "zigbee2mqtt"
DEVICES = [
    {"type": "Coordinator", "friendly_name": "Coordinator", "ieee_address": "0x0"},
    {"friendly_name": "Door sensor", "ieee_address": "0x1", "power_source": "Battery",
     "definition": {"model": "MCCGQ11LM", "vendor": "Aqara", "description": "Door and window sensor",
                    "exposes": [{"property": "contact"}, {"property": "battery"}]}},
    {"friendly_name": "Vibration sensor", "ieee_address": "0x3", "power_source": "Battery",
     "definition": {"model": "DJT11LM", "vendor": "Aqara", "description": "Vibration sensor (on the bed frame)",
                    "exposes": [{"property": "vibration"}, {"property": "action"}, {"property": "battery"}]}},
    # The real FP1E publishes more than a bare boolean (distance, illuminance...); room_state.py only acts on
    # "presence", so those extra fields are included here to confirm they pass through harmlessly, unused.
    {"friendly_name": "bedroom_presence", "ieee_address": "0x5", "power_source": "Mains (single phase)",
     "definition": {"model": "FP1E", "vendor": "Aqara", "description": "Presence sensor",
                    "exposes": [{"property": "presence"}, {"property": "distance"}, {"property": "illuminance"}]}},
]


def reset_all():
    """A clean slate between scenarios: as close to 'Jarvis just restarted' as an in-process test can get."""
    hub.state.clear()
    hub.changed.clear()
    hub.offline.clear()
    hub.events.clear()  # avoid a door event from an earlier scenario leaking into this one's "recently opened" check
    room_state.reset()
    triggers.idle_seconds = lambda: None  # default: no PC activity signal at all


def presence(value, at):
    """Publish a presence reading, then stamp hub's own bookkeeping with the simulated time `at`. hub.handle() always
    uses the real wall clock internally; these tests need full, deterministic control of elapsed time instead, so
    every helper below overwrites the timestamps hub just wrote with the simulated one."""
    before = hub.state.get("bedroom_presence", {}).get("presence")
    hub.handle(f"{B}/bedroom_presence", json.dumps({"presence": value, "distance": 1.2, "illuminance": 40}).encode())
    marks = hub.changed.setdefault("bedroom_presence", {})
    marks["updated"] = at
    if before != value:
        marks["presence"] = at


def vibration(at):
    hub.handle(f"{B}/Vibration sensor", json.dumps({"vibration": True, "action": "vibration"}).encode())
    marks = hub.changed.setdefault("Vibration sensor", {})
    marks["updated"] = marks["moved"] = at


def door(open_, at):
    hub.handle(f"{B}/Door sensor", json.dumps({"contact": not open_}).encode())
    marks = hub.changed.setdefault("Door sensor", {})
    marks["updated"] = at
    marks["opened" if open_ else "closed"] = at
    ev = hub.last_event("Door sensor", "opened" if open_ else "closed")
    if ev:
        ev["at"] = at  # _door_opened_recently() reads this, not the real wall-clock time record() used


reset_all()
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())
t.check("bedroom_presence understood as kind=presence", hub.devices["bedroom_presence"]["kind"] == "presence")
t.check("extra FP1E exposes (distance, illuminance) don't break device discovery",
        "distance" in hub.devices["bedroom_presence"]["props"])

# ---------------------------------------------------------------- 1. occupied / empty transitions
reset_all()
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())
t0 = time.time()
presence(True, t0)
ctx = room_state.current(now=t0 + 50)  # well past the debounce window: confirmed
t.check("presence true, held: RoomState.OCCUPIED", ctx.state == RoomState.OCCUPIED, ctx)
presence(False, t0 + 100)
ctx = room_state.current(now=t0 + 160)  # +60s after the flip: past debounce
t.check("presence false, held: RoomState.EMPTY", ctx.state == RoomState.EMPTY, ctx)
t.check("EMPTY carries no activity hint (nothing to infer about an empty room)", ctx.hint is None, ctx)

# ---------------------------------------------------------------- 2. false vacancy (a brief blip is not EMPTY)
reset_all()
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())
t0 = time.time()
presence(True, t0)
room_state.current(now=t0 + 50)  # confirm occupied first
presence(False, t0 + 55)
ctx = room_state.current(now=t0 + 60)  # only 5s since the flip: inside the debounce window
t.check("a brief 'nobody there' blip does not immediately flip to EMPTY (false vacancy)",
        ctx.state == RoomState.OCCUPIED, ctx)
presence(True, t0 + 58)  # it was a blip: back to True almost at once
ctx = room_state.current(now=t0 + 120)
t.check("after the blip reverts, state is still OCCUPIED (never dipped to EMPTY)", ctx.state == RoomState.OCCUPIED, ctx)

# ---------------------------------------------------------------- 3. rapid state changes (real log pattern: flaps every few seconds)
reset_all()
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())
t0 = time.time()
presence(True, t0)
room_state.current(now=t0 + 50)
seen_states = set()
for i, v in enumerate([False, True, False, True, False]):  # mirrors the real zigbee_events.json flapping pattern
    presence(v, t0 + 51 + i * 8)  # every 8s: well inside the 45s debounce window each time
    seen_states.add(room_state.current(now=t0 + 52 + i * 8).state)
t.check("rapid flapping inside the debounce window never thrashes RoomState (one confirmed value throughout)",
        seen_states == {RoomState.OCCUPIED}, seen_states)

# ---------------------------------------------------------------- 4. stale / offline sensor
reset_all()
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())
t0 = time.time()
presence(True, t0)
ctx = room_state.current(now=t0 + room_state.PRESENCE_STALE_S + 30)
t.check("a reading older than PRESENCE_STALE_S is UNKNOWN_OFFLINE, not a guessed last value",
        ctx.state == RoomState.UNKNOWN_OFFLINE, ctx)
reset_all()
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())
presence(True, time.time())
hub.handle(f"{B}/bedroom_presence/availability", b'{"state": "offline"}')
ctx = room_state.current()
t.check("Zigbee2MQTT reporting the device offline is UNKNOWN_OFFLINE even with a recent reading",
        ctx.state == RoomState.UNKNOWN_OFFLINE, ctx)

# ---------------------------------------------------------------- 5. bed vibration: fresh, and NOT treated as proof of absence
reset_all()
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())
t0 = time.time()
presence(True, t0)
room_state.current(now=t0 + 50)
vibration(t0 + 55)
ctx = room_state.current(now=t0 + 70)  # vibration 15s ago: fresh; no desk activity configured
t.check("fresh bed vibration + occupied + no desk activity -> POSSIBLY_AT_BED",
        ctx.hint == ActivityHint.POSSIBLY_AT_BED, ctx)
t.check("RoomState stays OCCUPIED (the hint is additional, not a replacement)", ctx.state == RoomState.OCCUPIED, ctx)

# "no vibration while someone remains on the bed": vibration goes stale, but presence is still true -> must NOT
# become EMPTY and must NOT claim the bed is empty (there is no such claim anywhere in this module).
ctx = room_state.current(now=t0 + 55 + room_state.BED_VIBRATION_FRESH_S + 60)  # vibration now stale; still occupied
t.check("stale vibration while still occupied is NOT reported as EMPTY", ctx.state != RoomState.EMPTY, ctx)
t.check("stale vibration while still occupied is NOT reported as POSSIBLY_AT_BED either (no fresh positive evidence)",
        ctx.hint != ActivityHint.POSSIBLY_AT_BED, ctx)

# ---------------------------------------------------------------- 6. false vibration / concurrent PC activity -> AMBIGUOUS
reset_all()
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())
t0 = time.time()
presence(True, t0)
room_state.current(now=t0 + 50)
triggers.idle_seconds = lambda: 5.0  # "typing right now"
vibration(t0 + 60)
ctx = room_state.current(now=t0 + 65)  # both signals fresh and contradictory at once
t.check("fresh bed vibration at the same time as desk activity -> AMBIGUOUS, not a guess either way",
        ctx.hint == ActivityHint.AMBIGUOUS, ctx)

# ---------------------------------------------------------------- 7. concurrent PC activity alone -> POSSIBLY_AT_DESK
reset_all()
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())
t0 = time.time()
presence(True, t0)
room_state.current(now=t0 + 50)
triggers.idle_seconds = lambda: 3.0
ctx = room_state.current(now=t0 + 55)
t.check("desk activity, no recent vibration -> POSSIBLY_AT_DESK", ctx.hint == ActivityHint.POSSIBLY_AT_DESK, ctx)

# ---------------------------------------------------------------- 8. possibly_resting - never phrased as asleep
reset_all()
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())
night = time.localtime(time.time())
# build a timestamp at 23:00 local today for a readable test regardless of when it's actually run
import datetime

base = datetime.datetime.now().replace(hour=23, minute=0, second=0, microsecond=0)
t0 = base.timestamp()
triggers.idle_seconds = lambda: None
presence(True, t0)
ctx = room_state.current(now=t0 + 50)
t.check("occupied, 11 PM, no desk/bed signal -> POSSIBLY_RESTING", ctx.hint == ActivityHint.POSSIBLY_RESTING, ctx)
t.check("POSSIBLY_RESTING's evidence text never says 'asleep' or 'in bed'",
        "asleep" not in ctx.why.lower() and "in bed" not in ctx.why.lower(), ctx.why)

# ---------------------------------------------------------------- 9. returning to room
reset_all()
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())
t0 = time.time()
door(False, t0)  # baseline reading: Hub only records an "opened" event on a CHANGE from a known prior contact state
presence(False, t0)
room_state.current(now=t0 + 50)  # confirmed empty first
door(True, t0 + 100)
presence(True, t0 + 102)
ctx = room_state.current(now=t0 + 103)
t.check("door opened then presence confirmed moments later -> RETURNING", ctx.state == RoomState.RETURNING, ctx)

# ---------------------------------------------------------------- 10. restart behavior (no stale carry-over)
reset_all()  # the only thing a real restart changes for this module: the in-memory debounce memory goes away
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps(DEVICES).encode())
t0 = time.time()
presence(True, t0 - 60)  # a reading that's already 60s old "on first read after restart"
ctx = room_state.current(now=t0)
t.check("first reading after a reset, already older than the debounce window, is accepted at once (no stale lockout)",
        ctx.state == RoomState.OCCUPIED, ctx)

# ---------------------------------------------------------------- 11. no unauthorized device actions
t.check("room_state module never imports anything that sends a Zigbee command",
        "def set(" not in open(room_state.__file__, encoding="utf-8").read() and
        "hub.set(" not in open(room_state.__file__, encoding="utf-8").read())
t.check("room_state module never imports proactive (nothing here can speak on its own)",
        "import proactive" not in open(room_state.__file__, encoding="utf-8").read())

reset_all()
t.done("ROOM STATE")
