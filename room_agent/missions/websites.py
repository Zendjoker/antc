"""Does this business have a website, and how good is it? Every conclusion carries the evidence it came from.

website_status (what was established, and how):
    ok / poor          its site loaded and was checked (the checks and their evidence are in `analysis`)
    broken             the listed site didn't load (error / server error / parked domain), with the exact error
    blocked            robots.txt doesn't let us read it: quality unknown (never guessed)
    social_only        no own site found, only social-media pages
    directory_only     no own site found, only directory listings (Yelp, TripAdvisor...): a directory is NOT their website
    none_found         no site in the source data AND a web search for the name + city found none (on the date given);
                       stated as "none found", never as "they have no website"
    possible           a search result might be theirs but nothing on it matched their phone or address: kept aside,
                       not trusted, not used as their website
    unknown            couldn't check (search failed, etc.)

The opportunity score (0-100) is a sum of explained points; see score().
"""

import logging
import re
import time
import urllib.parse

from room_agent.missions import net
from room_agent.missions.store import norm_phone, norm_text

log = logging.getLogger("room-agent")
SOCIAL = ("facebook.com", "instagram.com", "twitter.com", "x.com", "tiktok.com", "linkedin.com", "youtube.com",
          "linktr.ee", "pinterest.com", "threads.net")
DIRECTORIES = ("yelp.", "tripadvisor.", "google.", "goo.gl", "doordash.com", "ubereats.com", "grubhub.com", "seamless.com",
               "postmates.com", "opentable.com", "resy.com", "yellowpages.com", "mapquest.com", "foursquare.com",
               "zomato.com", "menupages.com", "allmenus.com", "menuism.com", "restaurantji.com", "sirved.com",
               "bbb.org", "manta.com", "chamberofcommerce.com", "nextdoor.com", "groupon.com", "toasttab.com",
               "squareup.com", "order.online", "chownow.com", "slice.com", "beyondmenu.com", "zmenu.com", "wikipedia.org",
               "openstreetmap.org", "apple.com", "bing.com", "waze.com", "yahoo.com", "superpages.com", "citysearch.com",
               "healthgrades.com", "zocdoc.com", "vitals.com", "angi.com", "homeadvisor.com", "thumbtack.com",
               "houzz.com", "booksy.com", "vagaro.com", "styleseat.com", "fresha.com", "mindbodyonline.com",
               "eater.com", "infatuation.com", "timeout.com", "sfgate.com", "michelin.com", "zagat.com", "reddit.com")
BUILDERS = {"wix": "Wix", "squarespace": "Squarespace", "weebly": "Weebly", "godaddysites": "GoDaddy",
            "wordpress": "WordPress", "shopify": "Shopify", "webflow": "Webflow", "duda": "Duda", "jimdo": "Jimdo"}
PARKED = re.compile(r"domain (is |may be )?for sale|buy this domain|this domain (has expired|is parked)|parked free|"
                    r"domain parking|hugedomains|sedo\.com|afternic|dan\.com|is available for registration", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
BAD_EMAIL_DOMAINS = ("example.com", "sentry.io", "wixpress.com", "sentry-next.wixpress.com", "domain.com", "email.com",
                     "yourdomain.com", "godaddy.com", "squarespace.com", "w3.org", "schema.org")


def host(url):
    return (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")


def kind_of(url):
    h = host(url)
    if any(h == s or h.endswith("." + s) for s in SOCIAL):
        return "social"
    if any(d in h for d in DIRECTORIES):
        return "directory"
    return "site"


# ---------------------------------------------------------------- reading a site
def _visible_text(html):
    import lxml.html

    html = re.sub(r"^\s*<\?xml[^>]*>", "", html or "")  # (lxml refuses a str that declares its own encoding)
    try:
        doc = lxml.html.fromstring(html)
    except Exception:
        return None, ""
    for el in list(doc.iter("script", "style", "noscript", "template")):
        if el.getparent() is not None:
            el.drop_tree()
    return doc, re.sub(r"\s+", " ", doc.text_content() or "").strip()


def _emails(html, site_host):
    found = set()
    for m in re.finditer(r"mailto:([^\"'?>\s]+)", html, re.I):
        found.add(urllib.parse.unquote(m.group(1)).strip().lower())
    for m in EMAIL.finditer(html):
        found.add(m.group(0).strip().lower())
    good = []
    for e in sorted(found):
        dom = e.rsplit("@", 1)[-1]
        if re.search(r"\.(png|jpe?g|gif|svg|webp|css|js)$", e) or any(dom == b or dom.endswith("." + b) for b in BAD_EMAIL_DOMAINS):
            continue
        if re.match(r"^[0-9a-f]{16,}@", e):  # (tracking ids)
            continue
        good.append(e)
    # their own domain first
    return sorted(good, key=lambda e: (not e.endswith("@" + site_host) and not e.endswith("." + site_host), e))[:5]


def analyze(url, lead, cancel=None):
    """Load a business's site (robots.txt respected) and check it. -> analysis dict with status, checks, evidence."""
    now = time.strftime("%Y-%m-%d %H:%M")
    a = {"url": url, "checked": now, "checks": [], "status": "unknown", "emails": [], "evidence": []}
    if not net.allowed_by_robots(url, cancel):
        a.update(status="blocked", evidence=[f"robots.txt at {host(url)} doesn't allow automated reading ({now})"])
        return a
    r = net.get(url, cancel=cancel, timeout=20, max_bytes=2_000_000)
    a.update(final_url=r.final_url or url, http_status=r.status, load_s=r.elapsed_s, bytes=r.bytes, redirects=r.redirects)
    if not r.ok:
        a.update(status="broken", error=r.error, evidence=[f"{url} didn't load: {r.error} ({now})"])
        return a
    html = r.text
    doc, text = _visible_text(html)
    if PARKED.search(text[:5000]) or PARKED.search(html[:5000]):
        a.update(status="broken", parked=True, evidence=[f"{a['final_url']} looks like a parked / for-sale domain ({now})"])
        return a
    low = html.lower()
    final = a["final_url"]
    checks = a["checks"]

    def check(name, passed, evidence, weight):
        checks.append({"name": name, "ok": bool(passed), "evidence": evidence, "weight": weight})

    check("https", final.startswith("https://"), f"final address {final}", 10)
    check("mobile_viewport", bool(re.search(r"<meta[^>]+name=[\"']viewport", low)),
          "has a viewport meta tag" if re.search(r"<meta[^>]+name=[\"']viewport", low) else "no viewport meta tag "
          "(pages don't adapt to phones)", 15)
    title = re.sub(r"\s+", " ", (doc.findtext(".//title") if doc is not None else "") or "").strip()
    check("title", bool(title), f"title: {title[:80]!r}" if title else "no page title", 5)
    desc = re.search(r"<meta[^>]+name=[\"']description[\"'][^>]*content=[\"']([^\"']*)", low)
    check("meta_description", bool(desc and desc.group(1).strip()), "has a meta description" if desc else
          "no meta description (search engines show a random snippet)", 3)
    check("enough_text", len(text) >= 400, f"{len(text)} characters of visible text", 10)
    phone = norm_phone(lead.get("phone", ""))
    page_digits = re.sub(r"\D", "", text)
    if phone:
        check("phone_on_page", phone[-7:] in page_digits, "their phone number is on the home page" if phone[-7:] in page_digits
              else "their phone number isn't on the home page", 5)
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", html, re.I)]
    this_year = int(time.strftime("%Y"))
    if years:
        newest = max(years)
        check("copyright_recent", newest >= this_year - 3, f"newest copyright year on the page: {newest}", 10)
    check("speed", (r.elapsed_s or 0) <= 4.0, f"home page took {r.elapsed_s}s to download", 5)
    tables = low.count("<table")
    check("modern_layout", not (tables >= 4 and "display:flex" not in low and "grid" not in low and "<div" not in low[:20000]),
          f"{tables} layout tables" if tables else "no table layout", 10)
    if "flash" in low and (".swf" in low or "shockwave" in low):
        check("no_flash", False, "uses Flash (doesn't run in modern browsers)", 15)
    if re.search(r"\b(menu|hours|open)\b", text, re.I) is None and "restaurant" in (lead.get("category") or ""):
        check("menu_or_hours", False, "no menu or opening hours on the home page", 8)
    a["confirms"] = confirms(text, lead)
    a["builder"] = next((v for k, v in BUILDERS.items() if k in low[:200000]), "")
    a["emails"] = _emails(html, host(final))
    contact = _contact_page(doc, final)
    if contact and not a["emails"]:
        c = net.get(contact, cancel=cancel, timeout=15, max_bytes=1_000_000)
        if c.ok:
            a["emails"] = _emails(c.text, host(final))
            a["contact_page"] = contact
    failed = sum(c["weight"] for c in checks if not c["ok"])
    a["status"] = "poor" if failed >= 25 else "ok"
    a["issues"] = [c["evidence"] for c in checks if not c["ok"]]
    a["evidence"] = [f"{final} loaded ({r.status}, {r.elapsed_s}s, {r.bytes} bytes) on {now}"] + a["issues"]
    return a


def _contact_page(doc, base):
    if doc is None:
        return ""
    for el in doc.iter("a"):
        href, label = (el.get("href") or ""), (el.text_content() or "").lower()
        if "contact" in href.lower() or "contact" in label:
            url = urllib.parse.urljoin(base, href)
            if host(url) == host(base) and url.startswith("http"):
                return url
    return ""


def confirms(page_text, lead):
    """Which of the lead's facts this page shows: {"phone": bool, "address": bool, "name": bool}. Used to confirm facts
    from a non-Google source before they may be stored (places.py)."""
    digits = re.sub(r"\D", "", page_text or "")
    norm = norm_text(page_text or "")
    phone = norm_phone(lead.get("phone", ""))
    m = re.match(r"(\d+)\s+(\w+)", norm_text(lead.get("address", "")))
    name = [w for w in norm_text(lead.get("name", "")).split() if len(w) > 2]
    return {"phone": bool(phone and phone[-7:] in digits),
            "address": bool(m and re.search(rf"\b{m.group(1)}\s+{re.escape(m.group(2))}", norm)),
            "name": bool(name and all(w in norm for w in name))}


# ---------------------------------------------------------------- finding a site the data didn't list
def _matches(page_text, lead):
    """Does a page belong to this business? -> (strength, why). Phone or street address = strong; name only = weak."""
    digits = re.sub(r"\D", "", page_text)
    phone = norm_phone(lead.get("phone", ""))
    if phone and phone[-7:] in digits:
        return "strong", "their phone number is on it"
    addr = norm_text(lead.get("address", ""))
    m = re.match(r"(\d+)\s+(\w+)", addr)
    if m and re.search(rf"\b{m.group(1)}\s+{re.escape(m.group(2))}", norm_text(page_text)):
        return "strong", "their street address is on it"
    name = [w for w in norm_text(lead.get("name", "")).split() if len(w) > 2]
    if name and all(w in norm_text(page_text) for w in name):
        return "weak", "only their name is on it"
    return "", ""


def find_official_site(lead, cancel=None, search=None):
    """Search the web for the business's own site. -> dict(status, website, evidence, social, directories, possible)"""
    from room_agent.missions import runctx

    search = search or net.search
    q = f"{lead['name']} {lead.get('city') or ''}".strip()
    try:
        results = search(q) or []
    except runctx.Cancelled:
        raise
    except Exception as e:  # noqa: BLE001
        return {"status": "unknown", "evidence": [f"the web search for '{q}' failed ({e.__class__.__name__})"], "query": q}
    when = time.strftime("%Y-%m-%d")
    social, directories, possible = [], [], []
    for r in results[:8]:
        url = r.get("href") or r.get("url") or ""
        if not url.startswith("http"):
            continue
        k = kind_of(url)
        if k == "social":
            social.append(url)
            continue
        if k == "directory":
            directories.append(url)
            continue
        page = net.get(url, cancel=cancel, timeout=15, max_bytes=1_500_000)
        if not page.ok:
            continue
        _, text = _visible_text(page.text)
        strength, why = _matches(text, lead)
        if strength == "strong":
            return {"status": "found", "website": page.final_url or url, "query": q, "social": social,
                    "directories": directories,
                    "evidence": [f"web search '{q}' ({when}) -> {url}: {why}"]}
        if strength == "weak":
            possible.append({"url": url, "why": why})
    if not results:
        return {"status": "unknown", "query": q, "evidence": [f"the web search for '{q}' returned nothing ({when}); "
                                                              "couldn't verify either way"]}
    status = "social_only" if social else "directory_only" if directories else "possible" if possible else "none_found"
    ev = [f"web search '{q}' ({when}): {len(results)} results, none on a site of their own that shows their phone or address"]
    if possible:
        ev.append("possibly theirs (not confirmed): " + ", ".join(p["url"] for p in possible[:2]))
    return {"status": status, "query": q, "social": social[:3], "directories": directories[:5], "possible": possible[:3],
            "evidence": ev}


# ---------------------------------------------------------------- the opportunity score
def score(lead, analysis, search_result=None):
    """-> (score 0-100, level, [reasons with evidence], confidence 0-1). Higher = a better website opportunity."""
    reasons, pts = [], 0

    def add(n, why):
        nonlocal pts
        pts += n
        reasons.append({"points": n, "why": why})

    status = lead.get("website_status")
    extra = lead.get("extra") or {}
    if extra.get("disused") or (extra.get("places") or {}).get("status") in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
        return 0, "excluded", [{"points": 0, "why": "listed as closed"}], 0.8
    confidence = 0.6
    if status == "none_found":
        add(55, "no website found: none in the listing data, and a web search found none (it may still exist)")
        confidence = 0.6
    elif status == "social_only":
        add(50, "only social-media pages found, no site of their own")
        confidence = 0.7
    elif status == "directory_only":
        add(45, "only directory listings found (a listing isn't a website)")
        confidence = 0.6
    elif status == "possible":
        add(25, "a page that might be theirs was found but couldn't be confirmed")
        confidence = 0.4
    elif status == "broken":
        add(50, "their listed site doesn't work: " + (analysis.get("error") or "parked / for sale"))
        confidence = 0.8
    elif status in ("ok", "poor"):
        failed = [c for c in analysis.get("checks", []) if not c["ok"]]
        for c in failed:
            add(c["weight"], c["evidence"])
        confidence = 0.85
    elif status == "blocked":
        add(10, "their site doesn't allow automated checks, so its quality is unknown")
        confidence = 0.3
    else:
        add(10, "website status couldn't be checked")
        confidence = 0.2
    if lead.get("phone"):
        add(10, "has a public phone number (reachable)")
    if lead.get("email"):
        add(5, "has a public email address")
    if not lead.get("address"):
        confidence -= 0.1
    s = max(0, min(100, pts))
    level = "high" if s >= 60 else "medium" if s >= 35 else "low"
    return s, level, reasons, round(max(0.1, min(confidence, 0.95)), 2)


MISSING_FIELDS = ("address", "phone", "website", "email")


def missing(lead):
    return [f for f in MISSING_FIELDS if not lead.get(f)]
