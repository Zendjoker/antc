"""Things that happen at a moment, not a clock time: reminders tied to events, and a few useful nudges.

Event reminders ("remind me when I get home to call mom"), kept in event_reminders.json until delivered:
    home     the door opens after the room was quiet a while (an arrival; conversation/greet.py's rule)
    leave    the door opens while someone was just here
    bed      the bed sensor moves in the evening or night (ZIGBEE_ALIASES 'bed=...', else a vibration sensor)
    desk     keyboard / mouse used again after DESK_AWAY_MIN or more away from the PC
    morning  the first sign of you after the night (voice, the PC, or the door, between 5 AM and noon)

Nudges (each through the proactive policy: never during quiet hours, a call, or a conversation; each at most once):
    break    BREAK_REMINDER_MIN of nonstop PC use (no 10-minute pause): "maybe stretch"
    rain     when you leave and today's forecast says rain is likely
    meeting  MEETING_ALERT_MIN before a calendar event (when Google Calendar is connected)
    plans    the morning a remembered plan is due ("Dentist on Oct 9, 2026"): mentioned once

Everything spoken goes through say(): the proactive policy decides now / after the conversation / not now; a reminder
that can't be said yet stays pending and is tried again (it's never dropped), a nudge that can't is simply skipped.
"""

import datetime
import json
import logging
import os
import re
import threading
import time
import uuid

from room_agent import config
from room_agent import runtime as rt

log = logging.getLogger("room-agent")
EVENTS = {
    "home": "when you get home",
    "leave": "when you leave",
    "bed": "when you go to bed",
    "desk": "when you're back at your PC",
    "morning": "tomorrow morning when you're up",
}
EVENT_WORDS = [("home", r"get(ting)? (back )?home|come (back )?home|arrive|i'?m home|back home|walk in|get back"),
               ("leave", r"leave|leaving|head(ing)? out|go out|going out|step out"),
               ("bed", r"go(ing)? to (bed|sleep)|bed ?time|in bed|lie down|before i sleep"),
               ("desk", r"(back )?(at|to) (my |the )?(pc|computer|desk)|sit down|start working"),
               ("morning", r"(tomorrow )?morning|when i (wake|get) up|wake up")]
_lock = threading.Lock()
_state = {"active_since": None, "last_input": time.time(), "away": False, "break_done_for": None,
          "morning_done": "", "rain_done": "", "plans_done": set(), "meetings_done": set()}


def event_for(text):
    t = str(text or "").lower().strip()
    if t in EVENTS:
        return t
    return next((key for key, pat in EVENT_WORDS if re.search(pat, t)), None)


# ---------------------------------------------------------------- the reminders
def _load():
    try:
        data = json.loads(config.EVENT_REMINDERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items):
    tmp = config.EVENT_REMINDERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1), encoding="utf-8")
    tmp.replace(config.EVENT_REMINDERS_FILE)


def add(text, event):
    text = " ".join(str(text or "").split()).strip(" .")
    ev = event_for(event)
    if not text:
        return "NEEDS: what to remind them about."
    if ev is None:
        return ("NEEDS: which moment: when they get home, leave, go to bed, are back at the PC, or in the morning? (For a "
                "clock time or a delay, use set_alarm / set_timer.)")
    with _lock:
        items = _load()
        items.append({"id": uuid.uuid4().hex[:8], "text": text[:300], "event": ev, "created": time.time(), "tries": 0})
        _save(items)
    return f"OK: I'll remind them {EVENTS[ev]}: \"{text}\". It stays until it's said."


def listing():
    items = _load()
    if not items:
        return "OK: no reminders waiting for a moment (home, leaving, bed, PC, morning)."
    return "OK: reminders waiting: " + "; ".join(f"{EVENTS[i['event']]}: {i['text']}" for i in items) + "."


def cancel(what):
    w = str(what or "").lower().strip()
    with _lock:
        items = _load()
        if w in ("all", "every", "everything"):
            gone = items
        else:
            gone = [i for i in items if w and (w in i["text"].lower() or event_for(w) == i["event"])]
        if not gone:
            return f"FAILED: no waiting reminder matches \"{what}\". Nothing was cancelled."
        _save([i for i in items if i not in gone])
    return f"OK: cancelled {len(gone)} reminder{'s' if len(gone) != 1 else ''}: " + "; ".join(i["text"] for i in gone) + "."


def fire(event, now=None):
    """The moment happened: say every reminder waiting for it. -> how many were handed over to be said."""
    with _lock:
        due = [i for i in _load() if i["event"] == event]
    if not due:
        return 0
    text = " ".join(f"Reminder: {i['text']}." for i in due)
    if say(text, "reminder", f"reminders:{event}:{','.join(i['id'] for i in due)}", now=now):
        with _lock:
            _save([i for i in _load() if i["id"] not in {d["id"] for d in due}])
        log.info("TRIGGER: %s -> reminded: %s", event, text)
        return len(due)
    log.info("TRIGGER: %s -> %d reminder(s) kept for later (not a good moment)", event, len(due))
    return 0


# ---------------------------------------------------------------- saying things
_retry = []  # [(text, kind, key)] reminders that couldn't be said: tried again when the conversation ends


def say(text, kind, key, now=None):
    """Hand a line to the proactive policy. -> True if it will be said (now, or right after the current conversation)."""
    from room_agent import proactive

    ev = proactive.Event(kind, key, text, data={"say": text})
    if now:
        ev.at = now
    decision, why = proactive.decide(ev, now=now)
    if decision == proactive.SPEAK_NOW:
        deliver(text)
        return True
    if decision == proactive.WAIT:
        return True  # (proactive.on_idle hands it back after the conversation: after_conversation())
    if kind == "reminder":
        _retry.append((text, kind, key))
    return False


def deliver(text):
    """Speak it now: Jarvis says it and listens for a reply, like a greeting (the voice loop picks it up)."""
    from room_agent.conversation import greet

    greet.say_soon(text)


def after_conversation(decisions):
    """The conversation ended (conversation/loops.py): say what waited for it; retry reminders kept for later."""
    from room_agent import proactive

    for ev, decision in decisions:
        if decision == proactive.SPEAK_NOW and ev.data.get("say"):
            deliver(ev.data["say"])
    waiting, _retry[:] = list(_retry), []
    for text, kind, key in waiting:
        say(text, kind, key + ":retry")


# ---------------------------------------------------------------- the moments
def on_door(event):
    from room_agent.conversation import greet

    away = time.time() - greet.last_activity()
    if away >= config.GREET_AWAY_MIN * 60:
        fire("home")
        _maybe_morning()
    else:
        fire("leave")
        rain_check()


def on_bed(event):
    name = str(event.get("device", ""))
    beds = [n for a, n in _aliases().items() if a in ("bed", "my bed")]
    if (beds and name not in beds) or (not beds and "vibration" not in name.lower()):
        return
    hour = datetime.datetime.now().hour
    if hour >= 20 or hour < 4:
        fire("bed")


def _aliases():
    try:
        from room_agent.tools.zigbee import Hub

        return Hub.aliases()
    except Exception:
        return {}


def _maybe_morning(now=None):
    now = now or time.time()
    t = datetime.datetime.fromtimestamp(now)
    day = t.date().isoformat()
    if 5 <= t.hour < 12 and _state["morning_done"] != day:
        _state["morning_done"] = day
        fire("morning", now=now)
        plans_today(now)


def on_voice(event=None):
    _maybe_morning()


# ---------------------------------------------------------------- the PC (keyboard / mouse)
def idle_seconds():
    """Seconds since the last keyboard or mouse input on this PC (Windows), or None."""
    if os.name != "nt":
        return None
    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def desk_tick(idle, now=None):
    """One check of PC activity (every few seconds). Fires 'desk' on return, nudges a break after long nonstop use."""
    now = now or time.time()
    away_s = config.DESK_AWAY_MIN * 60
    if idle is None:
        return
    if idle >= away_s:
        _state["away"] = True
        _state["active_since"] = None
        return
    if _state["away"]:  # (back after being away)
        _state["away"] = False
        _state["active_since"] = now - idle
        fire("desk", now=now)
        _maybe_morning(now)
    if _state["active_since"] is None:
        _state["active_since"] = now - idle
    active = now - _state["active_since"]
    limit = config.BREAK_REMINDER_MIN * 60
    if limit and active >= limit and _state["break_done_for"] != _state["active_since"]:
        _state["break_done_for"] = _state["active_since"]
        hours = active / 3600
        say(f"You've been at the computer for {hours:.0f} hour{'s' if round(hours) != 1 else ''} straight. Maybe stand up and "
            "stretch for a minute?", "nudge", f"break:{int(_state['active_since'])}", now=now)


GOOGLE_CHECK_S = 6 * 3600


def google_health(now=None, provider=None):
    """Every GOOGLE_CHECK_S: refresh the connected Google account's token in the background, so an expired connection
    is found (and said, once: integrations/google/provider.py) before they need their email. A token refresh is a free
    call to Google's sign-in service. -> True / False / None (nothing to check)."""
    now = now or time.time()
    if now - _state.get("google_at", 0) < GOOGLE_CHECK_S:
        return None
    _state["google_at"] = now
    try:
        if provider is None:
            from room_agent.integrations import provider as get

            provider = get("google")
        if not provider.configured() or provider.connection_status() != "connected":
            return None
        ok = provider.refresh_credentials()
        log.info("triggers: Google connection check: %s", "fine" if ok else "NOT working")
        return ok
    except Exception as e:
        log.debug("triggers: Google check failed: %s", e)
        return None


def _desk_loop():
    while True:
        try:
            desk_tick(idle_seconds())
            meetings_check()
            google_health()
        except Exception as e:
            log.debug("triggers: desk check failed: %s", e)
        time.sleep(5)


# ---------------------------------------------------------------- nudges with outside information
def rain_check(now=None, forecast=None):
    """When they leave: rain likely today? Said once a day."""
    if not config.RAIN_ALERT:
        return False
    day = datetime.date.fromtimestamp(now or time.time()).isoformat()
    if _state["rain_done"] == day:
        return False
    if forecast is None:
        try:
            from room_agent.tools import weather

            forecast = weather.get_weather()
        except Exception as e:
            log.debug("triggers: no forecast (%s)", e)
            return False
    m = re.search(r"Today[^:]*:[^;\n]*?(\d+)% chance of rain", str(forecast))
    if not m or int(m.group(1)) < config.RAIN_ALERT_PCT:
        return False
    _state["rain_done"] = day
    return say(f"Heads up, there's a {m.group(1)}% chance of rain today. Maybe take a jacket.", "nudge", f"rain:{day}", now=now)


def meetings_check(now=None, events=None):
    """MEETING_ALERT_MIN before a calendar event: one heads-up per event (needs Google Calendar connected)."""
    if not config.MEETING_ALERT_MIN:
        return 0
    now = now or time.time()
    if events is None:
        if now - _state.get("meetings_at", 0) < 120:
            return 0
        _state["meetings_at"] = now
        events = _calendar_soon(now)
    said = 0
    for e in events or []:
        start = e["start"].timestamp() if hasattr(e["start"], "timestamp") else float(e["start"])
        mins = (start - now) / 60
        if e.get("all_day") or not 0 < mins <= config.MEETING_ALERT_MIN or e["id"] in _state["meetings_done"]:
            continue
        _state["meetings_done"].add(e["id"])
        if say(f"Heads up: \"{e['title']}\" starts in {max(1, round(mins))} minute{'s' if round(mins) != 1 else ''}.",
               "meeting", f"meeting:{e['id']}", now=now):
            said += 1
    return said


def _calendar_soon(now):
    try:
        from room_agent.integrations import provider

        g = provider("google")
        if g.connection_status() != "connected" or "calendar" not in str(g.available_services(g.active())):
            return []
        from room_agent.integrations.capabilities import _cal

        svc = _cal()
        s = datetime.datetime.fromtimestamp(now, svc.tz())
        return svc.events(s, s + datetime.timedelta(minutes=config.MEETING_ALERT_MIN + 1), limit=10)
    except Exception as e:
        log.debug("triggers: calendar not checked (%s)", e)
        return []


_PLAN_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I)


def plans_today(now=None, plans=None):
    """The morning a remembered plan is due: mention it once ('Dentist on Oct 9, 2026' -> on Oct 9)."""
    if not config.PLAN_FOLLOWUPS:
        return 0
    today = datetime.date.fromtimestamp(now or time.time())
    if plans is None:
        try:
            plans = [f["content"] for f in rt.memory.facts() if f["category"] == "plan"]
        except Exception:
            plans = []
    due = []
    for p in plans:
        m = _PLAN_DATE.search(p)
        if not m:
            continue
        try:
            when = datetime.datetime.strptime(f"{m.group(1)[:3]} {m.group(2)} {m.group(3)}", "%b %d %Y").date()
        except ValueError:
            continue
        if when == today and p not in _state["plans_done"]:
            due.append(_PLAN_DATE.sub("", p).strip(" ,.-") or p)
            _state["plans_done"].add(p)
    if due and say("Just so you know, today: " + "; ".join(due) + ".", "reminder", f"plans:{today}", now=now):
        return len(due)
    return 0


def start():
    from room_agent.actions.events import events

    events.on("door.opened", on_door)
    events.on("vibration.detected", on_bed)
    threading.Thread(target=_desk_loop, name="triggers-desk", daemon=True).start()
