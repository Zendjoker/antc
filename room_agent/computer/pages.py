"""Fetching a public web page and pulling out its readable text (for research and as a fallback for reading a page).

Only http(s) to public addresses: localhost and private networks are refused, so a search result can never make Jarvis
fetch something on this PC or the home network (the control server, the router...). Size and time are capped.
"""

import logging
import re
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
    """-> (ok, why): an http(s) URL whose host resolves only to public addresses (room_agent/netguard.py)."""
    from room_agent import netguard

    ip, why = netguard.check_url(url, allow_private=ALLOW_PRIVATE)
    return ip is not None, why


def fetch(url, timeout=TIMEOUT_S, cancel=None):
    """-> Page (ok=False with `error` when it couldn't be read: say so, never guess its content).
    Every hop (the address and each redirect) is resolved once, must be public, and the connection goes to exactly
    that address (netguard): a redirect or a changed DNS answer can't make Jarvis request something on this PC or the
    home network."""
    from room_agent import netguard

    page = Page(url=url)
    r = netguard.request("GET", url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"}, timeout=timeout,
                         max_bytes=MAX_BYTES, cancel=cancel, allow_private=ALLOW_PRIVATE)
    page.final_url = r.final_url or url
    if r.error and not r.status:
        page.error = (r.error if r.error.startswith("not fetched") else
                      "the site didn't answer in time" if "Timeout" in r.error else r.error)
        return page
    if r.error == "too many redirects":
        page.error = "not fetched: too many redirects"
        return page
    if r.status >= 400:
        page.error = f"the site answered {r.status}" + (" (blocked or needs a login)" if r.status in (401, 403, 429) else "")
        return page
    ctype = {k.lower(): v for k, v in r.headers.items()}.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        page.error = f"not a web page ({ctype.split(';')[0] or 'unknown type'})"
        return page
    if cancel is not None and cancel():
        page.error = "stopped"
        return page
    html = r.body.decode(r.encoding or "utf-8", errors="replace")
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
