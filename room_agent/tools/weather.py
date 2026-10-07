"""Weather from Open-Meteo."""

import datetime
import re

import requests

from room_agent import runtime as rt
from room_agent.config import UNITS, WEATHER_LOCATION

WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
    81: "showers", 82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


def preferred_units():
    stored = rt.memory.get("units").lower() if rt.memory.available else ""
    if stored:
        return "metric" if re.search(r"metric|celsius|\bc\b|km", stored) else "imperial"
    return UNITS


def get_weather(location=""):
    """A named city, else where they are right now (detected), else their stored home city / the settings' city."""
    from room_agent.tools import location as here

    stored = (rt.memory.home_location() if rt.memory.available else "") or WEATHER_LOCATION
    if not str(location or "").strip():
        now = here.locate()
        if now:
            return forecast({"latitude": now["lat"], "longitude": now["lon"], "name": here.describe(now), "admin1": ""},
                            "where they are now")
    location = (location or stored or rt.session_location).strip()
    if not location:
        return ("UNAVAILABLE: no city given, none stored, and their location couldn't be detected right now. "
                "Ask once which city they're in; don't guess.")
    g = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location.split(",")[0].strip(), "count": 1},
        timeout=10,
    ).json()
    if not g.get("results"):
        return f"FAILED: couldn't find a place called {location}."
    place = g["results"][0]
    if not stored:
        rt.session_location = location  # asked once: the next "and tomorrow?" uses it without asking again
    return forecast(place)


def forecast(place, note=""):
    imperial = preferred_units() == "imperial"
    w = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "timezone": "auto",
            "forecast_days": 2,
            "temperature_unit": "fahrenheit" if imperial else "celsius",
            "wind_speed_unit": "mph" if imperial else "kmh",
        },
        timeout=10,
    ).json()
    c, d = w["current"], w["daily"]
    deg, spd = ("F", "mph") if imperial else ("C", "km/h")
    days = [
        f"{name}: {WMO.get(d['weather_code'][i], 'unknown')}, high {d['temperature_2m_max'][i]:.0f}{deg}, "
        f"low {d['temperature_2m_min'][i]:.0f}{deg}, {d['precipitation_probability_max'][i]}% chance of rain"
        for i, name in enumerate(
            f"{label} ({datetime.date.fromisoformat(d['time'][j]).strftime('%A')})" for j, label in enumerate(["Today", "Tomorrow"])
        )
    ]
    where = ", ".join(p for p in (place["name"], place.get("admin1", "")) if p) + (f" ({note})" if note else "")
    return (
        f"OK: {where}: now {WMO.get(c['weather_code'], 'unknown')}, "
        f"{c['temperature_2m']:.0f}{deg} (feels like {c['apparent_temperature']:.0f}{deg}), "
        f"wind {c['wind_speed_10m']:.0f} {spd}, humidity {c['relative_humidity_2m']}%. " + ". ".join(days)
    )
