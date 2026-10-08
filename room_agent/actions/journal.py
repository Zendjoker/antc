"""Every action's lifecycle, observable in the log and kept on disk:

    REQUESTED -> PLANNED -> EXECUTING -> VERIFYING -> COMPLETED | FAILED | CANCELED   (or WAITING: asked to confirm)

An action is COMPLETED only when the executor verified it (actions/executor.py). The journal (last 300 actions, in
ACTIONS_JOURNAL_FILE) survives restarts: an action that was EXECUTING when Jarvis stopped is reported once after the
restart as "interrupted: its result is unknown", so nothing is claimed about it and nothing re-runs it blindly.
"""

import itertools
import json
import logging
import threading
import time

from room_agent import config
from room_agent import runtime as rt

log = logging.getLogger("room-agent")
REQUESTED, PLANNED, EXECUTING, VERIFYING, COMPLETED, FAILED, CANCELED, WAITING = (
    "REQUESTED", "PLANNED", "EXECUTING", "VERIFYING", "COMPLETED", "FAILED", "CANCELED", "WAITING")
UNVERIFIED, UNKNOWN = "UNVERIFIED", "UNKNOWN"  # (ran, nothing could check it / may or may not have happened)
FINAL = (COMPLETED, FAILED, CANCELED, WAITING, UNVERIFIED, UNKNOWN)
_lock = threading.Lock()
_ids = itertools.count(int(time.time() * 1000) % 10_000_000)
_entries = {}
_local = threading.local()
_interrupted = []  # actions cut off by the last shutdown (found at start)


def current():
    return getattr(_local, "jid", None)


def requested(name, args, private=False):
    jid = next(_ids)
    shown = sorted(args) if private else {k: v for k, v in (args or {}).items() if k != "confidence"}
    with _lock:
        _entries[jid] = {"id": jid, "action": name, "args": shown, "state": REQUESTED, "at": time.time(),
                         "turn": rt.turn_no, "history": [REQUESTED]}
    _local.jid = jid
    return jid


def state(new, note="", jid=None):
    jid = jid if jid is not None else current()
    with _lock:
        e = _entries.get(jid)
        if e is None or e["state"] in FINAL:
            return
        e["state"] = new
        e["history"].append(new)
        if note:
            e["note"] = note[:200]
    if new in FINAL:
        log.info("ACTION #%s %s: %s%s", jid, e["action"], " -> ".join(e["history"]), f" ({note[:100]})" if note else "")
        _save()
    elif new == EXECUTING:
        _save()  # (so a crash mid-action is visible after a restart)


def finish(result):
    """The executor's final word on the current action."""
    msg = result.message or ""
    new = {"verified": COMPLETED, "unverified": UNVERIFIED, "unknown": UNKNOWN, "waiting": WAITING,
           "canceled": CANCELED}.get(result.outcome, FAILED)
    if new == FAILED and ("interrupt" in msg.lower() or "cancel" in (result.error_code or "")):
        new = CANCELED
    state(new, "" if result.success else msg.split(":", 1)[-1].strip())
    _local.jid = None


def recent(n=20):
    with _lock:
        return sorted(_entries.values(), key=lambda e: e["at"])[-n:]


def _save():
    try:
        with _lock:
            items = sorted(_entries.values(), key=lambda e: e["at"])[-300:]
        config.ACTIONS_JOURNAL_FILE.write_text(json.dumps(items), encoding="utf-8")
    except OSError as e:
        log.debug("journal: couldn't save (%s)", e)


def load():
    """At start: read the journal; anything left EXECUTING or VERIFYING was cut off by the last shutdown."""
    try:
        items = json.loads(config.ACTIONS_JOURNAL_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return []
    cut = []
    with _lock:
        for e in items if isinstance(items, list) else []:
            if e.get("state") in (REQUESTED, PLANNED, EXECUTING, VERIFYING):
                e["state"], e["note"] = CANCELED, "interrupted by a restart: result unknown"
                e.setdefault("history", []).append(CANCELED)
                cut.append(e)
            _entries[e["id"]] = e
    _interrupted[:] = cut
    if cut:
        log.warning("journal: %d action(s) were cut off by the last shutdown: %s", len(cut),
                    "; ".join(e["action"] for e in cut))
        _save()
    return cut


def context_lines(user_text):
    """Right after a restart, once: actions whose outcome is unknown (never claim them, never just re-run them)."""
    if not _interrupted:
        return []
    items, _interrupted[:] = list(_interrupted), []
    return ["- interrupted_actions: Jarvis restarted while these were running, so their outcome is UNKNOWN: "
            + "; ".join(f"{e['action']} {e['args']}" for e in items[-3:])
            + ". Don't claim they happened; check the state (or say you're not sure) before doing them again."]


def register():
    from room_agent.actions import core

    core.register_context(context_lines, order=13)
