"""Smart home through Home Assistant (implementation: tools/home_assistant.py)."""

from room_agent.abilities._kit import params, tool
from room_agent.actions.core import Group, register_claim, register_group


def _status():
    from room_agent.tools.home_assistant import ha_status

    return ha_status()


register_group(Group("home", title="smart home control (Home Assistant)", summary=lambda: _status()[1],
                     available=lambda: _status()[0], rules=[
    "- For smart home requests, look up entity ids with home_assistant_states if you don't already know them.",
    "- Home Assistant is a SEPARATE, optional integration from the directly-connected Zigbee devices (home_sensors, "
    "set_light): if Home Assistant isn't connected, say specifically that, never 'smart home isn't available' or "
    "anything implying the Zigbee lights/sensors are also down - check their own tools/status, which don't depend on "
    "Home Assistant at all."]))
# ("all set, your meeting is on Friday" is not a device being set on: no "all set", and no "on <a day or date>")
register_claim("home", r"(?<!\ball )\b(turned|switched|shut|flipped|set)\b.{0,40}\b(on|off)\b(?!\s+(monitor|screen|display|your|"
                       r"my|the|a|this|that|monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow|"
                       r"january|february|march|april|may|june|july|august|september|october|november|december|\d)\b)"
                       r"|\b(lights?|lamps?|fan|heater|tv|plug|switch|thermostat|ac|heat(ing)?)\b.{0,30}\b(are|is)\s+(now\s+)?(on|off)\b"
                       r"|\b(dimmed|brightened)\b.{0,30}\b(lights?|lamps?)\b"
                       r"|\b(locked|unlocked)\b.{0,25}\b(door|doors|lock|garage|gate)\b|\b(door|doors|garage|gate)\b.{0,25}\b(locked|unlocked)\b")


def _ha_on():
    try:
        return _status()[0]
    except Exception:
        return False


from room_agent import truth  # noqa: E402

for _kind in ("window", "climate", "lock"):
    truth.DEVICE_AVAILABLE.setdefault(_kind, []).append(_ha_on)


def _states(args):
    from room_agent.tools.home_assistant import ha_states

    return ha_states(args.get("domain", ""))


def _call(args):
    from room_agent.tools.home_assistant import ha_call

    return ha_call(args["domain"], args["service"], args["entity_id"], args.get("data"))


def _prepare(args):
    """Refused services never get as far as a confirmation question; the rest are normalized, so the yes is matched
    against exactly what will run."""
    from room_agent.tools.home_assistant import check_call

    _, ids = check_call(args.get("domain"), args.get("service"), args.get("entity_id"), args.get("data"))
    return {**args, "domain": str(args["domain"]).strip().lower(), "service": str(args["service"]).strip().lower(),
            "entity_id": ", ".join(ids)}


def _risk(args):
    from room_agent.actions.core import Risk
    from room_agent.tools.home_assistant import Refused, check_call

    try:
        return check_call(args.get("domain"), args.get("service"), args.get("entity_id"), args.get("data"))[0]
    except Refused:
        return Risk.SENSITIVE  # (refused outright by _prepare / ha_call anyway; never "safe")


def _describe(args):
    from room_agent.tools.home_assistant import describe_call

    return describe_call(args.get("domain"), args.get("service"), args.get("entity_id"), args.get("data"))


tool("home_assistant_states", "List Home Assistant entities with their friendly names and current state. Filter by domain "
     "like light, switch, climate, media_player, sensor.", params({"domain": {"type": "string"}}), _states, group="home",
     changes_state=False)
tool("home_assistant", "Call a Home Assistant service, e.g. domain=light service=turn_on entity_id=light.bedroom "
     "data={\"brightness_pct\": 40}. Locks, alarms, covers / garage doors, scripts, automations and scenes always need "
     "their yes first (Jarvis asks); name devices by entity id.",
     params({"domain": {"type": "string"}, "service": {"type": "string"}, "entity_id": {"type": "string"},
             "data": {"type": "object", "description": "Extra service data, optional"}}, ["domain", "service", "entity_id"]),
     _call, group="home", claim="home", prepare=_prepare, risk_for=_risk, describe=_describe)
