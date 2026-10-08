"""Your Zigbee devices (implementation: tools/zigbee.py): what the sensors say (door, temperature, bed, presence) and
the lights (on/off, brightness, color, white, effects). Reads are live; light changes are verified and can be undone."""

import re
import time

from room_agent.abilities._kit import params, tool
from room_agent.actions.core import Group, register_claim, register_context, register_group


def _hub():
    from room_agent.tools.zigbee import hub

    return hub


def _ready():
    return _hub().ready()


def _live():
    marks = _hub().changed.values()
    return any(time.time() - max(m.get("opened", 0), m.get("closed", 0), m.get("presence", 0)) < 600 for m in marks)


HINTS = re.compile(r"\b(door|window|open(ed)?|closed?|temperature|temp|hot|cold|warm|cool|humid|humidity|degrees|bed|sleep|"
                   r"slept|awake|movement|moving|motion|vibrat|presence|someone|anyone|nobody|room|sensors?|zigbee|aqara|"
                   r"lights?|led|strip|lamp|colou?r|bright|dim|rainbow|effect|red|blue|green|purple|pink|white|orange|yellow)", re.I)
register_group(Group("zigbee", HINTS, _live, "your Zigbee sensors and lights",
                     lambda: "; ".join(_hub().summary()) if _ready() else "", _ready, rules=[
    "- Door, temperature, bed and presence questions: answer from home_sensors (or the live line in the context), never "
    "from memory; say when a reading is old.",
    "- Light requests ('make it blue', 'dim the strip', 'lights off'): call set_light; with no device named, it uses the "
    "only light. Confirm only what it reports back."]))
register_claim("light", r"\b(made|set|changed|switched|turned)\b.{0,30}\b(red|orange|yellow|green|teal|cyan|blue|purple|"
                        r"violet|pink|magenta|white|warm|cool|rainbow)\b|\b(dimmed|brightened)\b|\b(led|strip|lights?|lamp)\b"
                        r".{0,20}\b(is|are|'s)\s+(now\s+)?(on|off|red|blue|green|purple|pink|white|dim(med)?)\b")


def _context(user_text):
    """A live line when the request is about the home, or right after the door / presence changed."""
    h = _hub()
    if not h.ready() or not (HINTS.search(user_text or "") or _live()):
        return []
    return ["Home devices (live): " + "; ".join(h.summary())]


register_context(_context, order=55)


# ---------------------------------------------------------------- sensors
def _sensors(args):
    h = _hub()
    if not h.ready():
        return "UNAVAILABLE: Zigbee2MQTT isn't running, so the sensors can't be read right now."
    said = str(args.get("device") or "").strip()
    if said:
        dev = h.find(said)
        if not dev:
            return f"FAILED: no device matches '{said}'. They are: {', '.join(h.devices)}."
        return "OK: " + h.describe(dev["name"])
    return "OK: " + "; ".join(h.summary())


tool("home_sensors", "What the home sensors say right now: whether the door is open (and when it last opened), room "
     "temperature and humidity, the last movement on the bed sensor, presence, and the lights' state. Leave device out "
     "for everything.", params({"device": {"type": "string", "description": "Optional: 'door', 'temperature', 'bed'..."}}),
     _sensors, group="zigbee", changes_state=False, examples=["is the door open", "how warm is it in here",
                                                              "did anyone open the door", "when did I last move in bed"])


# ---------------------------------------------------------------- lights
def _light(args):
    h = _hub()
    dev = h.find(args.get("device") or "", kind="light") or h.find(args.get("device") or "", kind="switch")
    if dev is None:
        lights = [d["name"] for d in h.devices.values() if d["kind"] in ("light", "switch")]
        return ("FAILED: which light? " + ", ".join(lights)) if lights else "FAILED: there are no Zigbee lights."
    return dev


def _observe(args, before=None):
    dev = _light(args)
    if isinstance(dev, str):
        return None
    st = _hub().state.get(dev["name"], {})
    return {"name": dev["name"], "state": st.get("state"), "brightness": st.get("brightness"),
            "color_mode": st.get("color_mode"), "color": st.get("color"), "color_temp": st.get("color_temp"),
            "effect": st.get("effect")}


def _set_light(args):
    h = _hub()
    if not h.ready():
        return "UNAVAILABLE: Zigbee2MQTT isn't running, so the lights can't be changed right now."
    dev = _light(args)
    if isinstance(dev, str):
        return dev
    on = args.get("on")
    payload, problem = h.light_payload(dev, on=on if isinstance(on, bool) else None, brightness=args.get("brightness"),
                                       color=args.get("color"), white=args.get("white"), effect=args.get("effect"))
    if payload is None:
        return f"FAILED: {problem}."
    if args.get("toggle"):
        payload["state"] = "OFF" if h.state.get(dev["name"], {}).get("state") == "ON" else "ON"
    after = h.set(dev["name"], payload)
    if after is None:
        return f"FAILED: {dev['name']} didn't answer (it may be unplugged or out of range). Nothing confirmed."
    return "OK: " + h.describe(dev["name"]) + "."


def _verify(args, before, after):
    if after is None:
        return False
    if args.get("on") is False:
        return after["state"] == "OFF"
    if args.get("on") is True or args.get("brightness") or args.get("color") or args.get("white") or args.get("effect"):
        return after["state"] == "ON"
    return True


def _undo(args, before, after):
    h = _hub()
    p = {"state": before["state"] or "OFF"}
    if before["state"] == "ON":
        if before.get("brightness"):
            p["brightness"] = before["brightness"]
        if before.get("color_mode") == "color_temp" and before.get("color_temp"):
            p["color_temp"] = before["color_temp"]
        elif before.get("color"):
            p["color"] = {k: before["color"][k] for k in ("x", "y") if k in before["color"]}
    back = h.set(before["name"], p)
    return f"OK: {h.describe(before['name'])} (as it was)." if back else f"FAILED: {before['name']} didn't answer."


def _subject(args):
    dev = _light(args)
    return None if isinstance(dev, str) else dev["name"]


def _said(result):
    after = result.state_after or {}
    if after.get("state") == "OFF":
        return "Okay, it's off."
    return "Okay, it's on." if after.get("state") == "ON" else None


def _reflex_ok(args):
    """'turn on the strip' runs without the model only if there's exactly one light it can mean; the spoken on/off
    word becomes the `on` argument."""
    args["on"] = args.pop("on_word", "").lower() == "on"
    return _ready() and not isinstance(_light(args), str)


LIGHT = r"(?:the\s+)?(?P<device>led(?:\s+strip)?|strip|lights?|lamp)"
tool("set_light", "Change a Zigbee light (the LED strip): on/off, brightness, color, white tone or an effect, in one call. "
     "Leave device out if there's only one light.",
     params({"device": {"type": "string", "description": "Optional: which light, e.g. 'LED strip'"},
             "on": {"type": "boolean", "description": "true = on, false = off"},
             "toggle": {"type": "boolean", "description": "Flip it (on <-> off)"},
             "brightness": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Percent"},
             "color": {"type": "string", "description": "A color name (red, blue, purple, pink...) or #hex"},
             "white": {"type": "string", "enum": ["warm", "neutral", "cool", "daylight"]},
             "effect": {"type": "string", "description": "An effect the light has, e.g. 'rainbow'"}}),
     _set_light, group="zigbee", claim=["home", "light"], event="light.changed", subject=_subject, observe=_observe,
     verify=_verify, undo=_undo, undo_is_symmetric=True,
     examples=["turn on the LED strip", "make the lights blue", "dim the strip to 20%", "warm white"],
     reflex=[(r"turn\s+(?P<on_word>on|off)\s+" + LIGHT, {}), (r"turn\s+" + LIGHT + r"\s+(?P<on_word>on|off)", {}),
             (LIGHT + r"\s+(?P<on_word>on|off)", {})],
     reflex_check=lambda a: _reflex_ok(a), reflex_say=_said)
