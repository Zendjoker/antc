"""Web search and news headlines through DuckDuckGo (no API key)."""

import re

MAX_RESULTS = 5
_FILLER_WORDS = {"what", "whats", "what's", "who", "whos", "who's", "when", "where", "why", "how", "is", "are", "was",
                 "were", "does", "do", "did", "the", "a", "an", "tell", "me", "about", "please", "can", "you", "could"}
_GENERIC_NEWS = {"news", "headlines", "headline", "top", "latest", "breaking", "today", "todays", "today's", "stories",
                 "now", "happening", "current", "morning", "tonight", "world", "right", "new"}


def _simplify(query):
    """Question-shaped queries sometimes return nothing; the bare keywords usually work."""
    words = [w for w in re.findall(r"[\w'-]+", query.lower()) if w not in _FILLER_WORDS]
    return " ".join(words) or query


def search(query, news):
    """Raw results (dicts with title, body, and for news also source and date), or [] when nothing came back."""
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    client = DDGS(timeout=8)
    if news:
        calls = [lambda q, t=t: client.news(q, region="us-en", max_results=MAX_RESULTS, timelimit=t) for t in ("d", "w", None)]
    else:
        calls = [lambda q: client.text(q, region="us-en", max_results=MAX_RESULTS)]
    for q in dict.fromkeys([query, _simplify(query)]):
        for call in calls:
            try:
                results = call(q)
            except DDGSException:
                continue
            if results:
                return results
    return []


def _line(r):
    title = (r.get("title") or "").strip()
    body = (r.get("body") or "").strip()[:220]
    tag = ", ".join(x for x in ((r.get("source") or "").strip(), (r.get("date") or "")[:10]) if x)
    return f"- {title}{f' ({tag})' if tag else ''}: {body}"


def web_search(query, news=False):
    query = (query or "").strip()
    if not query:
        return "FAILED: no search terms given."
    if news and set(_simplify(query).split()) <= _GENERIC_NEWS:
        query = "breaking news"  # "headlines" or "news today" alone match random lifestyle articles
    results = search(query, news)
    if not results:
        return f"OK: the search for '{query}' came back empty. Tell them you couldn't find anything on that."
    kind = "news" if news else "web"
    return f"OK: top {kind} results for '{query}':\n" + "\n".join(_line(r) for r in results)
