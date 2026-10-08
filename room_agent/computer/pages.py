"""Fetching a public web page and pulling out its readable text (for research and as a fallback for reading a page).

Only http(s) to public addresses: localhost and private networks are refused, so a search result can never make Jarvis
fetch something on this PC or the home network (the control server, the router...). Size and time are capped.
"""

import ipaddress
import logging
import re
import socket
import urllib.parse
from dataclasses import dataclass, field

log = logging.getLogger("room-agent")
MAX_BYTES = 2_000_000
TIMEOUT_S = 8
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36 "
      "JarvisResearch/1.0")
ALLOW_PRIVATE = False  # (tests serve pages from 127.0.0.1)
DROP = ("script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe", "button", "template")


@dataclass
class Page:
    url: str
    ok: bool = False
    final_url: str = ""
    title: str = ""
    text: str = ""
    paragraphs: list = field(default_factory=list)
    links: list = field(default_factory=list)
    error: str = ""


def _public(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False, "not a web address"
    if ALLOW_PRIVATE:
        return True, ""
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError:
        return False, "the site's name doesn't resolve"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False, "it points to this PC or the local network"
    return True, ""


def fetch(url, timeout=TIMEOUT_S, cancel=None):
    """-> Page (ok=False with `error` when it couldn't be read: say so, never guess its content)."""
    import requests

    page = Page(url=url)
    allowed, why = _public(url)
    if not allowed:
        page.error = f"not fetched: {why}"
        return page
    try:
        with requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"}, timeout=timeout,
                          stream=True, allow_redirects=True) as r:
            page.final_url = r.url
            if not _public(r.url)[0]:
                page.error = "not fetched: it redirected to a local address"
                return page
            if r.status_code >= 400:
                page.error = f"the site answered {r.status_code}" + (" (blocked or needs a login)" if r.status_code in (401, 403, 429) else "")
                return page
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype and "text" not in ctype:
                page.error = f"not a web page ({ctype.split(';')[0] or 'unknown type'})"
                return page
            body = b""
            for chunk in r.iter_content(65536):
                body += chunk
                if len(body) > MAX_BYTES or (cancel is not None and cancel()):
                    break
            if cancel is not None and cancel():
                page.error = "stopped"
                return page
            html = body.decode(r.encoding or "utf-8", errors="replace")
    except requests.Timeout:
        page.error = "the site didn't answer in time"
        return page
    except requests.RequestException as e:
        page.error = f"couldn't connect ({e.__class__.__name__})"
        return page
    extract(page, html)
    page.ok = bool(page.text)
    if not page.ok:
        page.error = "no readable text on it (it may need JavaScript or a login)"
    return page


def extract(page, html):
    """Title, readable paragraphs (main content preferred) and links from HTML."""
    import lxml.html

    try:
        doc = lxml.html.fromstring(html)
    except Exception as e:
        page.error = f"unreadable page ({e.__class__.__name__})"
        return page
    page.title = re.sub(r"\s+", " ", (doc.findtext(".//title") or "")).strip()[:200]
    for el in list(doc.iter(*DROP)):  # (collected first: dropping while iterating skips elements)
        if el.getparent() is not None:
            el.drop_tree()
    main = next(iter(doc.xpath("//main|//article|//*[@role='main']")), None)
    root = main if main is not None else (doc.body if doc.body is not None else doc)
    paras = []
    for el in root.iter("p", "li", "h1", "h2", "h3", "h4", "pre", "td", "blockquote", "dd"):
        t = re.sub(r"\s+", " ", el.text_content()).strip()
        if len(t) >= 30 or (el.tag in ("h1", "h2", "h3") and len(t) >= 3):
            paras.append(("## " if el.tag in ("h1", "h2", "h3") else "") + t)
    if not paras:
        paras = [p.strip() for p in re.split(r"\n\s*\n", root.text_content()) if len(p.strip()) >= 30]
    seen, unique = set(), []
    for p in paras:
        if p not in seen:
            seen.add(p)
            unique.append(p[:1500])
    page.paragraphs = unique
    page.text = "\n".join(unique)
    base = page.final_url or page.url
    for a in doc.iter("a"):
        href = a.get("href") or ""
        if href.startswith(("http", "/")):
            page.links.append((re.sub(r"\s+", " ", a.text_content()).strip()[:100], urllib.parse.urljoin(base, href)))
    return page
