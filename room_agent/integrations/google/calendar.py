"""Google Calendar through the Google connection. Reading needs calendar.readonly; adding/changing/deleting events needs
calendar.events. Times are in the calendar's own time zone (read from the calendar), not the PC's. Every change is read
back from Google before it's reported; no invitations are emailed (sendUpdates=none)."""

import datetime
import re
from zoneinfo import ZoneInfo

from room_agent.integrations.base import IntegrationError, NotFound

BASE = "https://www.googleapis.com/calendar/v3"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
PARTS = {"morning": (6, 12), "afternoon": (12, 18), "evening": (17, 23), "tonight": (17, 24), "night": (19, 24)}


def _tz(name):
    try:
        return ZoneInfo(name)
    except Exception:
        return datetime.datetime.now().astimezone().tzinfo


class AmbiguousTime(ValueError):
    """Words that name two different days or times ("this Friday" said on a Friday): asked about, never guessed.
    `choices`: the dates (or datetimes) it could mean."""

    def __init__(self, message, choices=()):
        super().__init__(message)
        self.choices = list(choices)


# Relative days, one meaning everywhere (creating, moving and reading events):
#   today / tonight / this morning|afternoon|evening -> today       tomorrow -> today + 1
#   "Friday", "this Friday"  -> the next Friday after today ("this Friday" said ON a Friday: today or next week? asked)
#   "next Friday"            -> the Friday after this week's, asked when this week's is still ahead (Wednesday: the
#                               9th or the 16th?); unambiguous once this week's has passed or is today
def _day(word, today):
    w = " ".join(word.lower().split())
    if w in ("today", "tonight"):
        return today
    if w == "tomorrow":
        return today + datetime.timedelta(days=1)
    if w == "yesterday":
        return today - datetime.timedelta(days=1)
    for i, name in enumerate(WEEKDAYS):
        if w.endswith(name) or w == name[:3]:
            ahead = (i - today.weekday()) % 7  # 0: it's today
            coming = today + datetime.timedelta(days=ahead or 7)
            if w.startswith("this ") and ahead == 0:
                _ask(word, [today, coming])
            if w.startswith("next ") and i > today.weekday():  # (this week's still ahead: that one, or the week after?)
                _ask(word, [coming, coming + datetime.timedelta(days=7)])
            return coming
    return None


def _ask(word, days):
    said = " or ".join(f"{d.strftime('%a %b')} {d.day}" for d in days)
    raise AmbiguousTime(f"'{word}' could mean {said}", days)


# Part of the day: which day it is ("tonight" = today) and which half of the clock a bare hour is on ("tonight at 8")
_PART_WORDS = re.compile(r"\b(?:this\s+)?(morning|afternoon|evening|tonight|night)\b")
_PM_PARTS = {"afternoon", "evening", "tonight", "night"}


def _clock(text, part=None):
    """'7pm' / '19:00' / '7:30 am' / '3' (afternoon, as people mean it) -> (hour, minute) or None. `part`: the part of
    the day they named ('tonight at 8' is 20:00, 'in the morning at 6' is 06:00)."""
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b", text.lower())
    if not m:
        return None
    h, mi, ap = int(m[1]), int(m[2] or 0), (m[3] or "").replace(".", "")
    if h > 23 or mi > 59:
        return None
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    elif not ap and part in _PM_PARTS and 1 <= h <= 11:
        h += 12  # "tonight at 8", "Friday evening at 7"
    elif not ap and part is None and 1 <= h <= 6:
        h += 12  # "at 3" means the afternoon
    return h, mi


def parse_time(text, tz, base=None, now=None):
    """ISO datetime / ISO date / 'tomorrow 3pm' / 'friday at 7' / 'tonight at 8' / '3' (on `base`'s day) -> aware
    datetime, or a date (all day) when no time is given. Raises ValueError (AmbiguousTime when it could mean two
    days). Relative words are read in the calendar's own time zone (`tz`)."""
    text = str(text or "").strip()
    now = now or datetime.datetime.now(tz)  # (`now`: fixed by tests; always the calendar's local time)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return datetime.date.fromisoformat(text)
        d = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return d.replace(tzinfo=tz) if d.tzinfo is None else d.astimezone(tz)
    except ValueError:
        pass
    low = " ".join(text.lower().split())
    part_m = _PART_WORDS.search(low)
    part = part_m[1] if part_m else None
    if part_m:
        low = low[:part_m.start()] + " " + low[part_m.end():]
    day = None
    for word in ["today", "tomorrow", "yesterday"] + [f"{w} {d}" for w in ("next", "this") for d in WEEKDAYS] + WEEKDAYS:
        if re.search(rf"\b{word}\b", low):
            day = _day(word, now.date())
            low = re.sub(rf"\b{word}\b", " ", low)
            break
    if day is None and part_m and (part == "tonight" or part_m[0].startswith("this ")):
        day = now.date()  # "tonight", "this evening"
    clock = _clock(low, part)
    if day is None and clock is None:
        raise ValueError(f"can't read the time '{text}'")
    if clock is None and part:  # ("tonight" alone: a time is needed, not an all-day event)
        raise AmbiguousTime(f"'{text}' has no time: ask what time")
    if day is None:
        day = (base or now).date() if isinstance(base or now, datetime.datetime) else base
    if clock is None:
        return day
    return datetime.datetime.combine(day, datetime.time(*clock), tz)


def period(when, tz, now=None):
    """'today' / 'tomorrow' / 'this week' / 'next week' / 'weekend' / 'friday afternoon' / ISO date -> (start, end)."""
    now = now or datetime.datetime.now(tz)
    w = str(when or "today").lower().strip()
    part = next((p for p in PARTS if p in w), None)
    w = w.replace(part, "").strip() if part else w
    start_of = lambda d: datetime.datetime.combine(d, datetime.time(), tz)
    if w in ("this week", "week"):
        s = start_of(now.date() - datetime.timedelta(days=now.weekday()))
        return max(s, now.replace(minute=0, second=0, microsecond=0)), s + datetime.timedelta(days=7)
    if w == "next week":
        s = start_of(now.date() + datetime.timedelta(days=7 - now.weekday()))
        return s, s + datetime.timedelta(days=7)
    if w in ("weekend", "this weekend"):
        s = start_of(now.date() + datetime.timedelta(days=(5 - now.weekday()) % 7))
        return s, s + datetime.timedelta(days=2)
    if w in ("upcoming", "next", "soon"):
        return now, now + datetime.timedelta(days=14)
    day = _day(w, now.date()) if w else now.date()
    if day is None:
        day = datetime.date.fromisoformat(w[:10])
    s = start_of(day)
    if part:
        a, b = PARTS[part]
        return s + datetime.timedelta(hours=a), s + datetime.timedelta(hours=b)
    return s, s + datetime.timedelta(days=1)


def _fmt(dt, all_day=False):
    if all_day:
        return dt.strftime("%a %b %d") + " (all day)"
    return dt.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


class CalendarService:
    _tz_cache = {}

    def __init__(self, provider, account=None):
        self.session = provider.session(account)
        self.account = self.session.account

    def tz(self):
        if self.account not in self._tz_cache:
            try:
                name = self.session.call("GET", f"{BASE}/calendars/primary").get("timeZone", "")
            except IntegrationError:
                name = ""
            self._tz_cache[self.account] = _tz(name) if name else datetime.datetime.now().astimezone().tzinfo
        return self._tz_cache[self.account]

    def tz_name(self):
        return getattr(self.tz(), "key", str(self.tz()))

    # ----- reading
    def list_calendars(self):
        items = self.session.call("GET", f"{BASE}/users/me/calendarList").get("items", [])
        return [{"id": c["id"], "name": c.get("summaryOverride") or c.get("summary", ""), "primary": bool(c.get("primary")),
                 "time_zone": c.get("timeZone", ""), "access": c.get("accessRole", "")} for c in items]

    def events(self, start, end, query=None, calendar_id="primary", limit=20):
        params = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true", "orderBy": "startTime",
                  "maxResults": max(1, min(int(limit or 20), 50)), "timeZone": self.tz_name()}
        if query:
            params["q"] = query
        items = self.session.call("GET", f"{BASE}/calendars/{calendar_id}/events", params=params).get("items", [])
        return [self.normalize(e, calendar_id) for e in items if e.get("status") != "cancelled"]

    def get_event(self, eid, calendar_id="primary"):
        e = self.session.call("GET", f"{BASE}/calendars/{calendar_id}/events/{eid}")
        if e.get("status") == "cancelled":
            raise NotFound("Google Calendar", "cancelled")
        return self.normalize(e, calendar_id)

    def normalize(self, e, calendar_id="primary"):
        tz = self.tz()
        s, en = e.get("start", {}), e.get("end", {})
        all_day = "date" in s and "dateTime" not in s
        if all_day:
            start = datetime.datetime.combine(datetime.date.fromisoformat(s["date"]), datetime.time(), tz)
            end = datetime.datetime.combine(datetime.date.fromisoformat(en.get("date", s["date"])), datetime.time(), tz)
        else:
            start = datetime.datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00")).astimezone(tz)
            end = datetime.datetime.fromisoformat(en.get("dateTime", s["dateTime"]).replace("Z", "+00:00")).astimezone(tz)
        return {"id": e["id"], "title": e.get("summary", "(no title)"), "start": start, "end": end, "all_day": all_day,
                "when": _fmt(start, all_day) + ("" if all_day else " to " + end.strftime("%I:%M %p").lstrip("0")),
                "location": e.get("location", ""), "description": e.get("description", ""),
                "organizer": (e.get("organizer") or {}).get("email", ""),
                "attendees": [a.get("displayName") or a.get("email", "") for a in e.get("attendees", [])][:10],
                "calendar_id": calendar_id, "status": e.get("status", "confirmed"), "tz": self.tz_name()}

    # ----- changes (calendar.events): each read back before it counts
    @staticmethod
    def _when_body(start, end):
        if isinstance(start, datetime.datetime):
            return {"dateTime": start.isoformat()}, {"dateTime": end.isoformat()}
        return {"date": start.isoformat()}, {"date": end.isoformat()}

    def create_event(self, title, start, end, location="", description="", calendar_id="primary"):
        s, e = self._when_body(start, end)
        body = {"summary": title, "start": s, "end": e}
        if location:
            body["location"] = location
        if description:
            body["description"] = description
        made = self.session.call("POST", f"{BASE}/calendars/{calendar_id}/events", params={"sendUpdates": "none"},
                                 json_body=body, scope_hint="calendar.events")
        ev = self.get_event(made["id"], calendar_id)
        if ev["title"] != title or ev["start"] != (start if isinstance(start, datetime.datetime)
                                                   else datetime.datetime.combine(start, datetime.time(), self.tz())):
            raise IntegrationError("Google Calendar", "event didn't save as asked")
        return ev

    def update_event(self, eid, calendar_id="primary", **changes):
        """changes: title, start, end (datetimes), location, description. A new start without an end keeps the length."""
        cur = self.get_event(eid, calendar_id)
        body = {}
        if changes.get("title"):
            body["summary"] = changes["title"]
        for k in ("location", "description"):
            if changes.get(k) is not None:
                body[k] = changes[k]
        if changes.get("start") or changes.get("end"):
            start = changes.get("start") or cur["start"]
            end = changes.get("end") or (start + (cur["end"] - cur["start"]) if changes.get("start") else cur["end"])
            body["start"], body["end"] = self._when_body(start, end)
        if not body:
            return cur
        self.session.call("PATCH", f"{BASE}/calendars/{calendar_id}/events/{eid}", params={"sendUpdates": "none"},
                          json_body=body, scope_hint="calendar.events")
        ev = self.get_event(eid, calendar_id)
        bad = [k for k, v in (("title", body.get("summary")), ("location", body.get("location")),
                              ("description", body.get("description"))) if v is not None and ev[k] != v]
        if body.get("start") and "dateTime" in body["start"]:
            if ev["start"] != datetime.datetime.fromisoformat(body["start"]["dateTime"]):
                bad.append("start")
        if bad:
            raise IntegrationError("Google Calendar", "change didn't save: " + ", ".join(bad))
        return ev

    def delete_event(self, eid, calendar_id="primary"):
        self.session.call("DELETE", f"{BASE}/calendars/{calendar_id}/events/{eid}", params={"sendUpdates": "none"},
                          scope_hint="calendar.events")
        try:
            self.get_event(eid, calendar_id)
        except NotFound:
            return True
        raise IntegrationError("Google Calendar", "event still there")
