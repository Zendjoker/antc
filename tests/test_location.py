"""Knowing where you are: Windows location first, then the internet connection's city; cached; turned off cleanly;
used by the weather; city-level only in front of the model and in the logs. All lookups are faked (no network).

Run:  .venv\\Scripts\\python -m tests.test_location
"""

import io
import logging
import time

from tests.harness import setup_env

TMP = setup_env(LOCATION_SOURCE="auto")

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.tools import location, weather  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
LOG = io.StringIO()
logging.basicConfig(level=logging.INFO, stream=LOG, format="%(message)s")
CALLS = []
WIN = {"ok": True}
IP = {"ok": True}
EXACT = (41.87814, -87.62979)  # (made-up precise coordinates: must never show up anywhere)


def fake_windows():
    CALLS.append("windows")
    if not WIN["ok"]:
        raise PermissionError("location off")
    return {"lat": EXACT[0], "lon": EXACT[1], "accuracy_m": 40, "source": "Windows location (Wi-Fi)"}


def fake_ip():
    CALLS.append("ip")
    if not IP["ok"]:
        return None
    return {"lat": 41.85, "lon": -87.65, "accuracy_m": 25000, "source": "internet connection", "city": "Chicago",
            "region": "Illinois", "country": "US"}


def fake_name(loc):
    CALLS.append("name")
    return {"city": "Chicago", "region": "Illinois", "country": "US"}


location._windows, location._ip, location._name = fake_windows, fake_ip, fake_name


def reset():
    CALLS.clear()
    location._cache.update(loc=None, at=0.0, tried=0.0)


print("finding the place:")
reset()
loc = location.locate()
t.check("Windows location first, named by OpenStreetMap", location.describe(loc) == "Chicago, Illinois"
        and CALLS == ["windows", "name"], CALLS)
t.check("precise fixes are described as precise", location.precision(loc) == "very precise")
CALLS.clear()
location.locate()
t.check("cached: no second lookup within 10 minutes", CALLS == [], CALLS)
location._cache["at"] = time.time() - location.MAX_AGE_S - 1
location.locate()
t.check("looked up again after 10 minutes (you may have moved)", "windows" in CALLS, CALLS)
reset()
WIN["ok"] = False
loc = location.locate()
t.check("Windows location off -> the internet connection's city", location.describe(loc) == "Chicago, Illinois"
        and CALLS == ["windows", "ip"] and "within about 25 km" == location.precision(loc), CALLS)
reset()
IP["ok"] = False
t.check("both fail -> no location (nothing invented)", location.locate() is None)
t.check("...'where am I' says it couldn't find it", location.get_location().startswith("FAILED"))
CALLS.clear()
location.locate()
t.check("...and doesn't hammer the lookups right after failing", CALLS == [], CALLS)
WIN["ok"] = IP["ok"] = True
reset()
config.LOCATION_SOURCE = "off"
t.check("turned off: no lookups at all", location.locate() is None and CALLS == [])
t.check("...'where am I' says it's turned off", location.get_location().startswith("UNAVAILABLE"))
config.LOCATION_SOURCE = "ip"
reset()
location.locate()
t.check("LOCATION_SOURCE=ip uses only the internet lookup", CALLS == ["ip"], CALLS)
config.LOCATION_SOURCE = "auto"
reset()

print("weather uses it:")
SEEN = {}


def fake_get(url, params=None, timeout=None, headers=None):
    class R:
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body
    if "geocoding" in url:
        SEEN["geocoded"] = params["name"]
        return R({"results": [{"latitude": 40.7, "longitude": -74.0, "name": "New York", "admin1": "New York"}]})
    SEEN["forecast_at"] = (params["latitude"], params["longitude"])
    return R({"current": {"temperature_2m": 61, "apparent_temperature": 60, "weather_code": 1, "wind_speed_10m": 5,
                          "relative_humidity_2m": 50},
              "daily": {"time": ["2026-10-06", "2026-10-07"], "weather_code": [1, 3], "temperature_2m_max": [65, 60],
                        "temperature_2m_min": [50, 48], "precipitation_probability_max": [5, 30]}})


weather.requests.get = fake_get
out = weather.get_weather("")
t.check("'what's the weather' -> where they are now, no city asked", out.startswith("OK: Chicago, Illinois (where they are now)")
        and SEEN["forecast_at"] == EXACT and "geocoded" not in SEEN, out[:80])
out = weather.get_weather("New York")
t.check("a named city still wins", out.startswith("OK: New York, New York") and SEEN["geocoded"] == "New York")

print("privacy and what the model sees:")
from room_agent.prompt import runtime_context  # noqa: E402

rt.turn_text = "what's the weather"
ctx = runtime_context("what's the weather")
t.check("the model is told the city, live", "location_now: Chicago, Illinois" in ctx)
t.check("'where they are' is available in the capability list", "knowing where they are right now (Chicago, Illinois" in ctx)
t.check("no 'location unknown, ask for a city' line anymore", "their_location: unknown" not in ctx)
everything = ctx + LOG.getvalue() + location.get_location()
t.check("exact coordinates never reach the model, the logs or the reply", "41.87" not in everything and "87.62" not in everything)
t.done("LOCATION TESTS")
