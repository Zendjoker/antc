"""Enforceable cancellation for mission work.

Every run of a step gets a Token. The engine cancels it when the step times out, the mission is paused / stopped, or
the PC-wide emergency stop fires. Python can't kill a thread, so instead EVERY side effect a step can have goes through
a checkpoint that raises Cancelled once its token is cancelled:
    network        missions/net.py (before each request, between response chunks, before each web search)
    paid calls     missions/meter.py (before reserving budget) and missions/llm.py (before sending)
    database       the GuardedStore a step gets (before every write)
    files          missions/sitegen.py / outreach.py / coder.py (before writing, before swapping a site in)
    subprocesses   missions/sandbox.py (the coding worker's whole process tree is killed)
So a step that was abandoned (timed out, or stopped while blocked in a request) can finish the one request it was
blocked in (bounded by that request's own timeout), but can't do anything after it: no write, no further request, no
charge, no file. Its result is ignored by the engine.
"""

import threading
import time

_local = threading.local()


class Cancelled(Exception):
    """The work this thread is doing was stopped (timeout, pause, stop, emergency stop)."""


class CancelledBeforeSend(Cancelled):
    """Stopped BEFORE a request was sent (waiting for its turn, or at the last check before sending): nothing reached
    the provider, so a budget reservation for it is released (meter.paid), not counted."""


class Token:
    def __init__(self, mission_id="", step_key="", deadline=None, should_stop=None):
        self.mission_id, self.step_key = mission_id, step_key
        self.deadline = deadline
        self.reason = ""
        self._event = threading.Event()
        self._should_stop = should_stop  # () -> reason or "" (mission state, emergency stop): polled at most 1/s
        self._polled = 0.0

    def cancel(self, reason="stopped"):
        if not self._event.is_set():
            self.reason = reason
            self._event.set()

    @property
    def cancelled(self):
        if self._event.is_set():
            return True
        if self.deadline is not None and time.time() > self.deadline:
            self.cancel("timed out")
            return True
        if self._should_stop is not None and time.time() - self._polled >= 1.0:
            self._polled = time.time()
            try:
                why = self._should_stop()
            except Exception:  # noqa: BLE001 (a failing check must not stop work by accident... but log-free here)
                why = ""
            if why:
                self.cancel(why)
                return True
        return False

    def check(self):
        if self.cancelled:
            raise Cancelled(self.reason or "stopped")

    def check_before_send(self):
        if self.cancelled:
            raise CancelledBeforeSend(self.reason or "stopped")

    def wait(self, seconds):
        """Sleep that ends early (raising Cancelled) when the token is cancelled."""
        end = time.time() + seconds
        while time.time() < end:
            self.check()
            self._event.wait(min(0.25, max(0.0, end - time.time())))
        self.check()


def bind(token):
    _local.token = token


def current():
    return getattr(_local, "token", None)


def check():
    """A checkpoint: raises Cancelled if the work on this thread was stopped. No-op outside mission work."""
    t = current()
    if t is not None:
        t.check()


def check_before_send():
    """The checkpoint right before a (possibly paid) request goes out: raises CancelledBeforeSend."""
    t = current()
    if t is not None:
        t.check_before_send()


def cancelled():
    t = current()
    return bool(t is not None and t.cancelled)


def sleep(seconds):
    t = current()
    if t is None:
        time.sleep(seconds)
    else:
        t.wait(seconds)
