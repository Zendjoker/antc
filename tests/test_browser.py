"""Browser control, offline: a simulated desktop (Opera, Chrome, Edge, Brave installed; windows with tabs and history;
pages with links, buttons and fields) stands in for Windows, the browsers and accessibility. Nothing touches the real
browsers, keyboard or mouse; no network.

Covers: browser choice (focused / named / recent / default / "the other one" / not installed), opening sites and
Google / YouTube searches in a new tab with verification, tabs and navigation, clicking results by number, elements by
name, typing (never into password fields), scrolling, copying links, stale page context, interruptions, failures.

Run:  .venv\\Scripts\\python -m tests.test_browser
"""

import itertools
import urllib.parse

from tests.harness import setup_env

setup_env()

from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor, journal  # noqa: E402
from room_agent.computer import browser_ops, browsers  # noqa: E402
from room_agent.computer.context import desk  # noqa: E402
from room_agent.computer.uia import El  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
core.ensure_loaded()
browser_ops.WAIT_NAV_S = 0.6


# ---------------------------------------------------------------- the simulated web
def page_for(url):
    """What a URL shows: title, links [(name, href)], buttons, fields, text."""
    u = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    h, q = browsers.host(url), urllib.parse.parse_qs(u.query)
    if h == "google.com" and u.path == "/search":
        query = q.get("q", [""])[0]
        return {"title": f"{query} - Google Search", "links": [
            ("Images", "https://www.google.com/imghp"), ("Sign in", "https://accounts.google.com/ServiceLogin"),
            ("Welcome to Python.org https://www.python.org", "https://www.python.org/"),
            ("Python Tutorial - W3Schools w3schools.com › python", "https://www.w3schools.com/python/"),
            ("Real Python Tutorials realpython.com", "https://realpython.com/"),
            ("Privacy", "https://policies.google.com/privacy")], "fields": [("Search", False)], "text": f"Results for {query}"}
    if h == "youtube.com" and u.path == "/results":
        query = q.get("search_query", [""])[0]
        return {"title": f"{query} - YouTube", "links": [
            ("Home", "https://www.youtube.com/"), ("", "https://www.youtube.com/watch?v=AAA"),
            ("Building Jarvis AI by Dev 1M views", "https://www.youtube.com/watch?v=AAA"),
            ("Jarvis in Python by Coder 300K views", "https://www.youtube.com/watch?v=BBB&t=1"),
            ("Coder channel", "https://www.youtube.com/@coder"),
            ("Real Jarvis AI demo by Lab 20K views", "https://www.youtube.com/watch?v=CCC")],
            "fields": [("Search", False)], "text": "videos"}
    if h == "youtube.com":
        return {"title": "YouTube", "links": [("Trending", "https://www.youtube.com/feed/trending")], "fields": [("Search", False)],
                "text": "YouTube home"}
    if h == "example.com" and u.path == "/login":
        return {"title": "Sign in - Example", "links": [], "fields": [("Email", False), ("Password", True)], "text": "Sign in",
                "buttons": ["Sign in", "Buy now", "I'm not a robot"]}
    if h == "example.com" and u.path == "/broken":
        return {"title": "Broken", "links": [("Dead link", "https://example.com/never")], "fields": [], "text": "x" * 300,
                "dead": True}
    return {"title": f"{h} page", "links": [("Docs", f"https://{h}/docs")], "fields": [], "text": f"This is {h}. " * 30,
            "buttons": ["Subscribe", "Load more"]}


class Tab:
    def __init__(self, url):
        self.history, self.i, self.reloads = [url], 0, 0

    @property
    def url(self):
        return self.history[self.i]

    def go(self, url):
        self.history = self.history[:self.i + 1] + [url]
        self.i += 1


class Win:
    def __init__(self, key, hwnd, urls):
        self.key, self.hwnd, self.tabs, self.active, self.scroll = key, hwnd, [Tab(u) for u in urls], 0, 0.0

    @property
    def tab(self):
        return self.tabs[self.active]

    def title(self):
        return f"{page_for(self.tab.url)['title']} - {browsers.KNOWN[self.key].name}" if self.tab.url else \
            f"New tab - {browsers.KNOWN[self.key].name}"


class Desktop:
    """Windows + browsers + accessibility, simulated."""

    def __init__(self):
        self.reset()

    def reset(self, front="opera", installed=("opera", "chrome", "edge", "brave"), default="opera", broken_launch=False,
              ignore_launch=False):
        self.have = {k: f"C:\\fake\\{k}.exe" for k in installed}
        self.default = default
        self.windows = {}
        self.ids = itertools.count(100)
        for k in ("opera", "chrome"):
            if k in self.have:
                self.add(k, ["https://news.example.org/today"])
        self.front = next((w.hwnd for w in self.windows.values() if w.key == front), None)
        self.launches, self.keys, self.typed = [], [], []
        self.broken_launch, self.ignore_launch = broken_launch, ignore_launch
        self.focus_ok, self.clip = True, ""
        self.focused_field = None
        desk.clear()

    def add(self, key, urls):
        w = Win(key, next(self.ids), urls)
        self.windows[w.hwnd] = w
        return w

    def win(self, key):
        return next((w for w in self.windows.values() if w.key == key), None)

    # ---- browsers.py hooks
    def installed(self, refresh=False):
        return dict(self.have)

    def default_key(self):
        return self.default

    def browser_windows(self, key=None):
        ws = sorted(self.windows.values(), key=lambda w: w.hwnd != self.front)
        return [(w.key, w.hwnd, w.title()) for w in ws if key is None or w.key == key]

    def foreground_browser(self):
        w = self.windows.get(self.front)
        return (w.key, w.hwnd) if w else (None, None)

    def read_state(self, hwnd):
        w = self.windows[hwnd]
        return {"url": w.tab.url, "title": w.title()}

    def title_of(self, hwnd):
        return self.windows[hwnd].title()

    def launch(self, exe, args):
        if self.broken_launch:
            raise OSError("cannot start")
        key = next(k for k, v in self.have.items() if v == exe)
        self.launches.append((key, args[-1]))
        if self.ignore_launch:
            return True
        w = self.win(key) or self.add(key, [])
        if not w.tabs:
            w.tabs.append(Tab(args[-1]))
        else:
            w.tabs.append(Tab(args[-1]))
            w.active = len(w.tabs) - 1
        return True


d = Desktop()
browsers.installed, browsers.default_key, browsers.browser_windows = d.installed, d.default_key, d.browser_windows
browsers.foreground_browser, browsers.read_state, browsers.title_of, browsers._launch = (
    d.foreground_browser, d.read_state, d.title_of, d.launch)


class FakeUIA:
    """Accessibility over the simulated pages: elements carry what they point at in `raw`."""
    ADDRESS_NAMES = ("address field",)

    def document(self, hwnd, wait=0):
        w = d.windows.get(hwnd)
        return None if w is None or not w.tab.url else {"doc": hwnd, "rid": (hwnd, w.tab.reloads, w.tab.url)}

    def find(self, root, kinds, limit=400):
        hwnd = root["doc"] if isinstance(root, dict) else root
        w = d.windows[hwnd]
        pg = page_for(w.tab.url)
        out = []
        if "link" in kinds:
            out += [El(name=n, kind="Hyperlink", href=hr, raw={"hwnd": hwnd, "href": hr, "dead": pg.get("dead")})
                    for n, hr in pg["links"]]
        if "button" in kinds:
            out += [El(name=b, kind="Button", raw={"hwnd": hwnd, "button": b}) for b in pg.get("buttons", [])]
        if "edit" in kinds:
            out += [El(name=f, kind="Edit", password=pw, raw={"hwnd": hwnd, "field": f, "value": ""})
                    for f, pw in pg["fields"]]
        if "edit" in kinds and not isinstance(root, dict):
            out.append(El(name="Address field", kind="Edit", value=w.tab.url, raw={"hwnd": hwnd, "address": True,
                                                                                   "value": w.tab.url}))
        if "tab" in kinds and not isinstance(root, dict):
            out += [El(name=page_for(tb.url)["title"] if tb.url else "New tab", kind="TabItem",
                       raw={"hwnd": hwnd, "tab": i}) for i, tb in enumerate(w.tabs)]
        return out

    def tabs(self, hwnd):
        w = d.windows[hwnd]
        return [(e.name, e, e.raw["tab"] == w.active) for e in self.find(hwnd, ["tab"])]

    def page_text(self, doc):
        return page_for(d.windows[doc["doc"]].tab.url)["text"]

    def invoke(self, el):
        r = el.raw
        w = d.windows[r["hwnd"]]
        if "href" in r:
            if not r.get("dead"):
                w.tab.go(r["href"])
            return "Invoke"
        if "tab" in r:
            w.active = r["tab"]
            return "Select"
        if "button" in r:
            if r["button"] == "Load more":
                w.tab.go(w.tab.url + "?page=2")
            return "Invoke"
        return ""

    def runtime_id(self, doc):
        return doc["rid"]

    def scroll_into_view(self, el):
        return True

    def focus(self, el):
        d.focused_field = el
        return True

    def focused(self):
        return d.focused_field

    def set_value(self, el, text):
        el.raw["value"] = text
        return True

    def read_value(self, el):
        return el.raw.get("value", "")

    def scroll_info(self, doc):
        w = d.windows[doc["doc"]]
        return None if "nopos" in w.tab.url else w.scroll

    def scroll(self, doc, down=True, big=False):
        w = d.windows[doc["doc"]]
        if "stuck" not in w.tab.url:
            w.scroll = min(100.0, w.scroll + 25) if down else max(0.0, w.scroll - 25)
        return True


class FakeInput:
    def foreground(self):
        return d.front

    def focus_window(self, hwnd, wait=0):
        if d.focus_ok:
            d.front = hwnd
        return d.focus_ok

    def hotkey(self, combo):
        d.keys.append(combo)
        w = d.windows[d.front]
        if combo == "alt+left" and w.tab.i > 0:
            w.tab.i -= 1
        elif combo == "alt+right" and w.tab.i < len(w.tab.history) - 1:
            w.tab.i += 1
        elif combo == "f5":
            w.tab.reloads += 1
        elif combo == "ctrl+t":
            w.tabs.append(Tab(""))
            w.active = len(w.tabs) - 1
        elif combo == "enter" and d.focused_field is not None and d.focused_field.raw.get("address"):
            w.tab.go(d.focused_field.raw["value"])
        elif combo == "enter" and d.focused_field is not None:
            v = d.focused_field.raw.get("value", "")
            w.tab.go(browsers.search_url(v, "youtube" if "youtube." in browsers.host(w.tab.url) else "google"))
        elif combo in ("end", "pagedown"):
            w.scroll = 100.0 if combo == "end" else min(100.0, w.scroll + 30)
        return True

    def type_text(self, text, delay=0):
        d.typed.append(text)
        return True

    def set_clipboard(self, text):
        d.clip = text
        return True

    def get_clipboard(self):
        return d.clip


browser_ops.uia, browser_ops.winput = FakeUIA(), FakeInput()


def run(name, args, said=""):
    rt.new_turn(said or name)
    return executor.execute(name, args)


def front(key):
    d.front = d.win(key).hwnd


# ---------------------------------------------------------------- 1-5. choosing the browser
print("Which browser:")
d.reset(front="opera")
r = run("open_url", {"site": "YouTube"}, "open YouTube")
t.check("A. Opera in front, 'open YouTube' -> opened in Opera, verified", r.success and d.launches == [("opera", "https://www.youtube.com")]
        and "Opera" in r.message, (r.message, d.launches))
t.check("...in a NEW tab: the tab that was open is still there", len(d.win("opera").tabs) == 2
        and d.win("opera").tabs[0].url == "https://news.example.org/today")
d.reset(front="opera")
r = run("open_url", {"site": "YouTube", "browser": "Chrome"}, "open YouTube in Chrome")
t.check("B. Opera in front, 'open YouTube in Chrome' -> Chrome", r.success and d.launches == [("chrome", "https://www.youtube.com")],
        (r.message, d.launches))
d.reset(front="chrome")
r = run("browser_search", {"query": "Python tutorials", "engine": "google"}, "search Google for Python tutorials")
t.check("C. Chrome in front, Google search -> in Chrome, results page verified", r.success and d.launches[0][0] == "chrome"
        and d.launches[0][1] == "https://www.google.com/search?q=Python+tutorials", (r.message, d.launches))
d.reset(front="opera")
r = run("browser_search", {"query": "Jarvis AI", "engine": "youtube"}, "search YouTube for Jarvis AI")
t.check("D. 'search YouTube for Jarvis AI' -> YouTube results in the right browser",
        r.success and d.launches == [("opera", "https://www.youtube.com/results?search_query=Jarvis+AI")], (r.message, d.launches))
d.reset(front=None)
d.front = None
r = run("open_url", {"site": "github.com"}, "open github.com")
t.check("no browser in front, none used recently -> the system default (Opera)", r.success and d.launches[0][0] == "opera", d.launches)
d.reset(front=None, default="edge")
d.front = None
desk.used_browser("chrome")
r = run("open_url", {"site": "reddit"}, "open reddit")
t.check("...but the one used most recently wins over the default", r.success and d.launches[0][0] == "chrome", d.launches)
d.reset(front="opera")
r = run("open_url", {"site": "youtube", "browser": "firefox"}, "open YouTube in Firefox")
t.check("a browser that isn't installed -> says so, opens nothing anywhere else", not r.success and not d.launches
        and "isn't installed" in r.message, (r.message, d.launches))
d.reset(front="opera")
desk.used_browser("chrome")
desk.used_browser("opera")
r = run("open_url", {"site": "youtube", "browser": "the other browser"}, "open this in the other browser")
t.check("'the other browser' -> the other one recently used (Chrome)", r.success and d.launches[0][0] == "chrome", (r.message, d.launches))
d.reset(front="opera")
desk.used_browser("opera")
r = run("open_url", {"site": "youtube", "browser": "the other browser"}, "open this in the other browser")
t.check("'the other browser' with no clear other one -> asks which", not r.success and "which browser" in r.message.lower(), r.message)
d.reset(front="opera", installed=("opera", "chrome"), default="opera")
r = run("open_url", {"site": "youtube", "browser": "opera gx"}, "open YouTube in Opera GX")
t.check("Opera GX named but only Opera installed -> not substituted", not r.success and not d.launches, r.message)

print("Addresses:")
t.check("site names -> their address", browsers.to_url("Gmail") == "https://mail.google.com"
        and browsers.to_url("the YouTube website") == "https://www.youtube.com")
t.check("bare domains -> https", browsers.to_url("github.com/foo") == "https://github.com/foo")
t.check("javascript:, file: and data: addresses are refused", all(browsers.to_url(u) is None for u in
        ("javascript:alert(1)", "file:///C:/Windows", "data:text/html,hi")))
t.check("search words are encoded, never pasted raw", browsers.search_url('C++ "quotes" & more') ==
        "https://www.google.com/search?q=C%2B%2B+%22quotes%22+%26+more")
d.reset(front="opera")
r = run("open_url", {"site": "javascript:alert(1)"}, "open javascript alert")
t.check("...and the tool refuses them without launching anything", not r.success and not d.launches, r.message)

# ---------------------------------------------------------------- 6-9. navigation, search, tabs
print("Navigation and tabs:")
d.reset(front="opera")
run("open_url", {"site": "youtube"}, "open YouTube")
r = run("browser_search", {"query": "AI assistants"}, "search for AI assistants")
w = d.win("opera")
t.check("'search for X' right after opening YouTube -> a YouTube search, in that same tab", r.success
        and "youtube.com/results" in w.tab.url and len(d.launches) == 1, (r.message, d.launches))
r = run("browser_navigate", {"action": "back"}, "go back")
t.check("'go back' -> the same tab goes back, verified by the address", r.success and w.tab.url == "https://www.youtube.com",
        (r.message, w.tab.url))
r = run("browser_navigate", {"action": "forward"}, "go forward")
t.check("'go forward' -> forward again", r.success and "results" in w.tab.url, r.message)
r = run("browser_navigate", {"action": "refresh"}, "refresh this page")
t.check("'refresh' -> verified by the page reloading", r.success and w.tab.reloads == 1, r.message)
n = len(w.tabs)
r = run("browser_navigate", {"action": "new_tab"}, "open another tab")
t.check("'open another tab' -> one more tab, checked", r.success and len(w.tabs) == n + 1, r.message)
r = run("browser_navigate", {"action": "switch_tab", "tab": "YouTube"}, "switch to my YouTube tab")
t.check("'switch to my YouTube tab' -> that tab selected, window shows it", r.success and "YouTube" in w.title(), (r.message, w.title()))
w.tab.history, w.tab.i = [w.tab.url], 0
r = run("browser_navigate", {"action": "back"}, "go back")
t.check("'go back' with nowhere to go -> not confirmed, not claimed", not r.success and "not confirmed" in r.message, r.message)
d.focus_ok = False
r = run("browser_navigate", {"action": "back"}, "go back")
t.check("window won't come to the front -> nothing pressed, says so", not r.success and "front" in r.message, r.message)

# ---------------------------------------------------------------- 10-12. clicking, typing, scrolling
print("Clicking:")
d.reset(front="opera")
run("browser_search", {"query": "python", "engine": "google"}, "search google for python")
w = d.win("opera")
r = run("browser_click", {"number": 2}, "open the second result")
t.check("G. 'click the second result' -> W3Schools (Google's own links skipped), navigation verified", r.success
        and w.tab.url == "https://www.w3schools.com/python/", (r.message, w.tab.url))
run("browser_search", {"query": "jarvis", "engine": "youtube"}, "search youtube for jarvis")
r = run("browser_click", {"target": "second"}, "open the second video")
t.check("'open the second video' on YouTube -> the second distinct video (thumbnail + title = one)", r.success
        and "watch?v=BBB" in d.win("opera").tab.url, (r.message, d.win("opera").tab.url))
run("browser_search", {"query": "jarvis", "engine": "youtube"}, "search youtube for jarvis")
r = run("browser_click", {"number": 9}, "open the ninth one")
t.check("a result number that isn't there -> says how many there are", not r.success and "only 3" in r.message, r.message)
d.win("opera").tab.go("https://example.com/login")
r = run("browser_click", {"target": "Sign in"}, "click sign in")
t.check("an element found by its label (a button), activated", "Sign in" in r.message, r.message)
r = run("browser_click", {"target": "Buy now"}, "click buy now")
t.check("'Buy now' isn't clicked by the normal click", not r.success and "browser_click_sensitive" in r.message, r.message)
r = run("browser_click_sensitive", {"target": "Buy now"}, "click buy now")
t.check("...and the sensitive click asks them first (nothing clicked yet)", r.message.startswith("NEEDS_CONFIRMATION"), r.message)
rt.pending = None
r = run("browser_click", {"target": "I'm not a robot"}, "click I'm not a robot")
t.check("a CAPTCHA is never clicked", not r.success and "CAPTCHA" in r.message, r.message)
r = run("browser_click", {"target": "Launch rockets"}, "click launch rockets")
t.check("something not on the page -> not clicked, says it isn't there", not r.success and "nothing called" in r.message, r.message)
d.win("opera").tab.go("https://example.com/broken")
r = run("browser_click", {"target": "Dead link"}, "click dead link")
t.check("J. a click that goes nowhere -> 'not confirmed', never 'done'", not r.success and "not confirmed" in r.message, r.message)

print("Typing:")
d.win("opera").tab.go("https://example.com/login")
r = run("browser_type", {"text": "me@example.com", "field": "Email"}, "type my email")
t.check("typed into a field by its label, checked by reading it back", r.success and "checked" in r.message, r.message)
r = run("browser_type", {"text": "hunter2", "field": "Password"}, "type hunter2 in the password")
t.check("a password field -> refused, nothing typed", not r.success and "password" in r.message.lower() and not d.typed, r.message)
run("open_url", {"site": "youtube"}, "open youtube")
d.focused_field = None
r = run("browser_type", {"text": "Jarvis AI assistant", "submit": True}, "type Jarvis AI assistant and search")
t.check("'type Jarvis AI assistant' + Enter in the search box -> results page, verified", r.success
        and "search_query=Jarvis+AI+assistant" in d.win("opera").tab.url, (r.message, d.win("opera").tab.url))

print("Scrolling and copying:")
w = d.win("opera")
w.scroll = 0.0
r = run("browser_scroll", {"direction": "down"}, "scroll down")
t.check("'scroll down' -> the page position moved (25%)", r.success and w.scroll == 25.0, (r.message, w.scroll))
r = run("browser_scroll", {"direction": "bottom"}, "scroll to the bottom")
t.check("'scroll to the bottom' -> checked at 100%", r.success and w.scroll == 100.0, r.message)
w.tab.go("https://stuck.example.com/nopos")
r = run("browser_scroll", {"direction": "down"}, "scroll down")
t.check("a page that doesn't report its position -> 'not confirmed'", not r.success and "not confirmed" in r.message, r.message)
run("browser_search", {"query": "python", "engine": "google"}, "search google for python")
r = run("copy_link", {"number": 1}, "copy the first link")
t.check("'copy the first result's link' -> clipboard has exactly that URL", r.success and d.clip == "https://www.python.org/", (r.message, d.clip))

# ---------------------------------------------------------------- context, interruptions, failures
print("Context, interruptions, failures:")
d.reset(front="opera")
run("browser_search", {"query": "python", "engine": "google"}, "search google for python")
browser_ops.results(d.win("opera").hwnd)
t.check("results remembered for the page they're on", desk.results_for(d.win("opera").tab.url) is not None)
d.win("opera").tab.go("https://news.example.org/other")  # (they clicked somewhere themselves)
t.check("they moved on by hand -> the old page's results aren't used", desk.results_for(d.win("opera").tab.url) is None)
r = run("browser_click", {"number": 1}, "open the first one")
t.check("...'open the first one' looks at the page that's really showing (no stale Google result opened)",
        "python.org" not in d.win("opera").tab.url and "this page" in r.message, (r.message, d.win("opera").tab.url))
n_tabs = len(d.win("opera").tabs)
r = run("open_url", {"site": "github.com", "current_tab": True}, "open github here")
t.check("'open github here' -> the current tab goes there (no new tab)", r.success and "github.com" in d.win("opera").tab.url
        and len(d.win("opera").tabs) == n_tabs, r.message)
rt.new_turn("open the second result")
rt.turn.cancel.set()
r = executor.execute("browser_click", {"number": 2})
t.check("I/18. interrupted -> stops, nothing clicked, says so", not r.success and "interrupted" in r.message, r.message)
d.reset(front="opera", broken_launch=True)
r = run("open_url", {"site": "youtube"}, "open youtube")
t.check("the browser can't be started -> FAILED, nothing claimed", not r.success and "couldn't be started" in r.message, r.message)
d.reset(front="opera", ignore_launch=True)
browsers_wait = browsers.wait_loaded
browsers.wait_loaded = lambda key, url, before, query="", timeout=8.0: browsers_wait(key, url, before, query, timeout=0.5)
r = run("open_url", {"site": "youtube"}, "open youtube")
browsers.wait_loaded = browsers_wait
t.check("the page never shows up -> 'not confirmed', never 'opened'", not r.success and "not confirmed" in r.message, r.message)
e = journal.recent(1)[0]
t.check("...and the action journal records it as UNKNOWN (it may have opened; never COMPLETED)", e["state"] == "UNKNOWN",
        e["state"])
d.reset(front="opera")
r = run("browser_read_page", {}, "read this page")
t.check("E. 'read this page' -> the real text of the page in front", r.success and "This is news.example.org" in r.message, r.message[:200])
t.check("...page reading is marked private (not logged / learned / remembered)", core.get("browser_read_page").private)
t.check("the desktop context is in memory only and expires (15 min)", desk.lines() and not hasattr(desk, "save"))
print("Page text can't drive actions:")
d.reset(front="opera")
d.win("opera").tab.go("https://example.com/login")
rt.pending = None
r = run("browser_type", {"text": "1 Main St", "field": "Email"}, "what does this page say?")
t.check("typing that their words didn't ask for (e.g. a page saying 'type your address') -> asks first, nothing typed",
        r.message.startswith("NEEDS_CONFIRMATION") and not d.focused_field, r.message)
rt.pending = None
r = run("browser_click", {"target": "Sign in"}, "summarize this page")
t.check("a click their words didn't ask for -> asks first", r.message.startswith("NEEDS_CONFIRMATION"), r.message)
rt.pending = None
r = run("open_url", {"site": "evil.example.net"}, "what's the weather like")
t.check("opening a site nobody asked for -> asks first, nothing launched", r.message.startswith("NEEDS_CONFIRMATION")
        and not d.launches, r.message)
rt.pending = None
t.done("BROWSER")
