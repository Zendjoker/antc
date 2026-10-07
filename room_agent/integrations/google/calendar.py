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


def _day(word, today):
    w = word.lower().strip()
    if w == "today":
        return today
    if w == "tomorrow":
        return today + datetime.timedelta(days=1)
    if w == "yesterday":
        return today - datetime.timedelta(days=1)
    for i, name in enumerate(WEEKDAYS):
        if w.endswith(name) or w == name[:3]:
            ahead = (i - today.weekday()) % 7
            if w.startswith("next "):
                ahead = ahead or 7
            return today + datetime.timedelta(days=ahead)
    return None


def _clock(text):
    """'7pm' / '19:00' / '7:30 am' / '3' (afternoon, as people mean it) -> (hour, minute) or None."""
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
    elif not ap and 1 <= h <= 6:
        h += 12  # "at 3" means the afternoon
    return h, mi


def parse_time(text, tz, base=None):
    """ISO datetime / ISO date / 'tomorrow 3pm' / 'friday at 7' / '3' (on `base`'s day) -> aware datetime, or a date
    (all day) when no time is given. Raises ValueError."""
    text = str(text or "").strip()
    now = datetime.datetime.now(tz)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return datetime.date.fromisoformat(text)
        d = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return d.replace(tzinfo=tz) if d.tzinfo is None else d.astimezone(tz)
    except ValueError:
        pass
    low = text.lower()
    day = None
    for word in ["today", "tomorrow", "yesterday"] + [f"next {d}" for d in WEEKDAYS] + WEEKDAYS:
        if re.search(rf"\b{word}\b", low):
            day = _day(word, now.date())
            low = re.sub(rf"\b{word}\b", " ", low)
            break
    clock = _clock(low)
    if day is None and clock is None:
        raise ValueError(f"can't read the time '{text}'")
    if day is None:
        day = (base or now).date() if isinstance(base or now, datetime.datetime) else base
    if clock is None:
        return day
    return datetime.datetime.combine(day, datetime.time(*clock), tz)


def period(when, tz):
    """'today' / 'tomorrow' / 'this week' / 'next week' / 'weekend' / 'friday afternoon' / ISO date -> (start, end)."""
    now = datetime.datetime.now(tz)
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
