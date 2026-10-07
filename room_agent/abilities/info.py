"""Information: the time, the weather, web search and news, the morning briefing."""

import datetime
import re

from room_agent import runtime as rt
from room_agent.abilities._kit import NO_ARGS, params, tool
from room_agent.actions.core import Group, register_claim, register_group, register_line

register_group(Group("time", title="time and date", summary="local clock"))
register_group(Group("weather", title="weather", summary="now, today and tomorrow for a named city, or the stored home city"))
register_group(Group("web", title="web search and news", summary="DuckDuckGo snippets: news, scores, prices, facts", rules=[
    "- For news, scores, prices, recent events, or any fact you're not sure of, use web_search (news=true for headlines). "
    "Then say what you found in one or two casual sentences, mention the source in passing, no URLs, and don't read out a "
    "list: pick the one or two best bits."]))
register_group(Group("briefing", re.compile(r"morning|brief|catch me up|rundown|my day|today", re.I), title="daily briefing",
                     summary="weather, headlines, timers, plans (no calendar)", rules=[
    "- For \"good morning\", \"brief me\" or \"catch me up\", call daily_briefing and give a short friendly rundown in a few "
    "sentences, not a list: greet them, weather first, one or two headlines, then anything coming up or running. Skip any "
    "part that wasn't available."]))
register_line("opening or browsing web pages", "not built; search snippets only", available=lambda: False)
register_claim("web", r"\b(i\s+)?(searched|googled|looked (it|that) up online|checked online|found online|browsed|looked on the web)\b"
                      r"|\bi'?ll (search|google|look (it|that) up online|check online|browse)\b")


def _weather(args):
    from room_agent.tools.weather import get_weather

    return get_weather(args.get("location", ""))


def _search(args):
    from room_agent.tools.web import web_search

    return web_search(args["query"], bool(args.get("news")))


def _briefing(args):
    from room_agent.tools.briefing import daily_briefing

    return daily_briefing()


tool("get_time", "Get the current local date and time.", NO_ARGS,
     lambda args: datetime.datetime.now().strftime("%A %B %d %Y, %I:%M %p"), group="time", changes_state=False)
tool("get_weather", "Current weather plus today's and tomorrow's forecast. Leave location empty for where they are right "
     "now (or their home city if that can't be found).",
     params({"location": {"type": "string", "description": "City name, e.g. 'Chicago'"}}), _weather, group="weather",
     changes_state=False)
tool("daily_briefing", "Everything for a morning-style catch-up in one call: date and time, weather, top news headlines, "
     "running timers and stored plans. Use for 'good morning', 'brief me', 'catch me up', 'what's going on today'.",
     NO_ARGS, _briefing, group="briefing", changes_state=False, claim="web")
tool("web_search", "Search the web, or news headlines, for current or factual things you don't reliably know: news, "
     "sports scores, prices, recent events, facts. Returns the top few results with snippets.",
     params({"query": {"type": "string", "description": "Short search terms, not a full sentence, e.g. 'lakers score' or "
                                                        "'mount everest height'"},
             "news": {"type": "boolean", "description": "True for headlines, what's happening, or recent events"}},
            ["query"]), _search, group="web", changes_state=False, claim="web")
