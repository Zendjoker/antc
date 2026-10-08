"""Finding businesses of a kind in an area, from real public data sources.

    OpenStreetMap (default, free)   Nominatim turns the place name into coordinates; Overpass lists the businesses with
                                    the category's OSM tag around it (name, address, phone, website, email, hours).
                                    Data (c) OpenStreetMap contributors, ODbL: reports and demo sites credit it.
    Google Places (optional)        Text Search (New) when GOOGLE_PLACES_API_KEY is set and the mission allows it; paid
                                    per request, inside the mission budget. Its content stays in memory only
                                    (missions/places.py): a Google-found business is persisted as its place id plus
                                    whatever a non-Google source confirms. OpenStreetMap is always queried as well, to
                                    confirm Google-found businesses (same phone, or same name within ~150 m).

Nothing is invented: a business has exactly the fields a source returned; everything else is listed as missing.
"""

import logging
import math
import re
import time

from room_agent.missions import meter, net, places
from room_agent.missions.store import norm_phone, norm_text

log = logging.getLogger("room-agent")
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
# category words -> OpenStreetMap tags (key, value regex)
CATEGORIES = {
    "restaurant": [("amenity", "restaurant")],
    "cafe": [("amenity", "cafe")], "coffee shop": [("amenity", "cafe")],
    "bar": [("amenity", "bar|pub")], "pub": [("amenity", "pub")],
    "fast food": [("amenity", "fast_food")], "food truck": [("amenity", "fast_food")],
    "bakery": [("shop", "bakery")], "butcher": [("shop", "butcher")], "deli": [("shop", "deli")],
    "dentist": [("amenity", "dentist"), ("healthcare", "dentist")],
    "doctor": [("amenity", "doctors"), ("healthcare", "doctor")],
    "pharmacy": [("amenity", "pharmacy")], "veterinarian": [("amenity", "veterinary")],
    "hair salon": [("shop", "hairdresser")], "barber": [("shop", "hairdresser")],
    "beauty salon": [("shop", "beauty")], "nail salon": [("shop", "beauty")], "spa": [("leisure", "spa"), ("shop", "massage")],
    "gym": [("leisure", "fitness_centre")], "yoga studio": [("leisure", "fitness_centre"), ("sport", "yoga")],
    "plumber": [("craft", "plumber")], "electrician": [("craft", "electrician")], "carpenter": [("craft", "carpenter")],
    "painter": [("craft", "painter")], "roofer": [("craft", "roofer")], "locksmith": [("shop", "locksmith"), ("craft", "locksmith")],
    "auto repair": [("shop", "car_repair")], "car wash": [("amenity", "car_wash")], "bicycle shop": [("shop", "bicycle")],
    "lawyer": [("office", "lawyer")], "accountant": [("office", "accountant")], "real estate": [("office", "estate_agent")],
    "insurance": [("office", "insurance")],
    "florist": [("shop", "florist")], "clothing store": [("shop", "clothes")], "shoe store": [("shop", "shoes")],
    "jewelry store": [("shop", "jewelry")], "bookstore": [("shop", "books")], "furniture store": [("shop", "furniture")],
    "hardware store": [("shop", "hardware|doityourself")], "grocery": [("shop", "supermarket|convenience|greengrocer")],
    "laundry": [("shop", "laundry|dry_cleaning")], "tailor": [("shop", "tailor"), ("craft", "tailor")],
    "tattoo": [("shop", "tattoo")], "pet store": [("shop", "pet")], "pet grooming": [("shop", "pet_grooming")],
    "hotel": [("tourism", "hotel|guest_house|motel")], "photographer": [("craft", "photographer")],
    "optician": [("shop", "optician")], "childcare": [("amenity", "childcare|kindergarten")],
}
ALIASES = {"restaurants": "restaurant", "cafes": "cafe", "coffee": "cafe", "bars": "bar", "pubs": "pub",
           "bakeries": "bakery", "dentists": "dentist", "doctors": "doctor", "salons": "hair salon",
           "hairdressers": "hair salon", "barbers": "barber", "barbershop": "barber", "gyms": "gym",
           "plumbers": "plumber", "electricians": "electrician", "mechanics": "auto repair", "mechanic": "auto repair",
           "lawyers": "lawyer", "attorneys": "lawyer", "accountants": "accountant", "realtors": "real estate",
           "florists": "florist", "hotels": "hotel", "vets": "veterinarian", "vet": "veterinarian",
           "pizzeria": "restaurant", "pizza": "restaurant", "diner": "restaurant", "diners": "restaurant",
           "photographers": "photographer", "nail salons": "nail salon", "spas": "spa", "locksmiths": "locksmith"}
CUISINES = {"pizza", "pizzeria", "sushi", "mexican", "thai", "chinese", "indian", "italian", "vietnamese", "korean",
            "japanese", "burger", "ramen", "taqueria", "mediterranean", "greek", "french", "vegan", "seafood", "bbq"}


class DiscoveryError(Exception):
    pass


def resolve_category(text):
    """'restaurants' / 'pizza places' / 'tag:shop=bakery' -> (label, [(osm key, value regex)], cuisine or '')."""
    t = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    m = re.fullmatch(r"tag:([a-z_:]+)=([a-z_|]+)", t)
    if m:
        return t, [(m.group(1), m.group(2))], ""
    cuisine = next((c for c in sorted(CUISINES) if re.search(rf"\b{c}\b", t)), "")
    if cuisine:
        return f"{cuisine} restaurant", CATEGORIES["restaurant"], {"pizzeria": "pizza", "taqueria": "mexican"}.get(cuisine, cuisine)
    short = re.sub(r"\b(places?|businesses|joints?|spots?)\b", "", t).strip() or t
    for cand in (t, short, short[:-1] if short.endswith("s") else short,
                 re.sub(r"\b(shops?|stores?)\b", "", short).strip()):
        cand = ALIASES.get(cand, cand)
        if cand in CATEGORIES:
            return cand, CATEGORIES[cand], ""
        if cand.endswith("s") and cand[:-1] in CATEGORIES:
            return cand[:-1], CATEGORIES[cand[:-1]], ""
    return t, [], ""


def geocode(place, cancel=None):
    """-> {lat, lon, name, bbox, osm_url} for a city / neighbourhood / address (Nominatim, cached 30 days)."""
    from room_agent.missions.store import store

    key = "geocode:" + norm_text(place)
    hit = store().cache_get(key, 30 * 86400)
    if hit:
        return hit
    r = net.get(NOMINATIM, params={"q": place, "format": "jsonv2", "limit": 1, "addressdetails": 1}, respect_robots=False,
                cancel=cancel, timeout=20)
    meter.count_request("Nominatim geocode")
    if not r.ok:
        raise DiscoveryError(f"couldn't look up '{place}' on OpenStreetMap: {r.error}")
    rows = r.json()
    if not rows:
        raise DiscoveryError(f"OpenStreetMap doesn't know a place called '{place}'")
    row = rows[0]
    bb = [float(x) for x in row.get("boundingbox", [])] or None
    out = {"lat": float(row["lat"]), "lon": float(row["lon"]), "name": row.get("display_name", place),
           "bbox": bb, "osm_url": f"https://www.openstreetmap.org/{row.get('osm_type', 'node')}/{row.get('osm_id', '')}",
           "city": (row.get("address") or {}).get("city") or (row.get("address") or {}).get("town") or place}
    store().cache_put(key, out)
    return out


def _overpass_query(tags, lat, lon, radius_m, cuisine, limit):
    parts = []
    for k, v in tags:
        sel = f'["{k}"~"^({v})$"]["name"]'
        if cuisine:
            sel += f'["cuisine"~"{cuisine}",i]'
        parts.append(f"nwr{sel}(around:{int(radius_m)},{lat:.6f},{lon:.6f});")
    return f"[out:json][timeout:60];({''.join(parts)});out center tags {int(limit)};"


def _address(tags):
    street = " ".join(x for x in (tags.get("addr:housenumber", ""), tags.get("addr:street", "")) if x)
    unit = tags.get("addr:unit", "")
    city = tags.get("addr:city", "")
    rest = ", ".join(x for x in (city, " ".join(y for y in (tags.get("addr:state", ""), tags.get("addr:postcode", "")) if y)) if x)
    return ", ".join(x for x in (street + (f" #{unit}" if unit else ""), rest) if x), city


def _distance_km(a_lat, a_lon, b_lat, b_lon):
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def from_osm(category, place, radius_km=3.0, limit=150, cancel=None):
    """-> (center, [profile]) from OpenStreetMap. Raises DiscoveryError when nothing could be asked."""
    label, tags, cuisine = resolve_category(category)
    if not tags:
        raise DiscoveryError(f"I don't know the OpenStreetMap tag for '{category}'. Use a common category (restaurant, "
                             "cafe, dentist, hair salon, plumber...), 'tag:key=value', or add a Google Places key.")
    center = geocode(place, cancel)
    radius_m = max(200, min(float(radius_km) * 1000, 25000))
    q = _overpass_query(tags, center["lat"], center["lon"], radius_m, cuisine, limit)
    last = ""
    for url in OVERPASS:
        r = net.post(url, data={"data": q}, timeout=90, cancel=cancel)
        meter.count_request("Overpass query")
        if r.ok:
            break
        last = r.error
        log.info("overpass %s failed: %s", url, r.error)
    else:
        raise DiscoveryError(f"OpenStreetMap's Overpass service didn't answer ({last}); try again later")
    try:
        elements = r.json().get("elements", [])
    except ValueError:
        raise DiscoveryError("Overpass answered something that isn't JSON (probably overloaded); try again later")
    fetched = time.strftime("%Y-%m-%d")
    out = []
    for el in elements:
        t = el.get("tags") or {}
        name = (t.get("name") or "").strip()
        if not name:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        address, city = _address(t)
        osm_url = f"https://www.openstreetmap.org/{el['type']}/{el['id']}"
        website = (t.get("website") or t.get("contact:website") or t.get("url") or "").strip()
        if website and not re.match(r"https?://", website, re.I):
            website = "http://" + website
        profile = {
            "key": f"osm:{el['type']}/{el['id']}", "name": name, "category": label,
            "address": address, "city": city or center.get("city", place), "lat": lat, "lon": lon,
            "phone": (t.get("phone") or t.get("contact:phone") or "").split(";")[0].strip(),
            "website": website, "email": (t.get("email") or t.get("contact:email") or "").split(";")[0].strip(),
            "sources": [osm_url],
            "extra": {"osm": {"id": f"{el['type']}/{el['id']}", "fetched": fetched},
                      "cuisine": t.get("cuisine", ""), "opening_hours": t.get("opening_hours", ""),
                      "social": {k.split(":", 1)[1]: v for k, v in t.items() if k.startswith("contact:") and
                                 k.split(":", 1)[1] in ("facebook", "instagram", "twitter", "tiktok", "yelp")},
                      "distance_km": round(_distance_km(center["lat"], center["lon"], lat, lon), 2) if lat and lon else None,
                      "disused": bool(t.get("disused") or t.get("abandoned") or t.get("opening_hours") == "closed")},
        }
        out.append(profile)
    out.sort(key=lambda p: (p["extra"]["distance_km"] is None, p["extra"]["distance_km"] or 0))
    return center, out


def _similar(a, b):
    ta, tb = set(norm_text(a).split()) - {"the", "and", "restaurant", "cafe", "bar"}, set(norm_text(b).split()) - {
        "the", "and", "restaurant", "cafe", "bar"}
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.6 or norm_text(a) in norm_text(b) or norm_text(b) in norm_text(a)


def _match_osm(c, osm):
    """The OpenStreetMap business that is this Google place: same phone, or a similar name within ~150 m."""
    phone = norm_phone(c.get("phone"))
    for p in osm:
        if phone and phone == norm_phone(p.get("phone")):
            return p
    if c.get("lat") is None:
        return None
    for p in osm:
        if p.get("lat") and _similar(c["name"], p["name"]) and _distance_km(c["lat"], c["lon"], p["lat"], p["lon"]) < 0.15:
            return p
    return None


def _google_profile(pid, c, osm, used, category, place):
    """-> the persistable profile for one Google result (None for a closed business)."""
    if c.get("status") in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
        return None  # (used, not stored)
    m = _match_osm(c, osm)
    if m is not None and m["key"] not in used:
        used.add(m["key"])
        prof = dict(m)
        prof["place_id"] = pid
        prof["extra"] = {**m["extra"], "places": {"id": pid}, "google_match": "matched on OpenStreetMap"}
        return prof
    # (only our own search terms and the place id are stored; provenance says so)
    return {"key": f"gplaces:{pid}", "place_id": pid, "name": f"Google place {pid[:10]}", "category": category,
            "city": place, "sources": [],
            "extra": {"places": {"id": pid}, "google_only": True,
                      "verified_by": {"category": "mission search terms", "city": "mission search terms"}}}


def places_query(category, place):
    return f"{category} in {place}"


def from_places(category, place, osm, limit=60, on_batch=None, start_token=None, pages=3):
    """-> ([persistable profile], used OSM keys) for Google-found businesses. Matched to OpenStreetMap: the OSM profile
    (OSM fields, OSM source) plus the place id. Not matched: only the place id and our own search terms; the Google
    content stays in memory (places.recall) until a non-Google source confirms it during research.
    on_batch(profiles, progress) runs after every paid page, before the next one is bought."""
    out, used = [], set()

    def page_done(page, n, complete):
        batch = [x for x in (_google_profile(pid, c, osm, used, category, place) for pid, c in page) if x]
        out.extend(batch)
        if on_batch is not None:
            on_batch(batch, {"places_pages": n, "places_complete": complete})

    places.text_search(places_query(category, place), limit=limit, pages=pages, start_token=start_token,
                       on_page=page_done)
    return out, used


def discover(category, place, radius_km=3.0, pool=60, use_places="auto", cancel=None, on_batch=None, resume=None):
    """-> (center or None, [persistable profile], notes). OpenStreetMap always (free); Google Places too when allowed.
    on_batch(profiles, progress) receives each batch as soon as it's known (OpenStreetMap first, then every paid
    Places page), so the caller can checkpoint it. resume: the progress saved by an earlier, cut-off run: Places pages
    already bought are never bought again (the search continues only from a still-fresh in-memory page token)."""
    notes = []
    resume = resume or {}
    want_places = use_places is True or (use_places == "auto" and places.enabled())
    center, osm, osm_error = None, [], None
    try:
        center, osm = from_osm(category, place, radius_km, limit=max(pool * 2, 60), cancel=cancel)
        osm = [p for p in osm if not p["extra"].get("disused")]
        notes.append(f"OpenStreetMap: {len(osm)} businesses within {radius_km:g} km of {center['name'][:80]}")
        if on_batch is not None:
            on_batch(osm[:pool], {"osm_done": True})
    except DiscoveryError as e:
        osm_error = e
        notes.append(f"OpenStreetMap not used: {e}")
    found = []
    pages_done = int(resume.get("places_pages") or 0)
    if want_places and resume.get("places_complete"):
        notes.append(f"Google Places: already searched ({pages_done} page(s)); not searched again")
    elif want_places:
        start, pages_left = None, 3
        if pages_done:
            start = places.next_token(places_query(category, place))
            pages_left = 3 - pages_done
            if start is None or pages_left <= 0:
                notes.append(f"Google Places: the search stopped after {pages_done} paid page(s) and can't be continued "
                             "without buying them again, so it wasn't repeated")
                if on_batch is not None:
                    on_batch([], {"places_pages": pages_done, "places_complete": True})
                pages_left = 0
        if pages_left > 0:
            try:
                g, used = from_places(category, place, osm, limit=pool, on_batch=on_batch, start_token=start,
                                      pages=pages_left)
                matched = sum(1 for p in g if not p["key"].startswith("gplaces:"))
                notes.append(f"Google Places: {len(g)} businesses ({matched} confirmed on OpenStreetMap; the rest are kept "
                             "as place ids until their own website confirms them)")
                found += g
                osm = [p for p in osm if p["key"] not in used]
            except meter.BudgetExceeded:
                raise
            except places.PlacesError as e:
                notes.append(f"Google Places not used: {e}")
    found += osm[:pool]
    if not found and osm_error is not None and not pages_done:
        raise osm_error
    return center, found, notes
