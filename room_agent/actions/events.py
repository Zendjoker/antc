"""A tiny event bus. Features announce what happened ("timer.finished", "app.opened", "window.moved"); anything can listen
without the announcing feature knowing about it. Handlers run right away, and one that fails never breaks the action.

    from room_agent.actions.events import events
    events.on("app.opened", lambda e: print(e["app"]))
    events.on("app.*", ...)   # every app event
"""

import logging
import time

log = logging.getLogger("room-agent")


class Events:
    def __init__(self):
        self._handlers = {}
        self.recent = []  # the last few events, for debugging and tests

    def on(self, name, handler):
        self._handlers.setdefault(name, []).append(handler)
        return handler

    def off(self, name, handler):
        if handler in self._handlers.get(name, []):
            self._handlers[name].remove(handler)

    def emit(self, name, **data):
        event = {"name": name, "at": time.time(), **data}
        self.recent = (self.recent + [event])[-50:]
        prefix = name.split(".")[0] + ".*"
        for handler in self._handlers.get(name, []) + self._handlers.get(prefix, []) + self._handlers.get("*", []):
            try:
                handler(event)
            except Exception as e:
                log.warning("event handler for %s failed: %s", name, e)
        return event


events = Events()
