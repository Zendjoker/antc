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
                   r"notice|noticed|came in|come in|walked in|got (home|back|in)|arriv|enter|home|left|leave|"
                   r"slept|awake|movement|moving|motion|vibrat|presence|someone|anyone|nobody|room|sensors?|zigbee|aqara|"
                   r"lights?|led|strip|lamp|colou?r|bright|dim|rainbow|effect|red|blue|green|purple|pink|white|orange|yellow)", re.I)
register_group(Group("zigbee", HINTS, _live, "your Zigbee sensors and lights",
                     lambda: "; ".join(_hub().summary()) if _ready() else "", _ready, rules=[
    "- Door, temperature, bed and presence questions: answer from home_sensors (or the live line in the context), never "
    "from memory; say when a reading is old.",
    "- Light requests ('make it blue', 'dim the strip', 'lights off'): call set_light right away; with no device named, it "
    "uses the only light. A clear request is never answered with a question ('off or dimmed?'): do exactly what they said. "
    "Confirm only what it reports back.",
    "- Only offer what the devices listed here can do: there is no window, blinds, AC, heater, fan or lock control "
    "unless a device for it is listed. If they ask for one, say plainly it isn't connected."]))
register_claim("light", r"\b(made|set|changed|switched|turned)\b.{0,30}\b(red|orange|yellow|green|teal|cyan|blue|purple|"
                        r"violet|pink|magenta|white|warm|cool|rainbow)\b|\b(dimmed|brightened)\b|\b(led|strip|lights?|lamp)\b"
                        r".{0,20}\b(is|are|'s)\s+(now\s+)?(on|off|red|blue|green|purple|pink|white|dim(med)?)\b")


def _installed():
    kinds = {d["kind"] for d in _hub().devices.values()}
    missing = [what for kind, what in (("presence", "motion or presence sensor"), ("contact", "door sensor"),
                                       ("climate", "temperature sensor")) if kind not in kinds]
    return ("There is NO " + " and NO ".join(missing) + ": never claim readings from one.") if missing else ""


def _context(user_text):
    """A live line when the request is about the home, or right after the door / presence changed: current readings,
    what happened recently (from the event history, which survives restarts), and what isn't installed."""
    h = _hub()
    if not h.ready() or not (HINTS.search(user_text or "") or _live()):
        return []
    lines = ["Home devices (live): " + "; ".join(h.summary())]
    events = h.event_lines()
    lines.append("- home_events_recent (from the sensors, newest last; the only source for 'did you notice...'): "
                 + ("; ".join(events) if events else "none in the last 6 hours"))
    if _installed():
        lines.append("- home_sensors_missing: " + _installed())
    return lines


register_context(_context, order=55)


# ---------------------------------------------------------------- sensors
def _sensors(args):
    h = _hub()
    if not h.ready():
        return "UNAVAILABLE: Zigbee2MQTT isn't running, so the sensors can't be read right now."
    said = str(args.get("device") or "").strip()
    events = h.event_lines(within_s=24 * 3600)
    tail = (" Recent events: " + "; ".join(events) + ".") if events else " No sensor events in the last 24 hours."
    tail += (" " + _installed()) if _installed() else ""
    if said:
        dev = h.find(said)
        if not dev:
            return f"FAILED: no device matches '{said}'. They are: {', '.join(h.devices)}." + tail
        return "OK: " + h.describe(dev["name"]) + "." + tail
    return "OK: " + "; ".join(h.summary()) + "." + tail


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
    """Spoken after a light change, from what the light reported back (never from what was asked)."""
    after, asked = result.state_after or {}, result.parameters or {}
    if after.get("state") == "OFF":
        return "Okay, it's off."
    if after.get("state") != "ON":
        return None
    dev = _hub().devices.get(after.get("name"), {})
    if asked.get("color") and after.get("color_mode") in ("xy", "hs"):
        return f"Done, it's {_hub().color_name(after)} now."
    if asked.get("brightness") and after.get("brightness"):
        return f"Done, it's at {round(after['brightness'] / dev.get('light', {}).get('max', 254) * 100)}%."
    return "Okay, it's on."


def _reflex_ok(args):
    """'turn on the strip' / 'change the color to blue' / 'dim the LED to 30%' run without the model only if there's
    exactly one light it can mean. The spoken words become arguments."""
    if "on_word" in args:
        args["on"] = args.pop("on_word").lower() == "on"
    if "brightness" in args:
        args["brightness"] = max(1, min(100, int(args["brightness"])))
    if str(args.get("device", "")).lower() in ("color", "colour", "it"):
        args.pop("device")
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
             (LIGHT + r"\s+(?P<on_word>on|off)", {}),
             (r"(?:change|set|make|turn|switch)\s+(?:the\s+)?(?P<device>led(?:\s+strip)?|strip|lights?|lamp|colou?r|it)\s+"
              r"(?:colou?r\s+)?(?:to\s+)?(?P<color>red|orange|yellow|green|teal|cyan|blue|purple|violet|pink|magenta)", {}),
             (r"(?:dim|set|turn\s+down|bring\s+down|turn)\s+(?:the\s+)?(?P<device>led(?:\s+strip)?|strip|lights?|lamp)"
              r"\s+(?:down\s+)?(?:to\s+)?(?P<brightness>\d{1,3})\s*(?:%|percent)", {})],
     reflex_check=lambda a: _reflex_ok(a), reflex_say=_said)
