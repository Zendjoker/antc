"""One process at a time runs missions from a given missions database.

    acquire()   takes an OS file lock on <MISSIONS_DB>.owner (Windows: msvcrt.locking; elsewhere: fcntl.flock). The OS
                drops the lock when the process ends, however it ends (crash, kill, power loss on reboot), so a lock
                file left behind is never "stale" in a way that blocks: the next process simply acquires it. What the
                previous owner wrote (pid, start time) is kept for the log ("took over from pid N, which is gone").
                Then a fresh fencing token is written to the database (owner table).
    fencing     engine claims a step only with the CURRENT owner's token (store.claim_step), so even a process that
                somehow kept running without the lock can't start mission work after another one took over.
Only the owner runs recovery at startup (engine.load) and the mission runner; a second Jarvis on the same database does
neither (it would otherwise mark the first one's running missions "interrupted" and run their steps in parallel).
"""

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from room_agent import config

log = logging.getLogger("room-agent")
_held = {"file": None, "token": None, "path": None, "previous": None}
_lock = threading.Lock()


class NotOwner(Exception):
    """Another process runs the missions on this database."""


def lock_path():
    return Path(str(Path(config.MISSIONS_DB).resolve()) + ".owner")


def _try_lock(f):
    if os.name == "nt":
        import msvcrt

        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)  # (byte 0; raises OSError if another process holds it)
    else:
        import fcntl

        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(f):
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_UN)
    except OSError:
        pass


def acquire(wait_s=2.0):
    """-> this process's fencing token (already the owner: the same token). Raises NotOwner if another live process
    holds the lock. wait_s: how long to keep trying - Windows releases a killed process's lock asynchronously (a
    moment after it died), so a restart right after a crash waits for that instead of skipping recovery."""
    from room_agent.missions.store import store

    with _lock:
        path = lock_path()
        if _held["file"] is not None and _held["path"] == path:
            return _held["token"]
        path.parent.mkdir(parents=True, exist_ok=True)
        f = open(path, "a+b")
        end = time.monotonic() + max(0.0, wait_s)
        while True:
            try:
                _try_lock(f)
                break
            except OSError:
                if time.monotonic() >= end:
                    f.close()
                    raise NotOwner("another Jarvis process is running the missions on this database")
                time.sleep(0.1)
        # (byte 0 is locked; the owner record is written after it, so it stays readable for the log / dashboard)
        f.seek(1)
        previous = f.read().decode("utf-8", errors="replace").strip()
        token = uuid.uuid4().hex
        f.seek(1)
        f.truncate()
        f.write(json.dumps({"pid": os.getpid(), "since": time.time(), "token": token[:8]}).encode())
        f.flush()
        store().set_owner(token, os.getpid())
        _held.update(file=f, token=token, path=path, previous=previous or None)
        if previous:
            try:
                prev = json.loads(previous)
                log.info("missions: took over from pid %s (no longer running)", prev.get("pid"))
            except ValueError:
                pass
        return token


def release():
    with _lock:
        f = _held["file"]
        if f is None:
            return
        _unlock(f)
        f.close()
        _held.update(file=None, token=None, path=None)


def token():
    return _held["token"] if _held["path"] == lock_path() else None


def info():
    """For the dashboard: who owns the missions (this process or not), and the previous owner if one was replaced."""
    return {"owner": token() is not None, "pid": os.getpid() if token() else None, "previous": _held["previous"]}
