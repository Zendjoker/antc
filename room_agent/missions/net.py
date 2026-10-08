"""Polite HTTP for missions: every request identifies itself, waits its turn per host, respects robots.txt for websites,
times out, is size-capped, and only goes to public addresses (computer/pages.py's guard).

APIs (OpenStreetMap Nominatim / Overpass, Google Places) are governed by their usage policies rather than robots.txt:
Nominatim at most 1 request per second with an identifying User-Agent, Overpass a few seconds apart, Places by quota.
Business websites: robots.txt is checked for our User-Agent before each page.

Network safety: EVERY request here (pages, robots.txt, API calls) goes through _request(): the address must resolve to
a public IP (no localhost / LAN / link-local), redirects are followed by hand and each hop is checked the same way
before it's requested, responses are size-capped. (Residual: the name is resolved for the check and again by the
connection itself, so a DNS answer that changes in between - "rebinding" - isn't prevented.)

Cancellation (runctx): every function here checks the calling step's token before sending, while waiting its turn and
between response chunks, so a stopped / timed-out step makes no further request. A stop before sending raises
runctx.CancelledBeforeSend (a paid call's reservation is then released); a stop while reading raises Cancelled.

Response.sent says whether a request may have reached the server (for billing): True = it did, False = provably not
(refused before sending, or the connection couldn't be opened), None = unknown (dropped mid-way).
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
ROBOTS_MAX_BYTES = 512_000
MAX_REDIRECTS = 5
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
    sent: object = False  # True = reached the server, False = provably never sent, None = unknown

    def json(self):
        return json.loads(self.text)


def _host(url):
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _public(url):
    from room_agent.computer import pages

    return pages._public(url)


def _wait_turn(url, cancel=None):
    """Wait for this host's turn. Being stopped while waiting = stopped before sending."""
    host = _host(url)
    gap = MIN_INTERVAL.get(host, DEFAULT_INTERVAL)
    while True:
        runctx.check_before_send()
        with _lock:
            wait = _last.get(host, 0.0) + gap - time.time()
            if wait <= 0:
                _last[host] = time.time()
                return
        if cancel is not None and cancel():
            raise runctx.CancelledBeforeSend("stopped")
        try:
            runctx.sleep(min(wait, 0.25))
        except runctx.Cancelled as e:
            raise runctx.CancelledBeforeSend(str(e)) from None


def _not_sent(exc):
    """Did this requests exception happen before the request could have gone out?"""
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    if isinstance(exc, (requests.exceptions.InvalidURL, requests.exceptions.MissingSchema,
                        requests.exceptions.InvalidSchema)):
        return True
    if isinstance(exc, requests.ConnectionError):
        try:
            import urllib3.exceptions as u3
        except ImportError:  # pragma: no cover
            return False
        reason = exc.args[0] if exc.args else None
        reason = getattr(reason, "reason", reason)
        return isinstance(reason, (u3.NewConnectionError, getattr(u3, "NameResolutionError", u3.NewConnectionError)))
    return False


def _request(method, url, *, params=None, data=None, json_body=None, headers=None, timeout=20, max_bytes=MAX_BYTES,
             cancel=None, follow=True):
    """One request with manual, checked redirects. -> Response (never raises except Cancelled)."""
    resp = Response(url=url)
    ok, why = _public(url)
    if not ok:
        resp.error = f"not sent: {why}"
        return resp
    _wait_turn(url, cancel)
    t0 = time.time()
    current, hops = url, 0
    hdrs = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8", **(headers or {})}
    try:
        while True:
            runctx.check_before_send() if hops == 0 else runctx.check()
            r = requests.request(method, current, params=params if hops == 0 else None,
                                 data=data if hops == 0 else None, json=json_body if hops == 0 else None,
                                 headers=hdrs, timeout=timeout, stream=True, allow_redirects=False)
            resp.sent = True
            with r:
                if follow and r.is_redirect and hops < MAX_REDIRECTS and method == "GET":
                    nxt = urllib.parse.urljoin(current, r.headers.get("Location", ""))
                    ok, why = _public(nxt)
                    if not ok:
                        resp.status, resp.final_url = r.status_code, nxt
                        resp.error = f"not fetched: it redirected to an address that isn't allowed ({why})"
                        return resp
                    resp.redirects.append(current)
                    if _host(nxt) != _host(url):  # (never carry API keys / custom headers to another host)
                        hdrs = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"}
                    current, hops = nxt, hops + 1
                    continue
                resp.status, resp.final_url, resp.headers = r.status_code, current, dict(r.headers)
                body = b""
                for chunk in r.iter_content(65536):
                    body += chunk
                    if len(body) > max_bytes:
                        body = body[:max_bytes]
                        break
                    if (cancel is not None and cancel()) or runctx.cancelled():
                        raise Stopped("stopped while reading a response")
                resp.bytes = len(body)
                resp.text = body.decode(r.encoding or "utf-8", errors="replace")
                if r.is_redirect:
                    resp.error = "too many redirects" if method == "GET" else f"the server answered {r.status_code}"
                    return resp
                resp.ok = 200 <= r.status_code < (400 if method == "GET" else 300)
                if not resp.ok:
                    resp.error = f"the server answered {r.status_code}"
                return resp
    except runctx.Cancelled:
        raise
    except requests.Timeout as e:
        resp.error = "no answer in time"
        resp.sent = False if (_not_sent(e) and hops == 0) else True
    except requests.RequestException as e:
        resp.error = f"couldn't connect ({e.__class__.__name__})"
        resp.sent = False if (_not_sent(e) and hops == 0 and resp.sent is False) else None
    finally:
        resp.elapsed_s = round(time.time() - t0, 2)
    return resp


def allowed_by_robots(url, cancel=None):
    """robots.txt for our User-Agent. Only for public addresses (anything else: not allowed, nothing requested).
    Unreadable (4xx) = allowed; server errors / timeouts / bad redirects = not allowed (cautious)."""
    p = urllib.parse.urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    if not _public(origin)[0]:
        return False
    cached = _robots.get(origin)
    if cached is None or time.time() - cached[1] > 3600:
        rp = urllib.robotparser.RobotFileParser()
        verdict = None
        r = _request("GET", origin + "/robots.txt", timeout=10, max_bytes=ROBOTS_MAX_BYTES, cancel=cancel)
        if not r.status or r.error.startswith("not "):
            verdict = False
        elif r.status >= 500:
            verdict = False
        elif r.status >= 400:
            verdict = True
        elif 300 <= r.status < 400:
            verdict = False
        else:
            rp.parse(r.text.splitlines())
        cached = (rp if verdict is None else verdict, time.time())
        _robots[origin] = cached
    rule = cached[0]
    return rule if isinstance(rule, bool) else rule.can_fetch(UA, url)


def get(url, params=None, headers=None, timeout=20, respect_robots=True, cancel=None, max_bytes=MAX_BYTES):
    """GET a public URL. -> Response (ok=False with `error` instead of raising, except Cancelled)."""
    runctx.check_before_send()
    if not _public(url)[0]:
        resp = Response(url=url)
        resp.error = f"not fetched: {_public(url)[1]}"
        return resp
    if respect_robots and not allowed_by_robots(url, cancel):
        resp = Response(url=url)
        resp.error = "not fetched: robots.txt doesn't allow it"
        return resp
    return _request("GET", url, params=params, headers=headers, timeout=timeout, max_bytes=max_bytes, cancel=cancel)


def search(query, max_results=8):
    """A web search (DuckDuckGo through tools/web.py, no key), paced and cancellable. -> [result dicts]"""
    from room_agent.tools import web

    _wait_turn("https://duckduckgo.com/")
    runctx.check_before_send()
    results = web.search(query, news=False) or []
    runctx.check()
    return results[:max_results]


def post(url, data=None, json_body=None, headers=None, timeout=40, cancel=None):
    """POST to a public API (no robots.txt: APIs have usage policies; redirects are not followed). -> Response"""
    runctx.check_before_send()
    return _request("POST", url, data=data, json_body=json_body, headers=headers, timeout=timeout, cancel=cancel,
                    follow=False)
