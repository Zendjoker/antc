"""Background watchers: "tell me when the price drops", "let me know when this page changes", "check my email every hour
for anything from the landlord". Kept in watchers.json (they survive restarts), checked on a schedule in the background.

    page   a public web page: changed at all / contains a phrase / its price is below a value (or below the price when the
           watch started: "when the price drops"). Read like research reads pages (public addresses only).
    email  new Gmail messages matching a search, since the watch started (needs Google connected).

When it triggers it tells you once (spoken through the proactive policy, or texted if you asked for a text), then stops,
except an email watch, which keeps going until it expires. Watches expire after WATCH_DAYS; at most MAX_WATCHERS.
"""

import hashlib
import json
import logging
import re
import threading
import time
import uuid

from room_agent import config

log = logging.getLogger("room-agent")
MAX_WATCHERS = 10
WATCH_DAYS = 14
MIN_EVERY_MIN = {"page": 15, "email": 10}
PRICE = re.compile(r"(?:(?P<cur>[$€£])\s?(?P<a>\d{1,3}(?:[,\s]\d{3})*(?:[.,]\d{1,2})?)|(?P<b>\d{1,3}(?:[,\s.]\d{3})*(?:[.,]\d{1,2})?)"
                   r"\s?(?P<cur2>€|EUR|USD|GBP))")
_lock = threading.Lock()


def _load():
    try:
        data = json.loads(config.WATCHERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items):
    tmp = config.WATCHERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1), encoding="utf-8")
    tmp.replace(config.WATCHERS_FILE)


def parse_price(text):
    """The first price on a page (the main one, on product pages). -> float or None."""
    m = PRICE.search(text or "")
    if not m:
        return None
    raw = (m.group("a") or m.group("b") or "").replace(" ", "")
    if re.search(r"[.,]\d{1,2}$", raw):  # (decimals: the last separator is the decimal point)
        whole, dec = raw[:-3 if raw[-3] in ".," else -2], raw[-2:].lstrip(".,")
        raw = re.sub(r"[.,]", "", whole) + "." + dec
    else:
        raw = re.sub(r"[.,]", "", raw)
    try:
        return float(raw)
    except ValueError:
        return None


def _fingerprint(text):
    return hashlib.sha1(re.sub(r"\s+", " ", text or "").strip().encode("utf-8", "ignore")).hexdigest()


def _fetch(url):
    from room_agent.computer import pages

    return pages.fetch(url)


# ---------------------------------------------------------------- adding
def add_page(url, condition="change", value="", every_min=60, notify="voice"):
    from room_agent.computer import browsers

    url = browsers.to_url(url) if url and not str(url).startswith("http") else url
    if not url:
        return "NEEDS: which page (an address, or 'this page' with it open in the browser)."
    condition = condition if condition in ("change", "contains", "price_below", "price_drop") else "change"
    with _lock:
        items = _load()
        if len(items) >= MAX_WATCHERS:
            return f"FAILED: already watching {MAX_WATCHERS} things (the limit). Stop one first."
    page = _fetch(url)
    if not page.ok:
        return f"FAILED: couldn't read {browsers.host(url)} to start watching it ({page.error}). Nothing set up."
    w = {"id": uuid.uuid4().hex[:8], "kind": "page", "url": page.final_url or url, "title": page.title[:80],
         "condition": condition, "value": str(value or ""), "every_min": max(MIN_EVERY_MIN["page"], int(every_min or 60)),
         "notify": "text" if notify == "text" else "voice", "created": time.time(), "last_check": time.time(),
         "fingerprint": _fingerprint(page.text), "start_price": parse_price(page.text)}
    if condition in ("price_below", "price_drop"):
        if w["start_price"] is None:
            return f"FAILED: couldn't find a price on {browsers.host(url)} (it may need JavaScript or a login). Not watching."
        if condition == "price_below" and not re.match(r"^\d+(\.\d+)?$", str(value).replace(",", "")):
            return "NEEDS: the price to wait for (a number)."
    if condition == "contains" and not w["value"].strip():
        return "NEEDS: the words to look for on the page."
    with _lock:
        _save(_load() + [w])
    what = {"change": "changes", "contains": f"shows \"{w['value']}\"", "price_drop": f"drops below {w['start_price']:g}",
            "price_below": f"is below {value}"}[condition]
    return (f"OK: watching \"{w['title'] or browsers.host(url)}\": I'll {'text' if w['notify'] == 'text' else 'tell'} you when "
            f"it {what} (checked every {w['every_min']} min, for {WATCH_DAYS} days).")


def add_email(query, every_min=60, notify="voice"):
    query = " ".join(str(query or "").split())
    if not query:
        return "NEEDS: what to look for in the email (who it's from, or words in it)."
    if not _google_ok():
        return "UNAVAILABLE: watching email needs Google connected (python main.py --connect google)."
    with _lock:
        items = _load()
        if len(items) >= MAX_WATCHERS:
            return f"FAILED: already watching {MAX_WATCHERS} things (the limit). Stop one first."
        items.append({"id": uuid.uuid4().hex[:8], "kind": "email", "query": query, "notify": "text" if notify == "text" else "voice",
                      "every_min": max(MIN_EVERY_MIN["email"], int(every_min or 60)), "created": time.time(),
                      "last_check": time.time(), "seen": []})
        _save(items)
    return f"OK: checking your email every {max(MIN_EVERY_MIN['email'], int(every_min or 60))} min for \"{query}\"; I'll tell you about new ones."


def listing():
    items = _load()
    if not items:
        return "OK: not watching anything."
    out = []
    for w in items:
        if w["kind"] == "page":
            out.append(f"the page \"{w['title'] or w['url']}\" ({w['condition'].replace('_', ' ')}{' ' + w['value'] if w['value'] else ''})")
        else:
            out.append(f"email matching \"{w['query']}\"")
    return "OK: watching: " + "; ".join(out) + "."


def stop(what):
    w = str(what or "").lower().strip()
    with _lock:
        items = _load()
        gone = items if w in ("all", "everything") else [
            i for i in items if w and (w in (i.get("title") or "").lower() or w in (i.get("url") or "").lower()
                                       or w in (i.get("query") or "").lower() or w == i["kind"])]
        if not gone:
            return f"FAILED: nothing being watched matches \"{what}\"."
        _save([i for i in items if i not in gone])
    return f"OK: stopped watching {len(gone)} thing{'s' if len(gone) != 1 else ''}."


# ---------------------------------------------------------------- checking
def _google_ok():
    try:
        from room_agent.integrations import provider

        g = provider("google")
        return g.connection_status() == "connected" and g.can("gmail", "read")
    except Exception:
        return False


def check_page(w, page=None):
    """-> the line to say if it triggered, else None (updates w)."""
    page = page or _fetch(w["url"])
    if not page.ok:
        w["errors"] = w.get("errors", 0) + 1
        return None
    w["errors"] = 0
    name = w["title"] or w["url"]
    if w["condition"] == "change":
        fp = _fingerprint(page.text)
        if fp != w["fingerprint"]:
            w["fingerprint"] = fp
            return f"The page \"{name}\" you asked me to watch has changed."
        return None
    if w["condition"] == "contains":
        if w["value"].lower() in page.text.lower():
            return f"The page \"{name}\" now says \"{w['value']}\"."
        return None
    price = parse_price(page.text)
    if price is None:
        return None
    limit = float(str(w["value"]).replace(",", "")) if w["condition"] == "price_below" else w["start_price"]
    if price < limit:
        return f"Price drop on \"{name}\": it's {price:g} now (it was {w['start_price']:g} when you asked)."
    return None


def check_email(w, found=None):
    if found is None:
        try:
            from room_agent.integrations import provider
            from room_agent.integrations.google.gmail import GmailService

            svc = GmailService(provider("google"))
            found = svc.search(f"{w['query']} after:{int(w['created'])}", 10)
        except Exception as e:
            log.info("watchers: email check failed (%s)", e)
            return None
    new = [m for m in found if m["id"] not in w["seen"]]
    if not new:
        return None
    w["seen"] = (w["seen"] + [m["id"] for m in new])[-200:]
    first = new[0]
    more = f" and {len(new) - 1} more" if len(new) > 1 else ""
    return f"New email matching \"{w['query']}\": from {first['from_name']} about {first['subject']}{more}."


def tick(now=None, fetch_page=None, fetch_mail=None):
    """Check every watcher that's due. -> [lines said]"""
    now = now or time.time()
    said = []
    with _lock:
        items = _load()
    keep = []
    for w in items:
        if now - w["created"] > WATCH_DAYS * 86400:
            log.info("watchers: %s expired", w.get("title") or w.get("query"))
            continue
        if now - w["last_check"] < w["every_min"] * 60:
            keep.append(w)
            continue
        w["last_check"] = now
        line = check_page(w, fetch_page(w["url"]) if fetch_page else None) if w["kind"] == "page" else \
            check_email(w, fetch_mail(w) if fetch_mail else None)
        if line:
            _notify(w, line, now)
            said.append(line)
            if w["kind"] == "page":
                continue  # (a page watch is done once it triggered)
        keep.append(w)
    with _lock:
        current = {i["id"] for i in _load()}
        _save([w for w in keep if w["id"] in current])  # (one stopped meanwhile stays stopped)
    return said


def _notify(w, line, now):
    if w.get("notify") == "text":
        from room_agent.phone import texts

        out = texts.text_me(line)
        if out.startswith("OK"):
            return
        log.info("watchers: text failed (%s), saying it instead", out[:80])
    from room_agent import triggers

    triggers.say(line, "watch", f"watch:{w['id']}:{int(now)}", now=now)


def _loop():
    while True:
        try:
            tick()
        except Exception as e:
            log.debug("watchers: check failed: %s", e)
        time.sleep(60)


def start():
    threading.Thread(target=_loop, name="watchers", daemon=True).start()
