"""A fake local world for business-mission tests and the mission benchmark: no real network, no real businesses.

    a town ("Testville") on a fake Nominatim, businesses on a fake Overpass (only those within the asked radius),
    their websites (good / poor / broken / none), a fake web search, robots.txt, per-request latency, and injectable
    failures (a web search that fails once, a demo build that fails its checks)

Everything goes through the real code above the two network seams (netguard.resolve_public / netguard.http) and the
search seam (tools.web.search): net.py's pacing, robots handling, size limits and the mission engine all run as is.
Names, phones and addresses are invented (555 numbers, .example domains).
"""

import json
import math
import re
import threading
import time
import urllib.parse

CENTER = (40.0, -75.0)
KINDS20 = (["none"] * 8 + ["social"] * 3 + ["ok"] * 3 + ["poor"] * 2 + ["broken"] * 2 + ["found"] * 2)
QUALIFYING = ("none", "social", "broken")  # (website_filter "none": no site of their own, or a broken one)


class Resp:
    def __init__(self, status=200, body=b"", headers=None, url=""):
        from requests.structures import CaseInsensitiveDict

        self.status_code, self.url, self.encoding = status, url, "utf-8"
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.headers = CaseInsensitiveDict(headers or {"content-type": "text/html; charset=utf-8"})

    @property
    def is_redirect(self):
        return False

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _good_page(b):
    year = time.strftime("%Y")
    text = (f"Welcome to {b['name']}. " + "We serve fresh food made from local ingredients every day. " * 12)
    return (f"<!doctype html><html><head><meta name='viewport' content='width=device-width'><title>{b['name']}</title>"
            f"<meta name='description' content='{b['name']} in Testville'></head><body><div><h1>{b['name']}</h1>"
            f"<p>{text}</p><p>Call {b['phone']} - {b['address']}</p><p>Menu and opening hours inside.</p>"
            f"<p>&copy; {year} {b['name']}</p><a href='mailto:{b['email']}'>{b['email']}</a></div></body></html>")


def _poor_page(b):
    return (f"<html><head></head><body><table><tr><td>{b['name']}</td></tr></table><table><tr><td>hi</td></tr></table>"
            f"<table><tr><td>x</td></tr></table><table><tr><td>Call {b['phone']}</td></tr></table>"
            f"<p>&copy; 2009</p></body></html>")


class World:
    """spacing_km: distance between consecutive businesses (from the centre outwards): small = a dense area."""

    def __init__(self, n=90, spacing_km=0.05, latency_s=0.0, pace_scale=0.02, category="restaurant", seed_shift=0):
        self.latency_s, self.pace_scale, self.category = latency_s, pace_scale, category
        self.biz = []
        for i in range(n):
            d = 0.2 + i * spacing_km
            ang = (i * 137.5) % 360
            lat = CENTER[0] + d / 111.0 * math.cos(math.radians(ang))
            lon = CENTER[1] + d / (111.0 * math.cos(math.radians(CENTER[0]))) * math.sin(math.radians(ang))
            kind = KINDS20[(i + seed_shift) % 20]
            b = {"i": i, "osm_id": 9000 + i, "name": f"Testville Kitchen {i:02d}", "phone": f"+1 610 555 {1000 + i}",
                 "street_no": str(10 + i), "address": f"{10 + i} Main St, Testville", "lat": lat, "lon": lon,
                 "dist_km": d, "kind": kind, "email": f"hello{i}@biz{i}.example"}
            if kind in ("ok", "poor"):
                b["website"] = f"{'https' if kind == 'ok' else 'http'}://biz{i}.example/"
            elif kind == "broken":
                b["website"] = f"http://dead{i}.example/"
            self.biz.append(b)
        self.http = []                # (method, url) of every request
        self.searches = []
        self.lock = threading.Lock()
        self.fail_search_once = set()  # business indexes whose FIRST web search raises (a transient failure)
        self._search_failed = set()

    # ------------------------------------------------------------ what's true (for scoring)
    def qualifying_within(self, radius_km):
        return [b for b in self.biz if b["dist_km"] <= radius_km and b["kind"] in QUALIFYING]

    def by_name(self, name):
        return next((b for b in self.biz if b["name"] == name), None)

    # ------------------------------------------------------------ the seams
    def request(self, method, url, **kw):
        with self.lock:
            self.http.append((method, url))
        if self.latency_s:
            time.sleep(self.latency_s)
        p = urllib.parse.urlparse(url)
        host = p.hostname or ""
        if p.path == "/robots.txt":
            return Resp(404, b"", url=url)
        if "nominatim" in host:
            return Resp(200, json.dumps([{"lat": str(CENTER[0]), "lon": str(CENTER[1]), "display_name": "Testville, Testland",
                                          "boundingbox": ["39.9", "40.1", "-75.1", "-74.9"], "osm_type": "relation",
                                          "osm_id": 1, "address": {"city": "Testville"}}]),
                        headers={"content-type": "application/json"}, url=url)
        if "overpass" in host:
            q = (kw.get("data") or {}).get("data", "")
            m = re.search(r"around:(\d+)", q)
            radius_km = int(m.group(1)) / 1000 if m else 3.0
            els = []
            for b in self.biz:
                if b["dist_km"] > radius_km:
                    continue
                tags = {"name": b["name"], "amenity": self.category, "phone": b["phone"],
                        "addr:housenumber": b["street_no"], "addr:street": "Main St", "addr:city": "Testville"}
                if b.get("website"):
                    tags["website"] = b["website"]
                els.append({"type": "node", "id": b["osm_id"], "lat": b["lat"], "lon": b["lon"], "tags": tags})
            return Resp(200, json.dumps({"elements": els}), headers={"content-type": "application/json"}, url=url)
        m = re.fullmatch(r"(biz|own|dead|other)(\d+)\.example", host)
        if m:
            kind, i = m.group(1), int(m.group(2))
            b = self.biz[i] if i < len(self.biz) else None
            if b is None or kind == "dead":
                return Resp(404, b"<html>gone</html>", url=url)
            if kind == "other":
                return Resp(200, "<html><head><title>Another place</title></head><body><p>Some other business in another "
                                 "town. Nothing about the one searched for.</p></body></html>", url=url)
            if kind == "own" or b["kind"] == "ok":
                return Resp(200, _good_page(b), url=url)
            return Resp(200, _poor_page(b), url=url)
        return Resp(404, b"not found", url=url)

    def search(self, q, news=False):
        with self.lock:
            self.searches.append(q)
        if self.latency_s:
            time.sleep(self.latency_s)
        b = next((x for x in self.biz if q.startswith(x["name"])), None)
        if b is None:
            return []
        if b["i"] in self.fail_search_once and b["i"] not in self._search_failed:
            self._search_failed.add(b["i"])
            raise ConnectionError("the search service didn't answer (simulated)")
        i = b["i"]
        if b["kind"] == "social":
            return [{"href": f"https://www.facebook.com/testvillekitchen{i}", "title": b["name"]}]
        if b["kind"] == "found":
            return [{"href": f"https://own{i}.example/", "title": b["name"]}]
        return [{"href": f"https://other{i}.example/", "title": "unrelated"},
                {"href": f"https://other{i + 1000}.example/", "title": "unrelated"}]


_ORIG = {}


def install(world):
    """Point the real code's network seams at `world`. -> world"""
    from room_agent import netguard
    from room_agent.missions import net
    from room_agent.tools import web

    netguard.resolve_public = lambda host, port=443, allow_private=False: ("93.184.216.34", "")
    netguard.http = lambda method, url, ip, **kw: world.request(method, url, **kw)
    web.search = world.search
    _ORIG.setdefault("intervals", dict(net.MIN_INTERVAL))
    _ORIG.setdefault("default", net.DEFAULT_INTERVAL)
    for h, v in _ORIG["intervals"].items():  # (real politeness pacing, scaled down: same shape, shorter waits)
        net.MIN_INTERVAL[h] = v * world.pace_scale
    net.DEFAULT_INTERVAL = _ORIG["default"] * world.pace_scale
    net._last.clear()
    net._robots.clear()
    return world
