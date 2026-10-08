"""Short-lived desktop context: which browser and page were used, the results on that page, the last page read, the
research in progress. In memory only (never long-term memory, no browsing history kept), and it expires:

    - anything older than CONTEXT_TTL_S is forgotten
    - a remembered page is only trusted while the browser still shows it: when its address changed (they clicked
      around themselves), the remembered results for the old page are dropped instead of being used
"""

import threading
import time

CONTEXT_TTL_S = 15 * 60
MAX_NAV = 10


class DesktopContext:
    def __init__(self):
        self._lock = threading.Lock()
        self.clear()

    def clear(self):
        self.browsers = {}      # key -> last time used or seen in front
        self.page = None        # {"browser", "hwnd", "url", "title", "at"}
        self.results = None     # {"url": page they're on, "items": [{"n", "title", "href"}], "at"}
        self.read = None        # {"url", "title", "excerpt", "at"} the last page read
        self.element = None     # {"name", "kind", "at"} the last element acted on
        self.research = None    # the last research report (computer/research.py Report)
        self.nav = []           # recent navigation: [(at, what)]
        self.screen = None      # the last screenshot's analysis (computer/screen.py Shot + elements), never the image

    def _fresh(self, item, max_age=CONTEXT_TTL_S):
        return item is not None and time.time() - item["at"] < max_age

    # -------- browsers
    def used_browser(self, key):
        with self._lock:
            self.browsers[key] = time.time()

    def current_browser(self, max_age=CONTEXT_TTL_S):
        live = {k: t for k, t in self.browsers.items() if time.time() - t < max_age}
        return max(live, key=live.get) if live else None

    def recent_browsers(self, max_age=CONTEXT_TTL_S):
        return [k for k, t in sorted(self.browsers.items(), key=lambda kv: -kv[1]) if time.time() - t < max_age]

    # -------- pages
    def saw_page(self, browser, hwnd, url, title):
        with self._lock:
            self.browsers[browser] = time.time()
            changed = not self.page or self.page["url"] != url
            self.page = {"browser": browser, "hwnd": hwnd, "url": url, "title": title, "at": time.time()}
            if changed and self.results and self.results["url"] != url:
                self.results = None  # (a different page: its results don't apply)
            self.nav = (self.nav + [(time.time(), url)])[-MAX_NAV:]

    def current_page(self):
        return self.page if self._fresh(self.page) else None

    def set_results(self, url, items):
        with self._lock:
            self.results = {"url": url, "items": items, "at": time.time()}

    def results_for(self, url):
        """The remembered results, only if they belong to the page the browser shows NOW."""
        r = self.results
        if not self._fresh(r) or not url or r["url"].rstrip("/") != str(url).rstrip("/"):
            return None
        return r["items"]

    def did_read(self, url, title, excerpt):
        self.read = {"url": url, "title": title, "excerpt": excerpt[:600], "at": time.time()}

    def touched(self, name, kind):
        self.element = {"name": name, "kind": kind, "at": time.time()}

    def lines(self):
        """For the model: what's going on in the browser (short; nothing when stale)."""
        out = []
        p = self.current_page()
        if p:
            from room_agent.computer.browsers import KNOWN, host

            out.append(f"- browser (recent, may have changed since): {KNOWN[p['browser']].name}, tab \"{p['title'][:70]}\" "
                       f"({host(p['url'])}). 'it' / 'this page' / 'go back' mean this tab.")
            items = self.results_for(p["url"])
            if items:
                out.append("- results on that page: " + "; ".join(f"{i['n']}. {i['title'][:50]}" for i in items[:6]))
        r = self.research
        if r is not None and time.time() - r.at < CONTEXT_TTL_S:
            out.append(f"- last research: \"{r.question[:80]}\" ({len(r.sources)} sources; the user can say 'open source "
                       "2' or ask to expand a finding)")
        return out


desk = DesktopContext()
