"""Timers, alarms and reminders. They're saved to disk, so they survive a restart."""

import datetime
import json
import logging
import queue
import re
import threading
import time

from room_agent import runtime as rt
from room_agent.audio.sounds import silence, tone
from room_agent.audio.speaker import finish_speaking
from room_agent.audio.tts import clip
from room_agent.config import (ALARM_GAP_S, ALARM_MAX_S, ALARM_MISSED_GRACE_S, QUIET_ALLOWS_TIMERS, REMINDERS_FILE,
                               TIMER_MAX_S, TIMER_MIN_S)

log = logging.getLogger("room-agent")

_items = {}  # id -> {"id", "kind" (timer | alarm), "due" (epoch), "label", "message", "repeat", "daily"}
_threads = {}
_lock = threading.RLock()
_acked = threading.Event()  # set when confirmed user speech stops the ringing (acknowledge_ring)
_ring_lock = threading.Lock()  # one ringing loop at a time: two due together mustn't talk over each other
_seq = 0


# ---------- storage ----------
def _save():
    """Write timers and alarms to disk. False if that failed."""
    try:
        tmp = REMINDERS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(_items.values()), indent=1), encoding="utf-8")
        tmp.replace(REMINDERS_FILE)
        return True
    except OSError as e:
        log.warning("couldn't save timers and alarms: %s", e)
        return False


def _arm(item):
    wait = min(max(0.0, item["due"] - time.time()), 86400)  # (Timer can't wait weeks; it re-arms in _fire)
    t = threading.Timer(wait, _fire, args=(item["id"],))
    t.daemon = True
    _threads[item["id"]] = t
    t.start()


def _add(**fields):
    """Schedule it and save it. Returns (item, saved_to_disk)."""
    global _seq
    with _lock:
        _seq += 1
        item = {"id": _seq, "set_at": time.time(), **fields}  # (set_at: for the dashboard's progress bar)
        _items[_seq] = item
        _arm(item)
        saved = _save()
    return item, saved


def _verified(item):
    """Read it back: it must be in the list and its timer thread must be running."""
    with _lock:
        thread = _threads.get(item["id"])
        return item["id"] in _items and thread is not None and thread.is_alive()


def _confirm(item, saved, text):
    if not _verified(item):
        return "FAILED: it couldn't be scheduled. Nothing is set."
    return "OK: " + text + ("" if saved else " Warning: it couldn't be saved to disk, so it won't survive a restart.")


def _next_day(due, now):
    when = datetime.datetime.fromtimestamp(due)
    while when.timestamp() <= now:
        when += datetime.timedelta(days=1)
    return when.timestamp()


def restore_timers():
    """Bring back what was set before the last shutdown. Something that rang while the app was off is rung
    now if it was only just missed; otherwise it's dropped (a daily alarm just waits for tomorrow)."""
    global _seq
    try:
        saved = json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return
    now = time.time()
    with _lock:
        for item in saved if isinstance(saved, list) else []:
            try:
                due = float(item["due"])
                item = {"id": int(item["id"]), "kind": item["kind"], "due": due, "label": str(item["label"]),
                        "message": str(item.get("message", "")), "repeat": bool(item.get("repeat")),
                        "daily": bool(item.get("daily"))}
            except (KeyError, TypeError, ValueError):
                continue
            if due <= now - ALARM_MISSED_GRACE_S:
                if not item["daily"]:
                    log.info("dropped %s '%s': it was due while the app was off", item["kind"], item["label"])
                    continue
                item["due"] = _next_day(due, now)
            elif due <= now:
                item["due"] = now + 5  # just missed: ring shortly
            _seq = max(_seq, item["id"])
            _items[item["id"]] = item
            _arm(item)
        _save()
    if _items:
        log.info("restored %d timer(s) and alarm(s)", len(_items))


# ---------- speaking ----------
def _say(text):
    print(f"Agent: {text}", flush=True)
    if rt.tts_enabled:
        rt.recent_speech.append(text)
        rt.speak_q.put(text)


def _chime(times):
    for _ in range(times):
        rt.speak_q.put(tone(1046, 0.15))
        rt.speak_q.put(silence(0.1))


def _nudge_pool(item):
    if re.search(r"\bwake\b|\bget up\b", f"{item['label']} {item['message']}".lower()):
        return "alarm"
    return "timer_nudge" if item["kind"] == "timer" else "reminder_nudge"


def _ring(item):
    """Keep ringing until you say something (or ALARM_MAX_S passes): the message, then varied nudges."""
    engine = rt.engine
    message, pool = _announcement(item), _nudge_pool(item)
    deadline = time.time() + ALARM_MAX_S if ALARM_MAX_S > 0 else float("inf")
    n = 0
    heard_before = engine.user_voice_at  # (only set while the agent is silent, so anything newer is you)
    _acked.clear()
    rt.ringing = item
    spoke = False
    try:
        _say(message)
        while True:
            finish_speaking()
            if engine.interrupted.is_set() or engine.user_voice_at > heard_before or _acked.is_set():
                spoke = True
                break  # you talked over it, in a pause, or your speech was confirmed (acknowledge_ring)
            gap_start = time.time()
            while time.time() - gap_start < ALARM_GAP_S and engine.user_voice_at <= heard_before and not _acked.is_set():
                time.sleep(0.1)
            if engine.user_voice_at > heard_before or _acked.is_set() or time.time() > deadline:
                spoke = time.time() <= deadline
                break
            n += 1
            _chime(2)
            if pool != "alarm" and n % 2 == 0:
                _say(message)  # (what it's about, again, between the nudges)
            else:
                line = rt.phrases.pick(pool)
                print(f"Agent: {line}", flush=True)
                rt.recent_speech.append(line)
                rt.speak_q.put(clip(line))
    finally:
        rt.ringing = None
        _acked.clear()
        if spoke and rt.last_ring and rt.last_ring.get("label") == item["label"]:
            rt.last_ring = {**rt.last_ring, "stopped": True, "at": time.time()}  # (latest verified state: it's stopped)
    engine.interrupted.clear()
    log.info("stopped ringing: %s", item["label"])


def acknowledge_ring():
    """Confirmed user speech while a timer or alarm rings: stop it now with nothing more queued, and keep it for a
    possible snooze. True if something was ringing."""
    item = rt.ringing
    if not item:
        return False
    _acked.set()
    while True:  # drop everything still queued
        try:
            rt.speak_q.get_nowait()
            rt.speak_q.task_done()
        except queue.Empty:
            break
    if rt.engine:
        rt.engine.flush()
        rt.engine.interrupted.set()  # cuts a sentence that is still streaming in (the next turn clears it)
    rt.last_ring = {"label": item["label"], "message": _announcement(item), "kind": item["kind"], "at": time.time(),
                    "stopped": True}
    log.info("acknowledged: %s", item["label"])
    _announce(item["kind"] + ".acknowledged", item)
    return True


def _announcement(item):
    if item["message"].strip():
        return item["message"].strip()
    if item["kind"] == "timer":
        return "Hey, your timer is done." if item["label"] == "timer" else f"Hey, your {item['label']} timer is done."
    return f"Hey, reminder: {item['label']}."


def _fire(tid):
    with _lock:
        item = _items.get(tid)
        if item and item["due"] - time.time() > 1:
            _arm(item)  # woke early because the wait was capped
            return
        _items.pop(tid, None)
        _threads.pop(tid, None)
        if item and item["daily"]:
            again = {k: v for k, v in item.items() if k not in ("id", "due")}
            _add(due=_next_day(item["due"], time.time()), **again)  # (this also saves)
        else:
            _save()
    if not item:
        return
    log.info("%s done: %s", item["kind"], item["label"])
    _announce("timer.finished" if item["kind"] == "timer" else "alarm.ringing", item)
    rt.last_ring = {"label": item["label"], "message": _announcement(item), "kind": item["kind"], "at": time.time(),
                    "stopped": False}  # (it's finished either way: the model must never think it's still counting down)
    if rt.state.quiet and not QUIET_ALLOWS_TIMERS:
        return
    if rt.tts_enabled:
        _chime(3)
    try:
        if item["repeat"] and rt.tts_enabled and rt.engine and rt.engine.capturing and _ring_lock.acquire(blocking=False):
            try:
                _ring(item)
            finally:
                _ring_lock.release()
        else:  # (also when another one is already ringing: say this one once, the ringing one keeps going)
            _say(_announcement(item))
    except Exception as e:
        log.error("announcement failed: %s", e)


# ---------- wording ----------
def _left(seconds):
    s = max(0, int(seconds))
    if s < 90:
        return f"{s} seconds"
    minutes = round(s / 60)
    if minutes < 90:
        return f"{minutes} minutes"
    hours, rest = divmod(minutes, 60)
    return f"{hours} hours" + (f" {rest} minutes" if rest else "")


def _when(due):
    d, now = datetime.datetime.fromtimestamp(due), datetime.datetime.now()
    if d.date() == now.date():
        day = "today"
    elif d.date() == (now + datetime.timedelta(days=1)).date():
        day = "tomorrow"
    else:
        day = d.strftime("%A %B %d")
    return f"{d.strftime('%I:%M %p').lstrip('0')} {day}"


def _notes(repeat, daily=False):
    return (" It repeats every day." if daily else "") + (
        " It keeps ringing until they say something." if repeat else " It rings once.")


# ---------- what they mean ----------
_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "couple": 2, "three": 3, "few": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
        "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "half": 0.5}
_UNIT = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
         "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600}
# "10 seconds", "10s", "2 hrs"; with words a space is required ("ten minutes", "half an hour"; never "as" = a + s)
_SPAN = re.compile(r"\b(\d+(?:\.\d+)?)\s*-?\s*(" + "|".join(sorted(_UNIT, key=len, reverse=True)) + r")\b|\b("
                   + "|".join(sorted(_NUM, key=len, reverse=True)) + r")(?:\s+an?)?[\s-]+("
                   + "|".join(sorted((u for u in _UNIT if len(u) > 1), key=len, reverse=True)) + r")\b", re.I)
_RELATIVE = re.compile(r"\b(in|after|for)\s+(?:about\s+|like\s+|another\s+)?(\d|a\b|an\b|half\b|"
                       + "|".join(k for k in _NUM if len(k) > 2) + r")", re.I)
_CLOCK = re.compile(r"\d{1,2}:\d{2}|\b\d{1,2}\s*(am|pm|a\.m\.|p\.m\.|o'?clock)(?!\w)", re.I)


def relative_seconds(text):
    """A length of time from now ("in 10 seconds", "in half an hour", "20 minutes"), or None. Clock times ("7:30",
    "8 AM") are not lengths of time."""
    text = str(text or "")
    if _CLOCK.search(text):
        return None
    total = sum((float(d) if d else _NUM[w.lower()]) * _UNIT[(du or wu).lower()] for d, du, w, wu in _SPAN.findall(text))
    return int(round(total)) if total >= 1 else None


def as_timer(name, args, said=""):
    """The user's goal, not the tool's name: an alarm, wake-up or reminder for a LENGTH of time from now ("set an alarm
    in 10 seconds") is a countdown timer. Returns set_timer arguments when a set_alarm call means that, else None."""
    if name != "set_alarm":
        return None
    args = args if isinstance(args, dict) else {}
    when = str(args.get("time") or "").strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", when):
        return None  # a real clock time
    seconds = relative_seconds(when) if when else None
    if seconds is None and _RELATIVE.search(str(said)):
        seconds = relative_seconds(said)  # (the model left the time out or put the wording in it)
    if seconds is None:
        return None
    return {"seconds": seconds, **{k: args[k] for k in ("label", "message", "ring_once") if args.get(k) not in (None, "")}}


# ---------- tools ----------
def set_timer(seconds, label="", message="", ring_once=False):
    if not TIMER_MIN_S <= seconds <= TIMER_MAX_S:
        return f"FAILED: a timer must be between {TIMER_MIN_S} second and {TIMER_MAX_S // 86400} days. Nothing was set."
    label = (label or "").strip() or "timer"
    item, saved = _add(kind="timer", due=time.time() + seconds, label=label, message=message, repeat=not ring_once, daily=False)
    due = datetime.datetime.fromtimestamp(item["due"]).strftime("%I:%M:%S %p")
    return _confirm(item, saved, f"timer '{label}' is running, {seconds} seconds, rings at {due}." + _notes(not ring_once))


def set_alarm(at, label="", message="", ring_once=False, daily=False, date=""):
    """An alarm or reminder for a clock time: the next time it's that time, or on `date` (YYYY-MM-DD)."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(at))
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        return f"FAILED: '{at}' isn't a time I can use. I need a 24-hour time like 07:30 or 17:00."
    now = datetime.datetime.now()
    if date:
        try:
            day = datetime.date.fromisoformat(str(date).strip())
        except ValueError:
            return f"FAILED: '{date}' isn't a date I can use (needs YYYY-MM-DD)."
        when = datetime.datetime.combine(day, datetime.time(int(m[1]), int(m[2])))
        if when <= now:
            return "FAILED: that time has already passed. Nothing was set."
    else:
        when = now.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
        if when <= now:
            when += datetime.timedelta(days=1)
    item, saved = _add(kind="alarm", due=when.timestamp(), label=(label or "").strip() or "alarm", message=message,
                       repeat=not ring_once, daily=daily)
    return _confirm(item, saved, f"alarm '{item['label']}' is set for {_when(item['due'])}, in {_left(item['due'] - time.time())}."
                    + _notes(not ring_once, daily))


def list_timers():
    with _lock:
        items = sorted(_items.values(), key=lambda i: i["due"])
    if not items:
        return "OK: no timers or alarms are set."
    now = time.time()
    return "OK: " + "; ".join(
        f"{i['label']}: {_left(i['due'] - now)} left" if i["kind"] == "timer"
        else f"{i['label']}: alarm at {_when(i['due'])}" + (", every day" if i["daily"] else "")
        for i in items)


def _announce(name, item):
    """Tell whoever listens (actions/events.py) without depending on them."""
    try:
        from room_agent.actions.events import events

        events.emit(name, label=item["label"], kind=item["kind"], message=item.get("message", ""))
    except Exception as e:
        log.debug("event %s not sent: %s", name, e)


def cancel_id(tid):
    """Cancel one timer or alarm by its id (for undo). Its label, or None if it's no longer set."""
    with _lock:
        item = _items.pop(tid, None)
        thread = _threads.pop(tid, None)
        if thread:
            thread.cancel()
        if item:
            _save()
    return item["label"] if item else None


def cancel_timer(label):
    wanted = label.lower().strip()
    with _lock:
        hits = [k for k, i in _items.items() if wanted == "all" or wanted in i["label"].lower()]
        for k in hits:
            _items.pop(k)
            thread = _threads.pop(k, None)
            if thread:
                thread.cancel()
        if hits:
            _save()
    if hits:
        return f"OK: cancelled {len(hits)} timer(s) or alarm(s)."
    done = rt.last_ring
    if done and time.time() - done["at"] < 600:
        return (f"OK: nothing to cancel. The {done['kind']} '{done['label']}' already went off and is finished; "
                f"nothing else matched '{label}'.")
    return f"OK: nothing set matched '{label}', nothing cancelled."
