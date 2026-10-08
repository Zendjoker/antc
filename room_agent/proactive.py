"""When something happens in the room (the door, presence, a device), should Jarvis say something? One deterministic,
inspectable policy for every such event, instead of each feature deciding on its own.

    decide(event) -> SPEAK_NOW   say it now (e.g. a greeting when the door opens after a quiet while)
                     WAIT        hold it until the current conversation is over, then decide again (it may be stale)
                     SILENT      no voice: a note on the dashboard's activity feed (and the event history)
                     IGNORE      nothing (a duplicate, a flapping sensor, turned off in settings)

Inputs: the event's priority, whether a conversation is going on (or Jarvis is speaking, or on a call), quiet mode,
quiet hours (QUIET_HOURS), the user's settings, cooldowns and duplicates (kept on disk, so a restart doesn't re-greet).
A door opening proves the door opened, not who came in: nothing here names a person. Every decision is logged as
"PROACTIVE: <event> -> <decision> (<why>)".
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field

from room_agent import config
from room_agent import runtime as rt

log = logging.getLogger("room-agent")
SPEAK_NOW, WAIT, SILENT, IGNORE = "SPEAK_NOW", "WAIT", "SILENT", "IGNORE"
URGENT, NORMAL, LOW = 3, 2, 1

# per kind of event: priority, how long a repeat counts as the same event, minimum time between spoken ones, how long a
# waiting one stays worth saying
KINDS = {
    "arrival": dict(priority=NORMAL, dedupe_s=20, cooldown_s=lambda: config.GREET_COOLDOWN_MIN * 60, wait_s=0),
    "presence": dict(priority=LOW, dedupe_s=60, cooldown_s=lambda: 0, wait_s=0),
    "device_change": dict(priority=LOW, dedupe_s=5, cooldown_s=lambda: 0, wait_s=0),
    "device_offline": dict(priority=LOW, dedupe_s=6 * 3600, cooldown_s=lambda: 0, wait_s=0),
    "task_attention": dict(priority=NORMAL, dedupe_s=300, cooldown_s=lambda: 120, wait_s=600),
    "security": dict(priority=URGENT, dedupe_s=60, cooldown_s=lambda: 0, wait_s=0),
}


@dataclass
class Event:
    kind: str
    key: str                      # what makes two events "the same" (e.g. "door:Door sensor")
    text: str                     # what happened, for the log and the dashboard ("Door sensor opened")
    at: float = field(default_factory=time.time)
    data: dict = field(default_factory=dict)


_lock = threading.Lock()
_state = {"seen": {}, "spoken": {}}  # key -> last time; kind -> last time spoken (persisted)
_queue = []                          # [(Event, until)] waiting for the conversation to end
_loaded = {"done": False}


def _load():
    if _loaded["done"]:
        return
    _loaded["done"] = True
    try:
        data = json.loads(config.PROACTIVE_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _state["seen"].update(data.get("seen", {}))
            _state["spoken"].update(data.get("spoken", {}))
    except (FileNotFoundError, ValueError, OSError):
        pass


def _save():
    try:
        cutoff = time.time() - 24 * 3600
        _state["seen"] = {k: v for k, v in _state["seen"].items() if v >= cutoff}
        config.PROACTIVE_STATE_FILE.write_text(json.dumps(_state), encoding="utf-8")
    except OSError as e:
        log.debug("proactive: couldn't save state (%s)", e)


def quiet_hours(now=None):
    """QUIET_HOURS='23-7': nothing proactive is spoken between 11 PM and 7 AM (urgent things excepted)."""
    spec = (config.QUIET_HOURS or "").strip()
    if not spec or "-" not in spec:
        return False
    try:
        start, end = (int(x) for x in spec.split("-", 1))
    except ValueError:
        return False
    hour = time.localtime(now or time.time()).tm_hour
    return start <= hour < end if start < end else (hour >= start or hour < end)


def busy():
    """-> why Jarvis can't talk right now ('' = available)."""
    from room_agent.conversation.states import State

    if rt.ringing:
        return "an alarm is ringing"
    try:
        from room_agent.phone import state as phone

        if phone.in_call:
            return "on a call"
    except Exception:
        pass
    st = rt.state.state
    if st is State.QUIET:
        return "quiet mode"
    if st in (State.LISTENING, State.PROCESSING, State.SPEAKING, State.IDLE_CHECK):
        return "in a conversation"
    if st is State.STARTING:
        return "starting up"
    return ""


def decide(ev, now=None, enabled=True):
    """-> (decision, why). Records the event (deduplication, cooldowns) as a side effect."""
    _load()
    now = now or ev.at
    spec = KINDS.get(ev.kind, dict(priority=LOW, dedupe_s=30, cooldown_s=lambda: 0, wait_s=0))
    with _lock:
        last = _state["seen"].get(ev.key, 0.0)
        _state["seen"][ev.key] = now
        if not enabled:
            out = IGNORE, "turned off in settings"
        elif now - last < spec["dedupe_s"]:
            out = IGNORE, f"same as {now - last:.0f}s ago (duplicate or a flapping sensor)"
        else:
            out = _decide(ev, spec, now)
        if out[0] == SPEAK_NOW:
            _state["spoken"][ev.kind] = now
        _save()
    log.info("PROACTIVE: %s -> %s (%s)", ev.text, *out)
    if out[0] == SILENT:
        notify(ev, out[1])
    return out


def _decide(ev, spec, now):
    why_busy = busy()
    pr = spec["priority"]
    if pr >= URGENT:
        return (SPEAK_NOW, "urgent") if why_busy not in ("on a call",) else (WAIT, "urgent, after the call")
    if why_busy:
        if spec["wait_s"] > 0 and why_busy != "quiet mode":
            _queue.append((ev, now + spec["wait_s"]))
            return WAIT, f"{why_busy}: held until it's over"
        return SILENT, f"{why_busy}: not interrupting"
    if quiet_hours(now):
        return SILENT, "quiet hours"
    if pr <= LOW:
        return SILENT, "not worth speaking up for"
    since = now - _state["spoken"].get(ev.kind, 0.0)
    if since < spec["cooldown_s"]():
        return SILENT, f"already spoke about this {since / 60:.0f} min ago"
    return SPEAK_NOW, "available, and worth saying"


def notify(ev, why=""):
    """A silent notification: the dashboard's activity feed (no voice)."""
    try:
        from room_agent import control

        control._activity.append({"at": ev.at, "text": ev.text + (f" (not spoken: {why})" if why else ""), "kind": "notice"})
        del control._activity[:-40]
    except Exception as e:
        log.debug("proactive: no dashboard (%s)", e)


def on_idle(now=None):
    """The conversation just ended: decide again about what was waiting. -> [(Event, decision)] to act on."""
    now = now or time.time()
    out = []
    with _lock:
        waiting, _queue[:] = list(_queue), []
    for ev, until in waiting:
        if now > until:
            log.info("PROACTIVE: %s -> IGNORE (waited too long, no longer relevant)", ev.text)
            continue
        ev.at = now
        _state["seen"].pop(ev.key, None)  # (it isn't a duplicate of itself)
        out.append((ev, decide(ev, now)[0]))
    return out


def pending():
    return [ev for ev, _ in _queue]


def reset():
    with _lock:
        _state["seen"].clear()
        _state["spoken"].clear()
        _queue.clear()
