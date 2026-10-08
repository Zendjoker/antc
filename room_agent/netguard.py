"""Outbound HTTP to untrusted addresses (web research, mission page fetches), safe against SSRF and DNS rebinding.

    resolve_public(host, port)  resolves the name ONCE; every address must be a public unicast one (ipaddress
                                is_global, multicast / reserved / unspecified excluded, IPv4 embedded in IPv6 - mapped,
                                6to4, Teredo, NAT64 - checked as the IPv4 it is). Anything else is refused.
    http(method, url, ip, ...)  sends the request to exactly that validated IP: the socket is opened to `ip`, while the
                                Host header, TLS SNI and certificate verification all keep using the hostname. A
                                second DNS answer (rebinding) is never consulted. No proxies from the environment, no
                                automatic redirects, no retries.
    request(...)                the two together, with redirects followed by hand: every hop is resolved, validated
                                and pinned the same way before it's requested; credential headers are dropped when a
                                redirect leaves the original host; the body is size-capped.
Used by computer/pages.py (general web research) and missions/net.py (missions).
"""

import ipaddress
import socket
import urllib.parse

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

MAX_REDIRECTS = 5
SAFE_HEADERS = {"user-agent", "accept", "accept-language", "accept-encoding"}
NAT64 = ipaddress.ip_network("64:ff9b::/96")


class Blocked(Exception):
    """The destination isn't allowed (not a public address, bad scheme, ...)."""


def ip_allowed(ip):
    """-> (ok, why). Public unicast only; IPv4 hidden inside IPv6 is judged as that IPv4."""
    try:
        a = ipaddress.ip_address(str(ip).split("%", 1)[0])
    except ValueError:
        return False, "not an IP address"
    if a.version == 6:
        inner = a.ipv4_mapped or a.sixtofour or (a.teredo[1] if a.teredo else None)
        if inner is None and a in NAT64:
            inner = ipaddress.IPv4Address(int(a) & 0xFFFFFFFF)
        if inner is not None:
            ok, why = ip_allowed(inner)
            return (ok, why) if not ok else (True, "")
    if a.is_loopback or a.is_private or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified:
        return False, "it points to this PC or a private network"
    if not a.is_global:
        return False, "it isn't a public internet address"
    return True, ""


def resolve_public(host, port=443, allow_private=False):
    """-> (ip, "") with the address to connect to, or (None, why). One DNS lookup; ALL answers must be public (a name
    that also resolves to a private address is refused outright). allow_private: only for the automated tests' local
    servers (computer/pages.ALLOW_PRIVATE); the address is still pinned."""
    host = str(host or "").strip().rstrip(".")
    if not host:
        return None, "no host"
    lit = host.strip("[]")
    try:
        ipaddress.ip_address(lit.split("%", 1)[0])
        literal = True
    except ValueError:
        literal = False
    try:
        infos = socket.getaddrinfo(lit if literal else host, port, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return None, "the site's name doesn't resolve"
    ips = []
    for info in infos:
        ip = info[4][0]
        ok, why = ip_allowed(ip)
        if not ok and not allow_private:
            return None, why
        ips.append(ip)
    if not ips:
        return None, "the site's name doesn't resolve"
    return ips[0], ""


def check_url(url, allow_private=False):
    """-> (ip, "") or (None, why) for an http(s) URL."""
    p = urllib.parse.urlparse(str(url or ""))
    if p.scheme not in ("http", "https") or not p.hostname:
        return None, "not a web address"
    if p.username or p.password:
        return None, "addresses with a user name / password aren't fetched"
    try:
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        return None, "invalid port"
    return resolve_public(p.hostname, port, allow_private)


# ---------------------------------------------------------------- connections pinned to the validated IP
def _pinned_socket(conn, base_new_conn):
    # urllib3 uses _dns_host both to open the socket AND (via .host) for SNI / certificate checks: point it at the
    # validated IP only while the socket is created, so TLS still verifies the hostname.
    name = conn._dns_host
    conn._dns_host = conn.pinned_ip
    try:
        return base_new_conn(conn)
    finally:
        conn._dns_host = name


class _PinnedHTTPConnection(HTTPConnection):
    pinned_ip = None

    def _new_conn(self):
        return _pinned_socket(self, HTTPConnection._new_conn)


class _PinnedHTTPSConnection(HTTPSConnection):
    pinned_ip = None

    def _new_conn(self):
        return _pinned_socket(self, HTTPSConnection._new_conn)


class _PinnedAdapter(HTTPAdapter):
    def __init__(self, ip):
        self._ip = ip
        super().__init__(max_retries=0)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        super().init_poolmanager(connections, maxsize, block, **pool_kwargs)
        ip = self._ip
        conn = type("PinnedConn", (_PinnedHTTPConnection,), {"pinned_ip": ip})
        sconn = type("PinnedSConn", (_PinnedHTTPSConnection,), {"pinned_ip": ip})
        self.poolmanager.pool_classes_by_scheme = {
            "http": type("PinnedPool", (HTTPConnectionPool,), {"ConnectionCls": conn}),
            "https": type("PinnedSPool", (HTTPSConnectionPool,), {"ConnectionCls": sconn})}


def http(method, url, ip, *, headers=None, params=None, data=None, json=None, timeout=20, verify=True):
    """One request, connected to `ip` (already validated), no redirects / proxies / retries. -> requests.Response
    (streamed: the caller reads it with iter_content and closes it)."""
    s = requests.Session()
    s.trust_env = False  # (no HTTP(S)_PROXY / .netrc from the environment: a proxy would bypass the pinning)
    s.mount("http://", _PinnedAdapter(ip))
    s.mount("https://", _PinnedAdapter(ip))
    # (the session isn't closed here: the streamed response holds its connection until the caller closes it)
    return s.request(method, url, headers=headers, params=params, data=data, json=json, timeout=timeout,
                     stream=True, allow_redirects=False, verify=verify)


class Result:
    def __init__(self, url):
        self.url, self.final_url, self.status, self.headers = url, "", 0, {}
        self.body, self.error, self.redirects, self.sent, self.encoding = b"", "", [], False, "utf-8"


def request(method, url, *, headers=None, params=None, data=None, json=None, timeout=20, max_bytes=2_000_000,
            follow=True, cancel=None, allow_private=False):
    """A guarded request: every hop validated + pinned (see module doc). Never raises for network errors: they're in
    Result.error; Result.sent tells whether the server may have received the request."""
    res = Result(url)
    current, hops = url, 0
    hdrs = dict(headers or {})
    origin_host = (urllib.parse.urlparse(url).hostname or "").lower()
    while True:
        ip, why = check_url(current, allow_private)
        if ip is None:
            res.error = f"not fetched: {why}" if hops == 0 else f"not fetched: it redirected to an address that isn't allowed ({why})"
            res.final_url = current
            return res
        try:
            r = http(method, current, ip, headers=hdrs, params=params if hops == 0 else None,
                     data=data if hops == 0 else None, json=json if hops == 0 else None, timeout=timeout)
        except requests.RequestException as e:
            res.error = f"couldn't connect ({e.__class__.__name__})"
            res.sent = None if hops == 0 else True
            return res
        res.sent = True
        with r:
            if follow and r.is_redirect and method == "GET" and hops < MAX_REDIRECTS:
                nxt = urllib.parse.urljoin(current, r.headers.get("Location", ""))
                res.redirects.append(current)
                if (urllib.parse.urlparse(nxt).hostname or "").lower() != origin_host:
                    hdrs = {k: v for k, v in hdrs.items() if k.lower() in SAFE_HEADERS}
                current, hops = nxt, hops + 1
                continue
            res.status, res.final_url, res.headers = r.status_code, current, dict(r.headers)
            res.encoding = r.encoding or "utf-8"
            body = b""
            for chunk in r.iter_content(65536):
                body += chunk
                if len(body) > max_bytes:
                    body = body[:max_bytes]
                    break
                if cancel is not None and cancel():
                    res.error = "stopped"
                    break
            res.body = body
            if r.is_redirect:
                res.error = "too many redirects" if method == "GET" else f"the server answered {r.status_code}"
            return res
