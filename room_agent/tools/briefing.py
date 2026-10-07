"""Daily briefing: everything for a "good morning" catch-up gathered in one tool call."""

import datetime
from concurrent.futures import ThreadPoolExecutor

from room_agent import runtime as rt
from room_agent.tools.timers import list_timers
from room_agent.tools.weather import get_weather
from room_agent.tools.web import search

HEADLINES = 3


def _weather():
    out = get_weather()
    if out.startswith("OK"):
        return out[3:].strip()
    return "not available: no home city is stored, so ask where they are if they want it"


def _headlines():
    today, fresh, seen = datetime.date.today(), [], set()
    for query in ("breaking news", "top stories today", "world news"):
        for r in search(query, news=True):
            try:
                age = (today - datetime.date.fromisoformat((r.get("date") or "")[:10])).days
            except ValueError:
                continue
            title = (r.get("title") or "").strip()
            if -1 <= age <= 2 and title and title not in seen:  # only the last couple of days
                seen.add(title)
                fresh.append(f"{title} ({r.get('source') or 'unknown source'})")
        if len(fresh) >= HEADLINES:
            break
    return " | ".join(fresh[:HEADLINES]) or "no fresh headlines came back, so skip the news"


def _coming_up():
    if not rt.memory.available:
        return ""
    plans = [f["content"] for f in rt.memory.facts() if f.get("category") in ("plan", "routine")]
    return "; ".join(plans[:5])


def daily_briefing():
    with ThreadPoolExecutor(max_workers=2) as pool:
        weather, news = pool.submit(_weather), pool.submit(_headlines)
        weather_text, news_text = _safely(weather), _safely(news)
    lines = [
        f"- time: {datetime.datetime.now().strftime('%A %B %d %Y, %I:%M %p')}",
        f"- weather: {weather_text}",
        f"- top headlines: {news_text}",
        f"- timers: {list_timers()[4:]}",
    ]
    coming_up = _coming_up()
    if coming_up:
        lines.append(f"- stored plans and routines (may be out of date): {coming_up}")
    lines.append("- calendar: not available, so don't mention events beyond the stored plans above")
    return "OK: briefing data:\n" + "\n".join(lines)


def _safely(future):
    try:
        return future.result(timeout=20)
    except Exception as e:
        return f"couldn't fetch ({e.__class__.__name__})"
