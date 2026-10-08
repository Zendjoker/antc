"""Polite HTTP for missions: every request identifies itself, waits its turn per host, respects robots.txt for websites,
times out, is size-capped, and only goes to public addresses (computer/pages.py's guard).

APIs (OpenStreetMap Nominatim / Overpass, Google Places) are governed by their usage policies rather than robots.txt:
Nominatim at most 1 request per second with an identifying User-Agent, Overpass a few seconds apart, Places by quota.
Business websites: robots.txt is checked for our User-Agent before each page.

Cancellation (runctx): every function here checks the calling step's token before sending, while waiting its turn and
between response chunks, so a stopped / timed-out step makes no further request.
"""

import json
import logging
import threading
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field

import requests

from room_agent.missions import runctx

log = logging.getLogger("room-agent")
UA = "JarvisResearch/1.0 (personal business-research assistant on a Windows PC; respects robots.txt)"
MAX_BYTES = 3_000_000
MIN_INTERVAL = {"nominatim.openstreetmap.org": 1.2, "overpass-api.de": 3.0, "overpass.kumi.systems": 3.0,
                "places.googleapis.com": 0.2, "duckduckgo.com": 2.5}
DEFAULT_INTERVAL = 1.0
_lock = threading.Lock()
_last = {}
_robots = {}


Stopped = runctx.Cancelled  # (the old name: the mission was paused / stopped while waiting)


@dataclass
class Response:
    url: str
    ok: bool = False
    status: int = 0
    final_url: str = ""
    text: str = ""
    headers: dict = field(default_factory=dict)
    elapsed_s: float = 0.0
    bytes: int = 0
    error: str = ""
    redirects: list = field(default_factory=list)
    sent: object = False  # post(): True = it reached the server, None = unknown (connection dropped), False = never sent

    def json(self):
        return json.loads(self.text)


def _host(url):
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _wait_turn(url, cancel=None):
    host = _host(url)
    gap = MIN_INTERVAL.get(host, DEFAULT_INTERVAL)
    while True:
        runctx.check()
        with _lock:
            wait = _last.get(host, 0.0) + gap - time.time()
            if wait <= 0:
                _last[host] = time.time()
                return
        if cancel is not None and cancel():
            raise Stopped("stopped")
        runctx.sleep(min(wait, 0.25))


def allowed_by_robots(url, cancel=None):
    """robots.txt for our User-Agent. Unreadable (4xx) = allowed; server errors / timeouts = not allowed (cautious)."""
    p = urllib.parse.urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    cached = _robots.get(origin)
    if cached is None or time.time() - cached[1] > 3600:
        rp = urllib.robotparser.RobotFileParser()
        verdict = None
        try:
            _wait_turn(origin, cancel)
            runctx.check()
            r = requests.get(origin + "/robots.txt", headers={"User-Agent": UA}, timeout=10)
            if r.status_code >= 500:
                verdict = False
            elif r.status_code >= 400:
                verdict = True
            else:
                rp.parse(r.text.splitlines())
        except Stopped:
            raise
        except requests.RequestException:
            verdict = False
        cached = (rp if verdict is None else verdict, time.time())
        _robots[origin] = cached
    rule = cached[0]
    return rule if isinstance(rule, bool) else rule.can_fetch(UA, url)


def get(url, params=None, headers=None, timeout=20, respect_robots=True, cancel=None, max_bytes=MAX_BYTES):
    """GET a public URL. -> Response (ok=False with `error` instead of raising, except Stopped)."""
    from room_agent.computer import pages

    runctx.check()
    resp = Response(url=url)
    ok, why = pages._public(url)
    if not ok:
        resp.error = f"not fetched: {why}"
        return resp
    if respect_robots and not allowed_by_robots(url, cancel):
        resp.error = "not fetched: robots.txt doesn't allow it"
        return resp
    _wait_turn(url, cancel)
    t0 = time.time()
    try:
        with requests.get(url, params=params, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8", **(headers or {})},
                          timeout=timeout, stream=True, allow_redirects=True) as r:
            resp.status, resp.final_url, resp.headers = r.status_code, r.url, dict(r.headers)
            resp.redirects = [h.url for h in r.history]
            if not pages._public(r.url)[0]:
                resp.error = "not fetched: it redirected to a local address"
                return resp
            body = b""
            for chunk in r.iter_content(65536):
                body += chunk
                if len(body) > max_bytes:
                    break
                if (cancel is not None and cancel()) or runctx.cancelled():
                    raise Stopped("stopped while reading a page")
            resp.bytes = len(body)
            resp.text = body.decode(r.encoding or "utf-8", errors="replace")
            resp.ok = 200 <= r.status_code < 400
            if not resp.ok:
                resp.error = f"the server answered {r.status_code}"
    except Stopped:
        raise
    except requests.Timeout:
        resp.error = "no answer in time"
        resp.sent = True
    except requests.ConnectionError as e:
        resp.error = f"couldn't connect ({e.__class__.__name__})"
        resp.sent = None  # (unknown: it may have failed before or after the request went out)
    except requests.RequestException as e:
        resp.error = f"couldn't connect ({e.__class__.__name__})"
        resp.sent = None
    resp.elapsed_s = round(time.time() - t0, 2)
    return resp


def search(query, max_results=8):
    """A web search (DuckDuckGo through tools/web.py, no key), paced and cancellable. -> [result dicts]"""
    from room_agent.tools import web

    _wait_turn("https://duckduckgo.com/")
    runctx.check()
    results = web.search(query, news=False) or []
    runctx.check()
    return results[:max_results]


def post(url, data=None, json_body=None, headers=None, timeout=40, cancel=None):
    """POST to a public API (no robots.txt: APIs have usage policies). -> Response"""
    from room_agent.computer import pages

    runctx.check()
    resp = Response(url=url)
    ok, why = pages._public(url)
    if not ok:
        resp.error = f"not sent: {why}"
        return resp
    _wait_turn(url, cancel)
    runctx.check()
    t0 = time.time()
    try:
        r = requests.post(url, data=data, json=json_body, headers={"User-Agent": UA, **(headers or {})}, timeout=timeout)
        resp.status, resp.final_url, resp.text, resp.headers = r.status_code, r.url, r.text[:MAX_BYTES], dict(r.headers)
        resp.sent = True
        resp.ok = 200 <= r.status_code < 300
        if not resp.ok:
            resp.error = f"the API answered {r.status_code}"
    except requests.Timeout:
        resp.error = "no answer in time"
    except requests.RequestException as e:
        resp.error = f"couldn't connect ({e.__class__.__name__})"
    resp.elapsed_s = round(time.time() - t0, 2)
    return resp
