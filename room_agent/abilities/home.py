"""Smart home through Home Assistant (implementation: tools/home_assistant.py)."""

from room_agent.abilities._kit import params, tool
from room_agent.actions.core import Group, register_claim, register_group


def _status():
    from room_agent.tools.home_assistant import ha_status

    return ha_status()


register_group(Group("home", title="smart home control (Home Assistant)", summary=lambda: _status()[1],
                     available=lambda: _status()[0], rules=[
    "- For smart home requests, look up entity ids with home_assistant_states if you don't already know them."]))
register_claim("home", r"\b(turned|switched|shut|flipped|set)\b.{0,40}\b(on|off)\b(?!\s+(monitor|screen|display|your|my|the|a|this|that)\b)"
                       r"|\b(lights?|lamps?|fan|heater|tv|plug|switch|thermostat|ac|heat(ing)?)\b.{0,30}\b(are|is)\s+(now\s+)?(on|off)\b"
                       r"|\b(dimmed|brightened)\b.{0,30}\b(lights?|lamps?)\b"
                       r"|\b(locked|unlocked)\b.{0,25}\b(door|doors|lock|garage|gate)\b|\b(door|doors|garage|gate)\b.{0,25}\b(locked|unlocked)\b")


def _states(args):
    from room_agent.tools.home_assistant import ha_states

    return ha_states(args.get("domain", ""))


def _call(args):
    from room_agent.tools.home_assistant import ha_call

    return ha_call(args["domain"], args["service"], args["entity_id"], args.get("data"))


tool("home_assistant_states", "List Home Assistant entities with their friendly names and current state. Filter by domain "
     "like light, switch, climate, media_player, sensor.", params({"domain": {"type": "string"}}), _states, group="home",
     changes_state=False)
tool("home_assistant", "Call a Home Assistant service, e.g. domain=light service=turn_on entity_id=light.bedroom "
     "data={\"brightness_pct\": 40}.",
     params({"domain": {"type": "string"}, "service": {"type": "string"}, "entity_id": {"type": "string"},
             "data": {"type": "object", "description": "Extra service data, optional"}}, ["domain", "service", "entity_id"]),
     _call, group="home", claim="home")
