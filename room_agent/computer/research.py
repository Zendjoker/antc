"""Grounded web research: search, read real pages, hand the model only what the pages say, with numbered sources.

    question -> a few search queries -> results (title + URL) -> rank (official / primary sources first, one per site)
    -> read the pages (in parallel, cancellable) -> the passages that match the question -> numbered sources

The model writes the comparison from those passages only, citing [n]; the source list (titles + URLs) comes from code,
so links are never invented. Pages that couldn't be read are listed as limitations, not guessed at. The last reports are
kept in research.json for the dashboard (not in memory).
"""

import concurrent.futures as cf
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field

from room_agent import config
from room_agent.computer import browsers, pages

log = logging.getLogger("room-agent")
DEPTH = {"quick": {"queries": 2, "pages": 4, "per_page": 2}, "deep": {"queries": 4, "pages": 8, "per_page": 3}}
KEEP_REPORTS = 5
PRIMARY_HOSTS = re.compile(r"(^|\.)(docs?|developers?|developer|learn|support|help)\.|readthedocs\.io$|github\.com$|"
                           r"gitlab\.com$|huggingface\.co$|arxiv\.org$|pypi\.org$|python\.org$|mozilla\.org$|"
                           r"microsoft\.com$|openai\.com$|anthropic\.com$|google\.com$|nvidia\.com$|wikipedia\.org$", re.I)
LOW_VALUE = re.compile(r"(pinterest|facebook|instagram|tiktok|quora)\.com$", re.I)
STOP = {"the", "a", "an", "of", "for", "to", "and", "or", "in", "on", "with", "is", "are", "what", "which", "how", "best",
        "find", "me", "about", "research", "compare", "comparison", "vs", "versus", "other", "official", "some", "good",
        "tell", "look", "up", "search", "can", "you", "please", "between", "do", "does", "should", "i", "my"}


@dataclass
class Source:
    n: int
    title: str
    url: str
    host: str
    primary: bool = False
    snippet: str = ""
    passages: list = field(default_factory=list)


@dataclass
class Report:
    question: str
    depth: str = "quick"
    queries: list = field(default_factory=list)
    sources: list = field(default_factory=list)       # Source, numbered in the order given to the model
    failed: list = field(default_factory=list)        # [(url, why)]
    status: str = "done"                              # done | cancelled | nothing_found
    seconds: float = 0.0
    at: float = field(default_factory=time.time)


def terms(text):
    return [w for w in re.findall(r"[\w.+#-]{2,}", str(text).lower()) if w not in STOP]


def make_queries(question, depth="quick"):
    q = " ".join(str(question).split()).strip().rstrip("?")
    core = " ".join(terms(q)) or q
    out = [q]
    low = q.lower()
    if re.search(r"\b(documentation|docs|library|api|sdk|package|install|error|exception)\b", low):
        out.append(f"{core} official documentation")
    if re.search(r"\b(compare|comparison|vs\.?|versus|best|alternatives?|other)\b", low):
        out += [f"{core} comparison benchmark", f"{core} alternatives"]
    if re.search(r"\berror|exception|traceback|failed\b", low):
        out.append(f"{core} fix")
    out += [core, f"{core} {time.strftime('%Y')}"]
    seen, unique = set(), []
    for x in out:
        if x.lower() not in seen:
            seen.add(x.lower())
            unique.append(x)
    return unique[:DEPTH.get(depth, DEPTH["quick"])["queries"]]


def is_primary(host, question):
    if PRIMARY_HOSTS.search(host):
        return True
    names = set(terms(question))
    return any(part in names for part in host.split(".")[:-1] if len(part) > 3)  # (zigbee2mqtt.io for "zigbee2mqtt")


def rank(results, question, limit):
    """Search results -> the pages to read: primary first, one per site, no social/low-value sites."""
    seen_urls, seen_hosts, scored = set(), set(), []
    for i, r in enumerate(results):
        url = r.get("href") or r.get("url") or ""
        if not url.startswith("http") or url in seen_urls:
            continue
        h = browsers.host(url)
        if LOW_VALUE.search(h):
            continue
        seen_urls.add(url)
        primary = is_primary(h, question)
        scored.append(((0 if primary else 1), i, h, url, r, primary))
    out = []
    for _, _, h, url, r, primary in sorted(scored, key=lambda s: (s[0], s[1])):
        if h in seen_hosts:
            continue
        seen_hosts.add(h)
        out.append({"url": url, "title": (r.get("title") or h).strip(), "host": h, "snippet": (r.get("body") or "")[:300],
                    "primary": primary})
        if len(out) >= limit:
            break
    return out


def passages(paragraphs, question, k=2, max_chars=700):
    """The k paragraphs that answer the question best (term overlap), in page order."""
    want = set(terms(question))
    if not want:
        return paragraphs[:k]
    scored = []
    for i, p in enumerate(paragraphs):
        words = set(terms(p))
        hit = len(want & words)
        if hit:
            scored.append((hit + min(len(p), 600) / 2000, i, p))
    best = sorted(scored, key=lambda s: -s[0])[:k]
    return [p[:max_chars] for _, _, p in sorted(best, key=lambda s: s[1])]


def _search(query):
    from room_agent.tools import web

    return web.search(query, news=False)


def research(question, depth="quick", progress=None, cancel=None, search=None, fetch=None):
    """-> Report. `cancel()` -> True stops between steps; `progress(text)` gets short spoken-style updates."""
    t0 = time.time()
    depth = depth if depth in DEPTH else "quick"
    plan = DEPTH[depth]
    search, fetch = search or _search, fetch or pages.fetch
    cancel = cancel or (lambda: False)
    report = Report(question=str(question).strip(), depth=depth, queries=make_queries(question, depth))
    results = []
    for q in report.queries:
        if cancel():
            report.status = "cancelled"
            return _done(report, t0)
        try:
            results += list(search(q) or [])
        except Exception as e:
            log.info("research: search %r failed: %s", q, e)
    picked = rank(results, question, plan["pages"])
    if not picked:
        report.status = "nothing_found"
        return _done(report, t0)
    if progress and depth == "deep":
        progress(f"Reading {len(picked)} sources.")
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, p["url"], cancel=cancel): p for p in picked}
        read = {}
        for fut in cf.as_completed(futures):
            p = futures[fut]
            try:
                read[p["url"]] = fut.result()
            except Exception as e:
                read[p["url"]] = pages.Page(url=p["url"], error=f"couldn't read it ({e.__class__.__name__})")
            if cancel():
                for f in futures:
                    f.cancel()
                report.status = "cancelled"
                break
    if report.status == "cancelled":
        return _done(report, t0)
    for p in picked:  # (numbered in rank order, so [1] is the most authoritative source)
        page = read.get(p["url"])
        if page is None or not page.ok:
            report.failed.append((p["url"], page.error if page else "not read"))
            continue
        found = passages(page.paragraphs, question, plan["per_page"])
        if not found:
            report.failed.append((p["url"], "nothing on it about the question"))
            continue
        report.sources.append(Source(n=len(report.sources) + 1, title=page.title or p["title"], url=page.final_url or p["url"],
                                     host=p["host"], primary=p["primary"], snippet=p["snippet"], passages=found))
    if not report.sources:
        report.status = "nothing_found"
    return _done(report, t0)


def _done(report, t0):
    report.seconds = round(time.time() - t0, 1)
    report.at = time.time()
    save(report)
    return report


def for_model(report):
    """The tool result: only what the pages say, numbered. The model cites [n] and never adds links of its own."""
    if report.status == "cancelled":
        return "FAILED: stopped: they interrupted the research, so there are no findings. Acknowledge briefly; don't summarize."
    if report.status == "nothing_found":
        why = "; ".join(f"{browsers.host(u)}: {w}" for u, w in report.failed[:4])
        return ("OK: the research found nothing usable for this question" + (f" (pages tried: {why})" if why else
                " (the searches came back empty)") + ". Say so honestly; don't answer from memory as if it were research.")
    lines = [f"OK: research on \"{report.question}\" ({len(report.sources)} sources read, {report.seconds:.0f}s). Answer ONLY "
             "from these passages. Compare the sources, point out where they disagree, and say which claims come from "
             "official/primary sources. Mark anything not in the passages as your own assumption. Cite as [n]. Speak a "
             "short summary (2-4 sentences, no URLs, no list); the full findings and links are on the dashboard and the "
             "user can say 'open source 2'."]
    for s in report.sources:
        lines.append(f"[{s.n}] {s.title[:90]} ({s.host}{', official/primary' if s.primary else ''})")
        lines += [f"    \"{p}\"" for p in s.passages]
    if report.failed:
        lines.append("Couldn't read: " + "; ".join(f"{browsers.host(u)} ({w})" for u, w in report.failed[:5]))
    return "\n".join(lines)


def save(report):
    """Keep the last few reports for the dashboard (research.json). Not memory: overwritten, never learned from."""
    path = config.RESEARCH_FILE
    try:
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError):
        old = []
    entry = asdict(report)
    entry["sources"] = [asdict(s) if not isinstance(s, dict) else s for s in report.sources]
    try:
        path.write_text(json.dumps(([entry] + [r for r in old if isinstance(r, dict)])[:KEEP_REPORTS], indent=1),
                        encoding="utf-8")
    except OSError as e:
        log.info("research: couldn't save the report for the dashboard (%s)", e)


def latest():
    try:
        data = json.loads(config.RESEARCH_FILE.read_text(encoding="utf-8"))
        return data[0] if data else None
    except (OSError, ValueError):
        return None
