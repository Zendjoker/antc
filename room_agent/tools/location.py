"""Where you are right now: Windows' own location (Wi-Fi / GPS / network, the same as the Maps app), else the city of your
internet connection. Turned into a city name with OpenStreetMap. Used for weather, local time and "near me".

Privacy: exact coordinates stay in memory only (never logged, saved or put in front of the model); Jarvis works with
the city. LOCATION_SOURCE=off in .env turns it off; =windows or =ip uses only that source.
"""

import asyncio
import logging
import threading
import time

import requests

from room_agent import config

log = logging.getLogger("room-agent")
MAX_AGE_S = 600       # look again after 10 minutes (you may have moved)
_lock = threading.Lock()
_cache = {"loc": None, "at": 0.0, "tried": 0.0}
_names = {}           # (lat, lon rounded) -> place name (so moving around a city doesn't re-ask OpenStreetMap)
UA = {"User-Agent": "Jarvis-room-agent/1.0 (personal voice assistant)"}
SOURCES = {0: "cellular", 1: "GPS", 2: "Wi-Fi", 3: "network", 4: "unknown", 5: "default", 6: "approximate"}


def enabled():
    return config.LOCATION_SOURCE != "off"


def _windows():
    """Windows' location (needs Location on, and "let desktop apps access your location")."""
    from winrt.windows.devices.geolocation import GeolocationAccessStatus, Geolocator

    async def run():
        if await Geolocator.request_access_async() != GeolocationAccessStatus.ALLOWED:
            return None
        g = Geolocator()
        g.desired_accuracy_in_meters = 100
        pos = await asyncio.wait_for(g.get_geoposition_async(), 10)
        c = pos.coordinate
        return {"lat": c.point.position.latitude, "lon": c.point.position.longitude, "accuracy_m": round(c.accuracy or 0),
                "source": "Windows location (" + SOURCES.get(int(c.position_source), "unknown") + ")"}

    out = {}
    t = threading.Thread(target=lambda: out.update(v=asyncio.run(run())), daemon=True)
    t.start()
    t.join(15)
    return out.get("v")


def _ip():
    """The city of your internet connection (rough: often the provider's nearest hub)."""
    r = requests.get("https://ipinfo.io/json", timeout=8, headers=UA)
    d = r.json() if r.status_code == 200 else {}
    if not d.get("loc"):
        return None
    lat, lon = (float(x) for x in d["loc"].split(","))
    return {"lat": lat, "lon": lon, "accuracy_m": 25000, "source": "internet connection",
            "city": d.get("city", ""), "region": d.get("region", ""), "country": d.get("country", "")}


def _name(loc):
    """City / region / country for these coordinates (OpenStreetMap, at city level)."""
    key = (round(loc["lat"], 2), round(loc["lon"], 2))
    if key not in _names:
        r = requests.get("https://nominatim.openstreetmap.org/reverse", timeout=8, headers=UA,
                         params={"lat": key[0], "lon": key[1], "format": "jsonv2", "zoom": 10, "addressdetails": 1})
        # (only a rounded position, about 1 km, leaves the PC: enough to name the city)
        a = (r.json() or {}).get("address", {}) if r.status_code == 200 else {}
        _names[key] = {"city": a.get("city") or a.get("town") or a.get("village") or a.get("municipality") or a.get("county", ""),
                       "region": a.get("state", ""), "country": a.get("country_code", "").upper()}
    return _names[key]


PHONE_FRESH_S = 900  # a position from your iPhone counts for 15 minutes (you're out, the PC's location is home)
_phone = {"loc": None}


def set_phone(lat, lon, accuracy_m=20):
    """Your iPhone's GPS (phone mode: Shortcuts sends it). While fresh, it wins over the PC's location."""
    loc = {"lat": float(lat), "lon": float(lon), "accuracy_m": accuracy_m, "source": "your iPhone's GPS"}
    try:
        loc.update(_name(loc))
    except Exception as e:
        log.info("couldn't name the place (%s)", e.__class__.__name__)
    loc["at"] = time.time()
    _phone["loc"] = loc
    log.info("location from the phone: %s", describe(loc))
    return loc


def locate(refresh=False):
    """{"city", "region", "country", "lat", "lon", "accuracy_m", "source", "at"} or None. Cached for MAX_AGE_S."""
    if not enabled():
        return None
    phone = _phone["loc"]
    if phone and time.time() - phone["at"] < PHONE_FRESH_S:
        return phone
    with _lock:
        if _cache["loc"] and not refresh and time.time() - _cache["at"] < MAX_AGE_S:
            return _cache["loc"]
        if not refresh and time.time() - _cache["tried"] < 60 and not _cache["loc"]:
            return None  # (just failed: don't hammer anything)
        _cache["tried"] = time.time()
    loc = None
    for source in (["windows", "ip"] if config.LOCATION_SOURCE == "auto" else [config.LOCATION_SOURCE]):
        try:
            loc = _windows() if source == "windows" else _ip()
        except Exception as e:
            log.info("location from %s unavailable (%s)", source, e.__class__.__name__)
            loc = None
        if loc:
            break
    if not loc:
        return _cache["loc"]  # (keep the last known place if a refresh fails)
    if not loc.get("city"):
        try:
            loc.update(_name(loc))
        except Exception as e:
            log.info("couldn't name the place (%s)", e.__class__.__name__)
    loc["at"] = time.time()
    with _lock:
        _cache["loc"], _cache["at"] = loc, loc["at"]
    log.info("location: %s (%s)", describe(loc), loc["source"])
    return loc


def describe(loc):
    """'Chicago, Illinois' (never coordinates)."""
    if not loc:
        return ""
    parts = [loc.get("city"), loc.get("region")] if loc.get("city") else [loc.get("region"), loc.get("country")]
    return ", ".join(p for p in parts if p) or "somewhere (couldn't name the place)"


def precision(loc):
    m = loc.get("accuracy_m") or 0
    return "very precise" if m and m <= 200 else f"within about {max(1, round(m / 1000))} km" if m else "rough"


def warm():
    """Find it in the background at startup, so the first weather question is instant."""
    if enabled():
        threading.Thread(target=locate, daemon=True).start()


def get_location():
    loc = locate()
    if not loc:
        if not enabled():
            return "UNAVAILABLE: location is turned off in the settings (LOCATION_SOURCE=off)."
        return ("FAILED: couldn't find where they are right now (Windows location is off or not allowed, and the internet "
                "lookup failed). Ask which city they're in if it matters.")
    return f"OK: {describe(loc)} ({precision(loc)}, from {loc['source']}). Don't read out coordinates."
