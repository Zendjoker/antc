"""Google Places, within its terms: https://developers.google.com/maps/documentation/places/web-service/policies

What the policy says (checked 2026-10-08): "You must not pre-fetch, cache, or store Places API content beyond the
allowed exceptions"; the place ID "is exempt from the caching restrictions" and may be stored indefinitely (Google
suggests refreshing ids older than 12 months). Places data shown without a Google Map must be attributed to
"Google Maps" (the logo where possible, the text where space is limited), with any third-party attributions Google
returns. (Customers billed in the EEA have different terms: not handled here; see MISSIONS.md.)

How Jarvis follows it:
    - Places content (name, address, phone, website, coordinates, type, business status) is held IN MEMORY only, for at
      most PLACES_MEMORY_TTL_S, for the research it was requested for. It is never written to missions.db, files,
      reports, exports, demo sites or outreach drafts.
    - What's persisted for a Google-found business: its place id, and facts CONFIRMED BY A NON-GOOGLE SOURCE (the same
      business on OpenStreetMap, or the business's own website showing that phone / address / name). Each such field
      records which source confirmed it (extra.verified_by).
    - When the memory copy has expired (or after a restart) and the content is needed again, it is fetched again with
      a Place Details request (paid, budget-checked) using the stored place id.
    - Anywhere Places content is shown live (the dashboard), it's labelled "Google Maps".
    - Third-party attributions: the field masks used here don't request reviews or photos, which are the fields that
      carry them.
"""

import logging
import threading
import time

from room_agent import config
from room_agent.missions import meter, net, runctx

log = logging.getLogger("room-agent")
ATTRIBUTION = "Google Maps"
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{}"
SEARCH_FIELDS = ("places.id,places.displayName,places.formattedAddress,places.location,places.nationalPhoneNumber,"
                 "places.internationalPhoneNumber,places.websiteUri,places.primaryTypeDisplayName,places.businessStatus,"
                 "nextPageToken")
DETAILS_FIELDS = ("id,displayName,formattedAddress,location,nationalPhoneNumber,internationalPhoneNumber,websiteUri,"
                  "primaryTypeDisplayName,businessStatus")
_mem = {}
_lock = threading.Lock()


class PlacesError(Exception):
    pass


def enabled():
    return bool(config.GOOGLE_PLACES_API_KEY)


def _content(p):
    loc = p.get("location") or {}
    return {"name": ((p.get("displayName") or {}).get("text") or "").strip(),
            "address": p.get("formattedAddress", ""),
            "phone": p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber") or "",
            "website": p.get("websiteUri", ""),
            "category": (p.get("primaryTypeDisplayName") or {}).get("text", ""),
            "status": p.get("businessStatus", ""),
            "lat": loc.get("latitude"), "lon": loc.get("longitude"), "attribution": ATTRIBUTION}


def remember(place_id, content):
    with _lock:
        _mem[place_id] = (time.time() + config.PLACES_MEMORY_TTL_S, dict(content))
        for k in [k for k, (exp, _) in _mem.items() if exp < time.time()]:
            _mem.pop(k, None)


def recall(place_id):
    """The in-memory content for a place, or None (expired / never fetched in this run)."""
    with _lock:
        hit = _mem.get(place_id)
        if hit and hit[0] >= time.time():
            return dict(hit[1])
        _mem.pop(place_id, None)
        return None


def forget_all():
    with _lock:
        _mem.clear()


def _request(method, url, body=None, field_mask="", cost=0.0, what=""):
    """One paid Places request inside the mission budget (reserve -> send -> settle)."""
    try:
        return _send(method, url, body, field_mask, cost, what)
    except meter.NotSent as e:  # (released: nothing was charged)
        raise PlacesError(f"Google Places wasn't reached ({e})")


def _send(method, url, body, field_mask, cost, what):
    if not enabled():
        raise PlacesError("no GOOGLE_PLACES_API_KEY set")
    headers = {"X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY, "X-Goog-FieldMask": field_mask}
    with meter.paid(cost, what, provider="google_places", daily=False) as charge:
        if method == "POST":
            r = net.post(url, json_body=body, headers=headers, timeout=30)
        else:
            r = net.get(url, headers=headers, timeout=30, respect_robots=False)
            r.sent = True if r.status else (False if r.error.startswith("not fetched") else None)
        if r.sent is False:
            raise meter.NotSent(r.error or "not sent")
        if r.status and 400 <= r.status < 500:
            charge.not_billed()  # (rejected: invalid request / key / quota; Google doesn't bill these)
            raise PlacesError(f"Google Places refused the request ({r.status})")
        if not r.ok:  # (server error / timeout / dropped connection: counted at the full estimate, see meter.paid)
            raise PlacesError(f"Google Places didn't answer properly ({r.error})")
        charge.actual(cost)  # (Places has no usage numbers in the response: the configured per-request price)
    runctx.check()
    try:
        return r.json()
    except ValueError:
        raise PlacesError("Google Places answered something that isn't JSON")


def text_search(query, limit=60, pages=3):
    """-> [(place_id, content)] for a text query; content is kept in memory only."""
    out, token, n = [], None, 0
    while len(out) < limit and n < pages:
        body = {"textQuery": query, "pageSize": 20, "languageCode": "en"}
        if token:
            body["pageToken"] = token
        data = _request("POST", SEARCH_URL, body, SEARCH_FIELDS, config.PLACES_COST_PER_REQUEST, "Google Places search")
        n += 1
        for p in data.get("places", []):
            if not p.get("id"):
                continue
            c = _content(p)
            remember(p["id"], c)
            out.append((p["id"], c))
        token = data.get("nextPageToken")
        if not token:
            break
    return out[:limit]


def content(place_id):
    """The place's content: from memory, else a Place Details request (paid). -> dict, or None if it can't be had."""
    hit = recall(place_id)
    if hit is not None:
        return hit
    if not enabled():
        return None
    data = _request("GET", DETAILS_URL.format(place_id), None, DETAILS_FIELDS, config.PLACES_DETAILS_COST_PER_REQUEST,
                    "Google Place Details")
    c = _content(data)
    remember(place_id, c)
    return c
