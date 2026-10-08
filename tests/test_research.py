"""Web research, offline: a local web server serves the pages, the search engine is a stub returning their addresses.
Real fetching and text extraction, no internet, no model calls.

Covers: queries from the question, primary sources first, one per site, social sites skipped, passages that really come
from the page, citations matching the pages, unreadable pages reported (404, not HTML), local addresses refused outside
tests, cancellation (before and during reading), the report saved for the dashboard, the voice tool and opening a cited
source, follow-up context.

Run:  .venv\\Scripts\\python -m tests.test_research
"""

import http.server
import json
import threading
import time

from tests.harness import setup_env

setup_env()

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor  # noqa: E402
from room_agent.computer import browsers, pages, research  # noqa: E402
from room_agent.computer.context import desk  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
core.ensure_loaded()

PAGES = {
    "/docs/whisper": ("Whisper - OpenAI docs", "<main><h1>Whisper</h1><p>Whisper is a general-purpose speech recognition model "
                      "trained on 680,000 hours of multilingual audio.</p><p>The large-v3 model has 1.55 billion parameters "
                      "and runs on a GPU.</p><nav>Menu Home About</nav></main>"),
    "/blog/asr-compare": ("Comparing speech recognition models", "<article><p>In our benchmark, Whisper large-v3 reached a word "
                          "error rate of 7.4 percent on our speech recognition test set.</p><p>Parakeet speech recognition "
                          "was faster than Whisper but less accurate on accents.</p><p>Unrelated cooking tips for "
                          "pasta lovers are below.</p></article>"),
    "/blog/asr-other": ("Another speech recognition review", "<article><p>Whisper large-v3 reached a word error rate of 9.1 "
                        "percent in this speech recognition review, higher than other reports.</p></article>"),
    "/slow": ("Slow", "<p>speech recognition " * 20 + "</p>"),
}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/slow":
            time.sleep(1.5)
        if path == "/file.pdf":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.end_headers()
            self.wfile.write(b"%PDF-1.4")
            return
        if path not in PAGES:
            self.send_response(404)
            self.end_headers()
            return
        title, body = PAGES[path]
        html = f"<html><head><title>{title}</title><script>var secret=1;</script></head><body>{body}</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
PORT = server.server_address[1]


def url(path, hostname="docs.whisper.test"):
    return f"http://{hostname}:{PORT}{path}"


_real_fetch = pages.fetch


def fetch_via(u, **kw):
    """Each simulated site has its own name (so 'one per site' means something); all are served by the local server."""
    host = u.split("//", 1)[1].split(":", 1)[0]
    page = _real_fetch(u.replace(host, "127.0.0.1", 1), **kw)
    page.url = page.final_url = u
    return page


RESULTS = [
    {"title": "Pinterest board", "href": "http://pinterest.com/x", "body": "pins"},
    {"title": "Compare ASR", "href": url("/blog/asr-compare", "blog-a.test"), "body": "benchmark"},
    {"title": "Whisper docs", "href": url("/docs/whisper"), "body": "official"},
    {"title": "Other review", "href": url("/blog/asr-other", "blog-b.test"), "body": "review"},
    {"title": "Missing", "href": url("/missing", "gone.test"), "body": ""},
    {"title": "A PDF", "href": url("/file.pdf", "files.test"), "body": ""},
]
searched = []


def fake_search(q):
    searched.append(q)
    return list(RESULTS)


pages.ALLOW_PRIVATE = True

print("Queries and ranking:")
qs = research.make_queries("Compare Whisper and other speech recognition systems", "deep")
t.check("several queries from one question (the question + comparison variants)", len(qs) >= 3 and qs[0].startswith("Compare")
        and any("comparison" in q or "alternatives" in q for q in qs), qs)
ranked = research.rank(RESULTS + [{"title": "dup", "href": url("/docs/whisper?x=1"), "body": ""}],
                       "speech recognition", 8)
t.check("the official/primary source comes first", ranked[0]["url"] == url("/docs/whisper"), [r["url"] for r in ranked])
t.check("one page per site; social sites skipped", len({r["host"] for r in ranked}) == len(ranked)
        and not any("pinterest" in r["host"] for r in ranked))

print("H. Research:")
report = research.research("Compare Whisper and other speech recognition systems", "deep", search=fake_search, fetch=fetch_via)
srcs = {s.url: s for s in report.sources}
t.check("pages read and numbered: docs first, then the readable others", report.status == "done"
        and report.sources[0].url == url("/docs/whisper") and len(report.sources) == 3, [(s.n, s.url) for s in report.sources])
real_text = {u: fetch_via(u).text for u in srcs}
t.check("citation accuracy: every quoted passage is really on its own page", all(
    p in real_text[s.url] for s in report.sources for p in s.passages))
t.check("no invented sources: every source was a search result", all(any(r["href"] == s.url for r in RESULTS)
                                                                     or s.url == url("/docs/whisper") for s in report.sources))
t.check("passages are the relevant ones (not the cooking tips, menus or scripts)", not any(
    "cooking" in p or "Menu" in p or "secret" in p for s in report.sources for p in s.passages))
failed = dict(report.failed)
t.check("a 404 and a PDF are reported as not readable, not guessed", any("404" in w for w in failed.values())
        and any("not a web page" in w for w in failed.values()), failed)
text = research.for_model(report)
t.check("the model gets numbered sources with their passages and the rules (compare, disagreements, cite [n])",
        "[1]" in text and "[3]" in text and "disagree" in text and "official/primary" in text)
t.check("conflicting numbers from two sources are both there for the comparison (7.4 vs 9.1 percent)",
        "7.4 percent" in text and "9.1 percent" in text)
t.check("no URLs in what the model will speak from (hosts only)", "http://" not in text)
saved = json.loads(config.RESEARCH_FILE.read_text(encoding="utf-8"))
t.check("the report is saved for the dashboard, with the real links", saved[0]["question"].startswith("Compare")
        and [s["url"] for s in saved[0]["sources"]] == [s.url for s in report.sources])

print("Cancelling and failures:")
t0 = time.time()
r = research.research("speech recognition", "quick", search=fake_search, fetch=fetch_via, cancel=lambda: True)
t.check("I. cancelled before it starts -> 'cancelled', nothing searched further", r.status == "cancelled" and not r.sources)
slow = [{"title": "slow", "href": url("/slow"), "body": ""}]
stop_at = time.time() + 0.3
t0 = time.time()
r = research.research("speech recognition", "quick", search=lambda q: slow, fetch=fetch_via,
                      cancel=lambda: time.time() > stop_at)
t.check("interrupted while reading -> stops and says so", r.status == "cancelled", r.status)
t.check("research.for_model on a cancelled report -> 'stopped', no summary", research.for_model(r).startswith("FAILED: stopped"))
r = research.research("speech recognition", "quick", search=lambda q: [])
t.check("nothing found -> says so (no answer from memory dressed up as research)", r.status == "nothing_found"
        and "honestly" in research.for_model(r))
r = research.research("speech recognition", "quick", search=lambda q: (_ for _ in ()).throw(OSError("offline")))
t.check("the search itself failing -> nothing_found, no crash", r.status == "nothing_found")
pages.ALLOW_PRIVATE = False
p = pages.fetch(f"http://127.0.0.1:{PORT}/docs/whisper")
t.check("outside tests, local/private addresses are never fetched", not p.ok and "this PC" in p.error, p.error)
pages.ALLOW_PRIVATE = True

print("Through the voice tool:")
research._search = fake_search
research.pages.fetch = fetch_via
rt.new_turn("research the best speech recognition models")
res = executor.execute("research_web", {"question": "best speech recognition models", "depth": "deep"})
t.check("research_web -> OK with sources; the desktop context remembers it for follow-ups", res.success and "[1]" in res.message
        and desk.research is not None and len(desk.research.sources) >= 2, res.message[:200])
t.check("...and the model is told about it in later turns ('open source 2')", any("last research" in line for line in desk.lines()))
opened = []
browsers.open_url = lambda u, browser="", said="", query="": (opened.append(u), f"OK: opened {browsers.host(u)}.")[1]
rt.new_turn("open source 2")
res = executor.execute("open_research_source", {"number": 2})
t.check("'open source 2' opens exactly that cited page", res.success and opened == [desk.research.sources[1].url], opened)
rt.new_turn("research speech recognition")
rt.turn.cancel.set()
res = executor.execute("research_web", {"question": "speech recognition"})
t.check("I. interrupted research through the tool -> 'stopped', nothing claimed", not res.success and "stopped" in res.message,
        res.message)
server.shutdown()
t.done("RESEARCH")
