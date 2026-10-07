"""EnvironmentContext: what's going on around Jarvis (not the conversation): the app being talked about, the window and
monitor last acted on, what's playing, the last action, what can be undone. "Move it to monitor 2", "maximize it",
"close it", "undo that" all read from here.

Fields that existing systems already keep (runtime.py) are read and written there, so nothing had to be migrated:
active_app <-> rt.last_active_app, pending_intent <-> rt.pending, ringing_event <-> rt.ringing / rt.last_ring.
Features fill in only what they know; the rest stays None.
"""

import time

from room_agent import runtime as rt

UNDO_KEEP, UNDO_FRESH_S = 10, 1800  # undo remembers the last 10 changes, for half an hour


class EnvironmentContext:
    def __init__(self):
        self.previous_app = None
        self.active_window = None    # {"app", "hwnd", "title"}
        self.active_monitor = None   # {"num", "where"}
        self.current_media = None    # {"title", "artist", "app", "status"}
        self.last_file = None        # (future: files)
        self.last_folder = None
        self.last_action = None      # ActionResult
        self.last_successful_action = None
        self.undo_stack = []         # ActionResults that have an undo, newest last
        # Things the conversation is about, by kind ("email", "thread", "draft", "event", "person", "email_list"...):
        # {"id", "label", "account", "at", ...}. "It", "that thread", "he", "send it" resolve against these.
        self.items = {}
        self.known_addresses = set()  # email addresses seen in real message headers this session (not in bodies)

    def remember_item(self, kind, item, label=""):
        self.items[kind] = {**item, "label": label or item.get("label", ""), "at": time.time()}

    def item(self, kind, max_age=3600):
        it = self.items.get(kind)
        return it if it and time.time() - it["at"] < max_age else None

    def forget_items(self, account=None):
        """Drop everything that came from this account (or everything): disconnect / account switch."""
        for k in [k for k, v in self.items.items() if account is None or v.get("account") == account]:
            self.items.pop(k, None)
        if account is None:
            self.known_addresses.clear()

    # ----- fields that live in runtime.py
    @property
    def active_app(self):
        return (rt.last_active_app or {}).get("name")

    @active_app.setter
    def active_app(self, name):
        if name and name != self.active_app:
            self.previous_app = self.active_app or self.previous_app
            rt.last_active_app = {"name": name, "action": "used", "at": time.time()}

    @property
    def pending_intent(self):
        return rt.pending

    @property
    def ringing_event(self):
        return rt.ringing or rt.last_ring

    # ----- actions
    def record(self, result):
        """After every action: remember it, and keep it for undo if it can be undone."""
        self.last_action = result
        if result.success:
            self.last_successful_action = result
            if result.can_undo:
                self.undo_stack = (self.undo_stack + [result])[-UNDO_KEEP:]

    def undoable(self, capability=None, subject=None):
        """The newest change that can still be undone (optionally: of this capability / kind, on this app)."""
        now = time.time()
        for r in reversed(self.undo_stack):
            if now - r.at > UNDO_FRESH_S:
                break
            if capability and capability not in (r.capability, r.kind):
                continue
            if subject and r.subject and subject != r.subject:
                continue
            return r
        return None

    def forget_undo(self, result):
        if result in self.undo_stack:
            self.undo_stack.remove(result)

    def describe(self):
        """A short line for the model (only what's known)."""
        parts = []
        if self.active_app and self.previous_app and self.previous_app != self.active_app:
            parts.append(f"previous app: {self.previous_app}")
        if self.active_monitor:
            parts.append(f"last window action on monitor {self.active_monitor['num']}")
        if self.last_action and time.time() - self.last_action.at < 600:
            a = self.last_action
            parts.append(f"last action: {a.capability} {'OK' if a.success else 'failed'}")
        about = [f"{k}: {v['label']}" for k, v in self.items.items()
                 if v.get("label") and not k.endswith("_list") and time.time() - v["at"] < 1800]
        if about:
            parts.append("talking about (pass 'last' or 'current' as the id to use these): " + "; ".join(about[:5]))
        undo = [r.undo_hint for r in reversed(self.undo_stack) if time.time() - r.at < UNDO_FRESH_S][:3]
        if undo:
            parts.append("can undo (newest first): " + "; ".join(undo))
        return "; ".join(parts)


env = EnvironmentContext()
