"""Home Assistant: lights, plugs and other devices."""

import time

import requests

from room_agent.config import HA_TOKEN, HA_URL

_ha_cache = {"t": 0.0, "ok": False, "detail": ""}


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
    """Call a service, then check the device actually ended up in the expected state."""
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
    if not expected:
        return f"OK: Home Assistant accepted {domain}.{service} for {entity_id}."
    seen = None
    for _ in range(10):  # devices can take a moment to report back
        seen = ha_entity_state(entity_id)
        if seen == expected:
            return f"OK: Home Assistant confirms {entity_id} is now {seen}."
        time.sleep(0.3)
    return f"FAILED: Home Assistant accepted the command but {entity_id} is still '{seen}', not '{expected}'."
