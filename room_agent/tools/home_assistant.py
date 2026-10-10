"""Home Assistant: lights, plugs and other devices.

What Jarvis may ask Home Assistant to do is decided here, by what a call acts on (never by how the model phrased it):
    safe       everyday devices (lights, plugs, fans, media, climate...): done when asked
    sensitive  anything that secures or opens the home or runs other actions (locks, alarm panels, covers / garage
               doors, gates and valves, sirens, cameras, scripts, automations, scenes, buttons, helpers that switch
               automations), an entity listed in HA_SENSITIVE_ENTITIES, and any domain not known to be everyday:
               the executor asks first, every time, and only the exact call they said yes to runs
    refused    services that run code, reach other machines or reconfigure Home Assistant itself (shell_command,
               python_script, rest_command, mqtt.publish, notify, homeassistant.restart...): never from Jarvis
Targets must be explicit entity ids: area / device / label targets would act on things nobody classified.
"""

import re
import time

import requests

from room_agent import config
from room_agent.actions.core import Risk
from room_agent.config import HA_TOKEN, HA_URL

_ha_cache = {"t": 0.0, "ok": False, "detail": ""}
NAME = re.compile(r"^[a-z0-9_]{1,64}$")
ENTITY = re.compile(r"^[a-z0-9_]{1,64}\.[a-z0-9_]{1,128}$")
SAFE_DOMAINS = {"light", "switch", "fan", "media_player", "climate", "humidifier", "vacuum", "remote", "timer", "counter",
                "tts"}
SENSITIVE_DOMAINS = {"lock", "alarm_control_panel", "cover", "valve", "siren", "camera", "script", "automation", "scene",
                     "button", "input_button", "input_boolean", "input_select", "input_number", "input_text", "select",
                     "number", "water_heater", "lawn_mower", "garage_door", "gate", "door"}
REFUSED_DOMAINS = {"shell_command", "python_script", "pyscript", "rest_command", "hassio", "recorder", "system_log",
                   "logger", "backup", "frontend", "lovelace", "auth", "person", "zone", "notify", "persistent_notification",
                   "cloud", "conversation", "mqtt", "zha", "zwave_js", "esphome", "update", "device_tracker", "template",
                   "group", "config", "homeassistant_alerts", "ffmpeg", "downloader", "command_line", "hue", "deconz"}
GENERIC = {"homeassistant": {"turn_on", "turn_off", "toggle"}}  # (acts on any domain: judged by its entities)
TARGET_KEYS = {"entity_id", "area_id", "device_id", "floor_id", "label_id", "target"}


class Refused(Exception):
    def say(self):
        return str(self)


def targets(entity_id):
    """'light.a, light.b' / ['light.a'] / 'all' -> list of lower-case ids."""
    raw = entity_id if isinstance(entity_id, (list, tuple)) else re.split(r"[,\s]+", str(entity_id or ""))
    return [str(e).strip().lower() for e in raw if str(e).strip()]


def check_call(domain, service, entity_id, data=None):
    """-> (Risk, entity ids) for a service call, or raises Refused. The one place a Home Assistant call is judged."""
    domain, service = str(domain or "").strip().lower(), str(service or "").strip().lower()
    if not NAME.match(domain) or not NAME.match(service):
        raise Refused("that isn't a valid Home Assistant service name")
    if domain in REFUSED_DOMAINS or (domain in GENERIC and service not in GENERIC[domain]):
        raise Refused(f"Jarvis never calls {domain}.{service}: it runs code, reaches other systems or reconfigures Home "
                      "Assistant itself. Do that in Home Assistant directly.")
    if data is not None and not isinstance(data, dict):
        raise Refused("the extra service data must be an object")
    if TARGET_KEYS & set(data or {}):
        raise Refused("devices must be named by entity id (no area / device / label targets in the data)")
    ids = targets(entity_id)
    if not ids:
        raise Refused("which device (its entity id)?")
    for e in ids:
        if e != "all" and not ENTITY.match(e):
            raise Refused(f"'{e}' isn't a valid entity id")
    domains = {domain} if domain not in GENERIC else set()
    for e in ids:
        if e == "all":
            domains.add(domain if domain not in GENERIC else "all")
        else:
            domains.add(e.split(".", 1)[0])
    if domains & REFUSED_DOMAINS:
        raise Refused(f"Jarvis never acts on {', '.join(sorted(domains & REFUSED_DOMAINS))} entities")
    sensitive = (any(d not in SAFE_DOMAINS for d in domains)
                 or any(e in config.HA_SENSITIVE_ENTITIES for e in ids))
    return (Risk.SENSITIVE if sensitive else Risk.SAFE), ids


def describe_call(domain, service, entity_id, data=None):
    """For the confirmation question: exactly what would happen ('unlock lock.front_door')."""
    what = f"{str(service).replace('_', ' ')} {', '.join(targets(entity_id))}"
    if str(domain).lower() not in ("homeassistant",) and str(domain).lower() not in str(entity_id).lower():
        what = f"{domain}: {what}"
    if data:
        what += " (" + ", ".join(f"{k}={v}" for k, v in list(data.items())[:4]) + ")"
    return what


def ha_headers():
    return {"Authorization": f"Bearer {HA_TOKEN}"}


def ha_status():
    """(available, detail) for Home Assistant, from a real request (cached for a minute)."""
    if not HA_URL or not HA_TOKEN:
        return False, "not set up: no Home Assistant address/token configured, so no lights, plugs or devices"
    if time.time() - _ha_cache["t"] > 60:
        try:
            r = requests.get(f"{HA_URL}/api/", headers=ha_headers(), timeout=3)
            ok = r.ok
            detail = "connected" if ok else f"configured but answering with an error (status {r.status_code})"
        except requests.RequestException:
            ok, detail = False, "configured but not reachable right now"
        _ha_cache.update(t=time.time(), ok=ok, detail=detail)
    return _ha_cache["ok"], _ha_cache["detail"]


def ha_states(domain=""):
    r = requests.get(f"{HA_URL}/api/states", headers=ha_headers(), timeout=10)
    r.raise_for_status()
    rows = [
        f"{s['entity_id']} ({s['attributes'].get('friendly_name', '')}): {s['state']}"
        for s in r.json()
        if not domain or s["entity_id"].startswith(domain + ".")
    ]
    return ("OK: " + "\n".join(rows[:100])) if rows else f"OK: no devices in domain '{domain}'."


def ha_entity_state(entity_id):
    r = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=ha_headers(), timeout=5)
    return r.json().get("state") if r.ok else None


def ha_call(domain, service, entity_id, data=None):
    """Call a service, then check the device actually ended up in the expected state. Whether it may run at all (and
    whether it needs a yes) is the executor's job, through check_call; refused services are refused here too."""
    try:
        _, ids = check_call(domain, service, entity_id, data)
    except Refused as e:
        return f"FAILED: not done: {e}."
    domain, service = domain.strip().lower(), service.strip().lower()
    entity_id = ids[0] if len(ids) == 1 else ids
    try:
        r = requests.post(
            f"{HA_URL}/api/services/{domain}/{service}",
            headers=ha_headers(),
            json={**(data or {}), "entity_id": entity_id},
            timeout=10,
        )
    except requests.RequestException as e:
        return f"FAILED: couldn't reach Home Assistant ({e.__class__.__name__})."
    if not r.ok:
        return f"FAILED: Home Assistant refused it (status {r.status_code}: {r.text[:150]})."
    expected = {"turn_on": "on", "turn_off": "off", "lock": "locked", "unlock": "unlocked",
                "open_cover": "open", "close_cover": "closed"}.get(service)
    if not expected or not isinstance(entity_id, str) or entity_id == "all":
        return f"OK: Home Assistant accepted {domain}.{service} for {', '.join(ids)}."
    seen = None
    for _ in range(10):  # devices can take a moment to report back
        seen = ha_entity_state(entity_id)
        if seen == expected:
            return f"OK: Home Assistant confirms {entity_id} is now {seen}."
        time.sleep(0.3)
    return f"FAILED: Home Assistant accepted the command but {entity_id} is still '{seen}', not '{expected}'."
