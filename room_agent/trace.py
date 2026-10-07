"""Development trace: one compact block per turn showing what was decided and why. Off unless TRACE=1.

    INPUT: 'Set a timer for five seconds'      AUDIO: USER (started 2300ms after it stopped)
    INTENT: SET_TIMER   CAPABILITY: set_timer=true   PARAMS: seconds=5   MISSING: none
    ACTION: set_timer   RESULT: verified   RESPONSE: "Done."
"""

import logging

from room_agent import config

log = logging.getLogger("room-agent")
_lines = []


def note(key, value):
    if config.TRACE:
        _lines.append(f"{key}: {value}")


def has(key):
    return any(line.startswith(key + ":") for line in _lines)


def flush():
    if config.TRACE and _lines:
        log.info("TRACE\n    " + "\n    ".join(_lines))
    _lines.clear()
