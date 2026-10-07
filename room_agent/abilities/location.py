"""Where you are right now (implementation: tools/location.py). City level for the model; coordinates never shown."""

from room_agent.abilities._kit import NO_ARGS, tool
from room_agent.actions.core import Group, register_context, register_group


def _here():
    from room_agent.tools import location

    return location.locate() if location.enabled() else None


def _summary():
    from room_agent.tools import location

    if not location.enabled():
        return "turned off in the settings"
    loc = _here()
    if not loc:
        return "couldn't find it right now (Windows location off and the internet lookup failed); ask for a city if needed"
    return f"{location.describe(loc)}, {location.precision(loc)}, from {loc['source']}; used for weather and local questions"


def _enabled():
    from room_agent.tools import location

    return location.enabled()


register_group(Group("location", title="knowing where they are right now", summary=_summary, available=_enabled))


def _context(user_text):
    from room_agent.tools import location

    loc = _here()
    if not loc:
        return []
    return [f"- location_now: {location.describe(loc)} ({location.precision(loc)}, live from {loc['source']}). Use it for "
            "weather, local time and 'near me' without asking; never read out coordinates or say how you know unless asked."]


register_context(_context, order=15)


def _get(args):
    from room_agent.tools.location import get_location

    return get_location()


tool("get_location", "Where they are right now (city), detected from Windows location or their internet connection: "
     "'where am I?'.", NO_ARGS, _get, group="location", changes_state=False)
