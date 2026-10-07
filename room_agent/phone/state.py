"""Are you driving? Told by your iPhone (Shortcuts: "When CarPlay connects / disconnects" or "When Driving Focus turns
on / off"). Plus whether a call with Jarvis is going on right now."""

import logging
import threading
import time

log = logging.getLogger("room-agent")
_lock = threading.Lock()
driving = None    # {"since", "source"} while driving, else None
in_call = None    # {"since", "direction"} while on a call with Jarvis, else None


def set_driving(on, source="iPhone"):
    """True / False from the phone. Announced as driving.started / driving.stopped."""
    global driving
    from room_agent.actions.events import events

    with _lock:
        was = driving is not None
        driving = {"since": time.time(), "source": source} if on else None
    if on and not was:
        log.info("driving: started (%s)", source)
        events.emit("driving.started", source=source)
    elif was and not on:
        log.info("driving: stopped")
        events.emit("driving.stopped", source=source)
    return driving


def is_driving():
    return driving is not None


def call_started(direction):
    global in_call
    in_call = {"since": time.time(), "direction": direction}


def call_ended():
    global in_call
    in_call = None


def describe():
    if not driving:
        return ""
    mins = int((time.time() - driving["since"]) // 60)
    return f"driving (for {mins} min)" if mins else "driving (just started)"
