"""What can be done inside a browser window, grounded in what's really there and checked afterwards.

Every action finds its target through accessibility (computer/uia.py), acts on it directly when it can (Invoke a link,
SetValue a field, select a tab) and only falls back to keys when that's the only way (back, forward, refresh, new tab,
Enter). Afterwards it checks the effect: the address changed, the value reads back, the scroll position moved, the tab
count grew. If nothing observable changed it says "not confirmed" (a FAILED result), never "done".

Never: typing into password / one-time-code / card fields, clicking a CAPTCHA or "I'm not a robot", closing tabs. Buttons
that send, buy, pay, delete or post need their own confirmed call (browser_click_sensitive).
"""

import logging
import re
import time
import urllib.parse

from room_agent import runtime as rt
from room_agent.computer import browsers, uia, winput
from room_agent.computer.context import desk

log = logging.getLogger("room-agent")

CLICKABLE = ["link", "button", "menuitem", "tab", "checkbox", "radio", "listitem", "combo"]
FIELDS = ["edit", "combo"]
NEVER_CLICK = re.compile(r"captcha|i'?m not a robot|verify (that )?you('re| are) (human|not a robot)|recaptcha|hcaptcha", re.I)
SENSITIVE_CLICK = re.compile(
    r"\b(buy|purchase|pay|checkout|check out|place (your )?order|order now|subscribe|donate|send|post|publish|tweet|reply|"
    r"submit|delete|remove|erase|unsubscribe|sign out|log ?out|deactivate|transfer|confirm (payment|purchase|order)|"
    r"add (a )?(payment|card)|install|grant|allow access|accept all)\b", re.I)
SECRET_FIELD = re.compile(r"password|passcode|one[- ]time|otp|2fa|two[- ]factor|verification code|security code|\bpin\b|"
                          r"\bcvv\b|\bcvc\b|card number|credit card|social security|\bssn\b", re.I)
MESSAGE_FIELD = re.compile(r"message|reply|comment|compose|write a|tweet|post|chat|email body", re.I)
ORDINAL = {"first": 1, "1st": 1, "one": 1, "second": 2, "2nd": 2, "two": 2, "third": 3, "3rd": 3, "three": 3, "fourth": 4,
           "4th": 4, "four": 4, "fifth": 5, "5th": 5, "five": 5, "sixth": 6, "6th": 6, "six": 6, "seventh": 7, "eighth": 8,
           "ninth": 9, "tenth": 10, "last": -1}
WAIT_NAV_S = 6.0


def interrupted():
    """They talked over it, or the turn was cancelled: stop between steps."""
    eng = rt.engine
    return bool(rt.turn.cancel.is_set() or getattr(rt.turn, "interrupted", False)
                or (eng is not None and getattr(eng, "interrupted", None) is not None and eng.interrupted.is_set()))


# ---------------------------------------------------------------- which window
def target_window(browser=""):
    """(key, hwnd) of the browser window to act on, or (None, problem text)."""
    want = browsers.named_in(browser) if browser else None
    key, hwnd = browsers.foreground_browser()
    if hwnd and (not want or key == want):
        return key, hwnd
    p = desk.current_page()
    wins = browsers.browser_windows(want) if want else []
    if not want and p and any(h == p["hwnd"] for _, h, _ in browsers.browser_windows(p["browser"])):
        return p["browser"], p["hwnd"]
    if not want and not wins:
        recent = desk.current_browser()
        wins = browsers.browser_windows(recent) if recent else browsers.browser_windows()
    if wins:
        return wins[0][0], wins[0][1]
    name = browsers.KNOWN[want].name if want else "a browser"
    return None, f"FAILED: there's no {name} window open to do that in. Offer to open the page first."


def _state(hwnd):
    return browsers.read_state(hwnd)


def wait_change(hwnd, before, timeout=WAIT_NAV_S, check=None):
    """Wait until the tab's address (or title) changes from `before`. -> the new state, or None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if interrupted():
            return None
        st = _state(hwnd)
        if check is not None:
            if check(st):
                return st
        elif (st["url"] and st["url"] != before["url"]) or (st["title"] and st["title"] != before["title"]):
            return st
        time.sleep(0.25)
    return None


def _norm_url(u):
    u = str(u or "").strip()
    if u and not re.match(r"^[a-z]+:", u, re.I):
        u = "https://" + u
    return u.rstrip("/")


# ---------------------------------------------------------------- reading
def read_page(hwnd, key, max_chars=6000):
    """{"url", "title", "text", "links", "how"} of the tab showing now. Accessibility first (what they really see,
    signed-in pages included, never sent anywhere), the public copy of the URL as a fallback."""
    st = _state(hwnd)
    doc = uia.document(hwnd)
    text = uia.page_text(doc) if doc is not None else ""
    how = "the page as shown in the browser"
    if len(text) < 200 and st["url"]:
        from room_agent.computer import pages

        page = pages.fetch(_norm_url(st["url"]))
        if page.ok and len(page.text) > len(text):
            text, how = page.text, "a fresh copy of the page from its address (not the logged-in view)"
    text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()
    desk.saw_page(key, hwnd, st["url"], st["title"])
    desk.did_read(st["url"], st["title"], text)
    return {"url": st["url"], "title": st["title"], "text": text[:max_chars], "truncated": len(text) > max_chars,
            "how": how, "chars": len(text)}


# ---------------------------------------------------------------- results on a page
def results(hwnd, url=None):
    """The numbered main results on a search / listing page, as the user sees them: [{"n", "title", "href", "el"}]."""
    url = url if url is not None else _state(hwnd)["url"]
    doc = uia.document(hwnd)
    if doc is None:
        return []
    links = [e for e in uia.find(doc, ["link"], limit=600) if e.href.startswith("http")]
    h = browsers.host(url)
    if "youtube." in h:
        picked = [e for e in links if "/watch?v=" in e.href or "/shorts/" in e.href]
    elif "google." in h and "/search" in _norm_url(url):
        picked = [e for e in links if not browsers.host(e.href).endswith(("google.com", "gstatic.com", "googleusercontent.com"))
                  and "webcache" not in e.href and len(e.name) > 8]
    elif "bing.com" in h or "duckduckgo.com" in h:
        picked = [e for e in links if not browsers.same_site(e.href, url) and len(e.name) > 8]
    else:
        picked = [e for e in links if len(e.name) > 15]
    out, seen = [], set()
    for e in picked:
        key = e.href.split("&")[0] if "youtube." in h else e.href
        if key in seen:
            continue  # (a thumbnail and a title link to the same video: one result)
        seen.add(key)
        out.append({"n": len(out) + 1, "title": _clean_title(e.name), "href": e.href, "el": e})
    desk.set_results(url, [{k: v for k, v in r.items() if k != "el"} for r in out[:20]])
    return out


def _clean_title(name):
    t = re.split(r"\s+(https?://|[a-z0-9.-]+\.[a-z]{2,}\s*›)", name)[0]
    return re.sub(r"\s+", " ", t).strip()[:120]


def ordinal(text):
    t = str(text or "").lower()
    m = re.search(r"\b(?:number\s+|#\s*)?(\d{1,2})(?:st|nd|rd|th)?\b", t)
    if m and not re.search(r"\d{3,}", t):
        return int(m.group(1))
    return next((n for w, n in ORDINAL.items() if re.search(rf"\b{w}\b", t)), None)


# ---------------------------------------------------------------- finding an element by what it is
def _score(name, wanted):
    a, b = name.lower(), wanted.lower().strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if b in a:
        return 0.8 - min(len(a) - len(b), 200) / 1000
    wa, wb = set(re.findall(r"\w+", a)), set(re.findall(r"\w+", b))
    return 0.6 * len(wa & wb) / max(len(wb), 1) if wb else 0.0


def find_element(hwnd, wanted, kinds=None):
    """The best visible, enabled match for what they called it, or None. Page elements first, then browser UI."""
    doc = uia.document(hwnd)
    pools = ([uia.find(doc, kinds or CLICKABLE, limit=800)] if doc is not None else []) + [uia.find(hwnd, kinds or CLICKABLE, limit=300)]
    for pool in pools:
        scored = sorted(((_score(e.name, wanted), e) for e in pool if e.enabled), key=lambda se: (-se[0], se[1].offscreen))
        if scored and scored[0][0] >= 0.5:
            return scored[0][1]
    return None


# ---------------------------------------------------------------- clicking
def click(hwnd, key, target="", number=None, sensitive_ok=False):
    """Click a result by number, or an element by its name. -> tool result text."""
    if interrupted():
        return "FAILED: stopped: they interrupted, so nothing was clicked."
    before = _state(hwnd)
    el, label = None, ""
    n = number if number else (ordinal(target) if target and re.search(r"\b(result|video|link|one|item|option)\b|^\W*\w+\W*$",
                                                                        str(target), re.I) else None)
    if n:
        items = results(hwnd, before["url"])
        if not items:
            return "FAILED: there are no results on this page to pick from (nothing was clicked). Ask what to click."
        if n == -1:
            n = len(items)
        if n > len(items):
            return f"FAILED: there are only {len(items)} results on this page, so there's no number {n}. Nothing was clicked."
        el, label = items[n - 1]["el"], f"result {n}, \"{items[n - 1]['title'][:70]}\""
    elif target:
        el = find_element(hwnd, target)
        if el is None:
            return (f"FAILED: there's nothing called \"{target}\" on this page that can be clicked (checked the page's "
                    "links and buttons). Nothing was clicked.")
        label = f"\"{el.name[:70]}\""
    else:
        return "NEEDS: what to click (a result number, or what the button or link says)."
    if NEVER_CLICK.search(el.name):
        return "FAILED: that's a CAPTCHA / human check: I never click those. They have to do it themselves."
    if SENSITIVE_CLICK.search(el.name) and not sensitive_ok:
        return (f"FAILED: {label} looks like it sends, buys, posts or deletes something, so it wasn't clicked. If they "
                "really want it, call browser_click_sensitive with the same target (it asks them to confirm first).")
    if not el.enabled:
        return f"FAILED: {label} is greyed out (disabled), so it can't be clicked right now."
    if el.offscreen:
        uia.scroll_into_view(el)
    how = uia.invoke(el)
    if not how:
        return f"FAILED: {label} doesn't respond to being activated through accessibility. Nothing was clicked."
    desk.touched(el.name, el.kind)
    if el.kind == "Hyperlink" and el.href:
        st = wait_change(hwnd, before, check=lambda s: s["url"] and browsers.same_site(s["url"], el.href)
                         and _norm_url(s["url"]) != _norm_url(before["url"]))
        st = st or _new_tab_with(key, el.href, before["url"])
        if st is None:
            return (f"FAILED: not confirmed: clicked {label}, but the browser never went to {browsers.host(el.href)}. "
                    "Don't say it opened.")
        desk.saw_page(key, st.get("hwnd", hwnd), st["url"], st["title"])
        return f"OK: clicked {label}; now on \"{st['title'][:80]}\" ({browsers.host(st['url'])})."
    st = wait_change(hwnd, before, timeout=2.5)
    if st is not None:
        desk.saw_page(key, hwnd, st["url"], st["title"])
        return f"OK: clicked {label}; the page changed to \"{st['title'][:80]}\"."
    return (f"FAILED: not confirmed: {label} was activated, but nothing visible changed (same page). Tell them you "
            "pressed it but can't see an effect; don't say it worked.")


def _new_tab_with(key, href, before_url):
    """A link that opened in a new tab/window: a window now showing exactly that page (not the one we were on)."""
    want = _norm_url(href).split("#")[0]
    for _, h, _ in browsers.browser_windows(key):
        st = _state(h)
        now = _norm_url(st["url"]).split("#")[0]
        if st["url"] and now != _norm_url(before_url) and (now == want or now.startswith(want)):
            return {**st, "hwnd": h}
    return None


def copy_link(hwnd, target="", number=None):
    n = number or ordinal(target)
    if n:
        items = results(hwnd)
        if not items or n > len(items) or n < -1:
            return "FAILED: there's no such result on this page to copy."
        href, name = items[n - 1 if n > 0 else -1]["href"], items[n - 1 if n > 0 else -1]["title"]
    elif target and target.lower() not in ("this", "this page", "it", "the page", "that"):
        el = find_element(hwnd, target, ["link"])
        if el is None or not el.href:
            return f"FAILED: no link called \"{target}\" on this page."
        href, name = el.href, el.name
    else:
        href, name = _norm_url(_state(hwnd)["url"]), "this page"
    if not href or not winput.set_clipboard(href) or winput.get_clipboard() != href:
        return "FAILED: couldn't put the link on the clipboard."
    return f"OK: copied the link to {name[:60]} ({browsers.host(href)}) to the clipboard."


# ---------------------------------------------------------------- typing
def type_text(hwnd, key, text, field="", submit=False, sensitive_ok=False):
    """Fill a field (by name, or the focused / main search field) and optionally press Enter. Checked by reading the
    value back; a submit is checked by the page changing."""
    if interrupted():
        return "FAILED: stopped: they interrupted, so nothing was typed."
    el = None
    if field:
        el = find_element(hwnd, field, FIELDS)
    if el is None:
        f = uia.focused()
        if f is not None and f.kind in ("Edit", "ComboBox") and winput.foreground() == hwnd and not _is_address_bar(f):
            el = f
    if el is None and not field:
        doc = uia.document(hwnd)
        fields = [e for e in uia.find(doc, FIELDS, limit=200) if e.enabled and not e.offscreen] if doc is not None else []
        el = next((e for e in fields if re.search(r"search", e.name, re.I)), None) or (fields[0] if len(fields) == 1 else None)
    if el is None:
        return (f"FAILED: couldn't find {'a field called ' + repr(field) if field else 'a text field to type into'} on this "
                "page. Nothing was typed. Ask them which field (or to click into it).")
    if el.password or SECRET_FIELD.search(el.name):
        return "FAILED: that's a password / code / card field: I never type into those. They have to type it themselves."
    if submit and MESSAGE_FIELD.search(el.name) and not sensitive_ok:
        return ("FAILED: that field sends a message or post, so it wasn't submitted. Type it without submitting, or ask them "
                "to confirm and use browser_click_sensitive on the send button.")
    before = _state(hwnd)
    ok = uia.set_value(el, text) and _matches(uia.read_value(el), text)
    if not ok:  # (some fields only take keystrokes)
        if not winput.focus_window(hwnd) or not uia.focus(el):
            return "FAILED: couldn't put the cursor in that field (the window or field wouldn't take focus). Nothing typed."
        winput.hotkey("ctrl+a")
        winput.type_text(text)
        time.sleep(0.15)
        ok = _matches(uia.read_value(el), text)
    if not ok:
        return f"FAILED: not confirmed: typed into \"{el.name[:50] or 'the field'}\", but it doesn't show the text afterwards."
    desk.touched(el.name, el.kind)
    if not submit:
        return f"OK: typed it into \"{el.name[:50] or 'the field'}\" (checked: the field shows it). Not submitted."
    if not winput.focus_window(hwnd) or not uia.focus(el) or not winput.hotkey("enter"):
        return "FAILED: typed it, but couldn't press Enter in that window, so it wasn't submitted."
    st = wait_change(hwnd, before)
    if st is None:
        return "FAILED: not confirmed: typed it and pressed Enter, but the page didn't change."
    desk.saw_page(key, hwnd, st["url"], st["title"])
    return f"OK: typed it and pressed Enter; now on \"{st['title'][:80]}\"."


def _is_address_bar(el):
    return any(n in el.name.lower() for n in uia.ADDRESS_NAMES)


def _matches(value, text):
    return " ".join(str(value or "").split()).lower().find(" ".join(str(text).split()).lower()) >= 0


# ---------------------------------------------------------------- scrolling
def scroll(hwnd, direction="down", amount="some"):
    """Scroll the page. Checked by the page's scroll position (accessibility) moving."""
    if interrupted():
        return "FAILED: stopped: they interrupted."
    doc = uia.document(hwnd)
    if doc is None:
        return "FAILED: couldn't find the page in that window to scroll."
    down = direction.lower() not in ("up", "top")
    if direction.lower() in ("top", "bottom"):
        if not winput.focus_window(hwnd):
            return "FAILED: couldn't bring the browser to the front to scroll."
        before = uia.scroll_info(doc)
        winput.hotkey("end" if down else "home")
        time.sleep(0.4)
        after = uia.scroll_info(doc)
        if before is not None and after is not None and after != before:
            return f"OK: scrolled to the {'bottom' if down else 'top'} ({after:.0f}% down the page)."
        if after is not None and after == (100.0 if down else 0.0):
            return f"OK: it's already at the {'bottom' if down else 'top'}."
        return "FAILED: not confirmed: pressed End/Home, but the page position didn't change."
    big = amount in ("lot", "page", "a lot", "much", "more")
    steps = 3 if amount in ("a lot", "lot", "much") else 1
    before = uia.scroll_info(doc)
    if before is not None and before >= 99.9 and down:
        return "OK: nothing to do: it's already at the bottom of the page."
    if before is not None and before <= 0.1 and not down:
        return "OK: nothing to do: it's already at the top of the page."
    sent = all(uia.scroll(doc, down, big=True if big else False) for _ in range(steps))
    time.sleep(0.25)
    after = uia.scroll_info(doc)
    if not sent or before is None or after is None or after == before:  # (the page doesn't scroll by accessibility: wheel)
        if not winput.focus_window(hwnd):
            return "FAILED: couldn't bring the browser to the front to scroll."
        before = uia.scroll_info(doc) if before is None else before
        winput.hotkey("pagedown" if down else "pageup")
        time.sleep(0.35)
        after = uia.scroll_info(doc)
    if before is not None and after is not None and after != before:
        return f"OK: scrolled {'down' if down else 'up'} ({after:.0f}% down the page)."
    if after is None:
        return ("FAILED: not confirmed: sent the scroll, but this page doesn't report its position, so I can't tell if it "
                "moved. Say you tried, not that it worked.")
    return "FAILED: not confirmed: the page didn't move (it may not scroll, or it's at the end)."


# ---------------------------------------------------------------- navigation and tabs
def navigate(hwnd, key, action, tab=""):
    if interrupted():
        return "FAILED: stopped: they interrupted."
    action = str(action or "").lower().replace(" ", "_")
    b = browsers.KNOWN[key]
    before = _state(hwnd)
    if action == "switch_tab":
        return switch_tab(hwnd, key, tab)
    combo = {"back": "alt+left", "forward": "alt+right", "refresh": "f5", "new_tab": "ctrl+t"}.get(action)
    if not combo:
        return f"FAILED: '{action}' isn't something I can do in the browser (back, forward, refresh, new tab, switch tab)."
    if not winput.focus_window(hwnd):
        return f"FAILED: couldn't bring {b.name} to the front, so nothing was pressed."
    if action == "new_tab":
        n_before = len(uia.tabs(hwnd))
        winput.hotkey(combo)
        deadline = time.time() + 3
        while time.time() < deadline:
            if len(uia.tabs(hwnd)) > n_before:
                desk.saw_page(key, hwnd, "", "New tab")
                return f"OK: opened a new tab in {b.name}."
            time.sleep(0.2)
        return f"FAILED: not confirmed: pressed Ctrl+T in {b.name}, but no new tab appeared."
    if action == "refresh":
        doc = uia.document(hwnd)
        rid = uia.runtime_id(doc) if doc is not None else ()
        winput.hotkey(combo)
        deadline = time.time() + WAIT_NAV_S
        while time.time() < deadline:
            time.sleep(0.3)
            d2 = uia.document(hwnd, wait=0.3)
            if d2 is not None and rid and uia.runtime_id(d2) != rid:
                return f"OK: reloaded \"{_short(before['title'], b)}\"."
        return "FAILED: not confirmed: pressed F5, but couldn't see the page reload."
    winput.hotkey(combo)
    st = wait_change(hwnd, before)
    if st is None:
        return (f"FAILED: not confirmed: pressed {'Back' if action == 'back' else 'Forward'}, but the page didn't change "
                f"(maybe there's no page to go {'back' if action == 'back' else 'forward'} to).")
    desk.saw_page(key, hwnd, st["url"], st["title"])
    return f"OK: went {action}; now on \"{_short(st['title'], b)}\" ({browsers.host(st['url'])})."


def go_here(hwnd, key, url):
    """Load `url` in the tab that's showing now (their 'here' / 'in this tab', or a search on the site they're on):
    through the address bar, checked by the address changing to it."""
    if interrupted():
        return "FAILED: stopped: they interrupted."
    b = browsers.KNOWN[key]
    before = _state(hwnd)
    if not winput.focus_window(hwnd):
        return f"FAILED: couldn't bring {b.name} to the front, so the page wasn't changed."
    bar = next((e for e in uia.find(hwnd, ["edit"], limit=40) if any(n in e.name.lower() for n in uia.ADDRESS_NAMES)), None)
    if bar is None or not uia.set_value(bar, url) or not uia.focus(bar):
        winput.hotkey("ctrl+l")  # (the address bar in every browser)
        winput.type_text(url)
    winput.hotkey("enter")
    st = wait_change(hwnd, before, check=lambda s: s["url"] and browsers.same_site(s["url"], url)
                     and _norm_url(s["url"]) != _norm_url(before["url"]))
    if st is None:
        return f"FAILED: not confirmed: typed the address into {b.name}, but the tab didn't go to {browsers.host(url)}."
    desk.saw_page(key, hwnd, st["url"], st["title"])
    return f"OK: loaded {browsers.host(url)} in the current {b.name} tab; it's showing \"{_short(st['title'], b)}\"."


def switch_tab(hwnd, key, wanted):
    b = browsers.KNOWN[key]
    if not str(wanted or "").strip():
        return "NEEDS: which tab (what's on it)?"
    candidates = []
    for k, h, _ in browsers.browser_windows(key):
        for name, el, selected in uia.tabs(h):
            candidates.append((_score(name, wanted), name, el, h, selected))
    if not candidates:
        return f"FAILED: couldn't read {b.name}'s tabs."
    score, name, el, h, selected = max(candidates, key=lambda c: c[0])
    if score < 0.5:
        return f"FAILED: no {b.name} tab matches \"{wanted}\". Open tabs: " + "; ".join(c[1][:40] for c in candidates[:8]) + "."
    if not uia.invoke(el):
        return f"FAILED: couldn't select the tab \"{name[:60]}\"."
    winput.focus_window(h)
    deadline = time.time() + 2.5
    while time.time() < deadline:
        if name[:20].lower() in browsers.title_of(h).lower():
            st = _state(h)
            desk.saw_page(key, h, st["url"], st["title"])
            return f"OK: switched to the \"{name[:70]}\" tab in {b.name}."
        time.sleep(0.15)
    return f"FAILED: not confirmed: selected the \"{name[:60]}\" tab, but the window doesn't show it."


def _short(title, b):
    return browsers._short_title(title, b)


def site_search_url(current_url, query):
    """'Search this website for X' -> a search limited to the site they're on."""
    h = browsers.host(current_url)
    if "youtube." in h:
        return browsers.search_url(query, "youtube")
    if "github.com" in h:
        return browsers.search_url(query, "github")
    if "wikipedia.org" in h:
        return browsers.search_url(query, "wikipedia")
    if "amazon." in h:
        return browsers.search_url(query, "amazon")
    if "reddit.com" in h:
        return browsers.search_url(query, "reddit")
    return browsers.search_url(query, "google", site=h) if h else None


def quote(q):
    return urllib.parse.quote_plus(q)
