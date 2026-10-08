"""EnvironmentContext: what's going on around Jarvis (not the conversation): the app being talked about, the window and
monitor last acted on, what's playing, the last action, what can be undone. "Move it to monitor 2", "maximize it",
"close it", "undo that" all read from here.

Fields that existing systems already keep (runtime.py) are read and written there, so nothing had to be migrated:
active_app <-> rt.last_active_app, pending_intent <-> rt.pending, ringing_event <-> rt.ringing / rt.last_ring.
Features fill in only what they know; the rest stays None.

Beliefs: facts about the world with how much they can be trusted right now. The executor records them (what an
observation or a verified action showed); nothing else has to. Each one knows its source, method, confidence and age:
    VERIFIED     observed (or read back after an action) recently
    BELIEVED     reported or inferred, never observed
    STALE        it was true when observed, but that's too long ago to rely on
    CONFLICTING  two recent sources disagree
    UNKNOWN      no belief at all (Jarvis may say "I don't know" and should observe, ask, never invent)
"""

import time
from dataclasses import dataclass
from typing import Any

from room_agent import runtime as rt

UNDO_KEEP, UNDO_FRESH_S = 10, 1800  # undo remembers the last 10 changes, for half an hour
VERIFIED, BELIEVED, UNKNOWN, STALE, CONFLICTING = "VERIFIED", "BELIEVED", "UNKNOWN", "STALE", "CONFLICTING"
OBSERVED_METHODS = ("observed", "action-verified")
MAX_BELIEFS = 300


@dataclass
class Belief:
    key: str                  # "media.volume", "apps.Spotify.running", "inspect_ports(port=8000)"...
    value: Any
    source: str               # the capability (or other component) it came from
    method: str = "observed"  # observed | action-verified | reported | inferred
    confidence: float = 1.0
    observed_at: float = 0.0
    stale_after: float = 60.0  # seconds after which it's STALE
    conflict: Any = None       # {"value", "source", "at"} when another recent source disagreed

    def status(self, now=None):
        now = now or time.time()
        if self.conflict and now - self.conflict["at"] < self.stale_after:
            return CONFLICTING
        if now - self.observed_at > self.stale_after:
            return STALE
        return VERIFIED if self.method in OBSERVED_METHODS else BELIEVED

    def describe(self, now=None):
        now = now or time.time()
        age = int(now - self.observed_at)
        value = self.value if not isinstance(self.value, str) or len(self.value) <= 140 else self.value[:140] + "..."
        return f"{self.key} = {value} ({self.status(now).lower()}, {age}s ago, from {self.source})"


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
        self.beliefs = {}             # key -> Belief (see the module doc)

    # ----- beliefs about the world
    def believe(self, key, value, source, method="observed", confidence=1.0, stale_after=60.0, at=None):
        """Record what a source says is true. A fresh observation replaces older or weaker beliefs; a weaker claim that
        disagrees with a fresh observation doesn't replace it but marks the belief CONFLICTING."""
        now = at or time.time()
        old = self.beliefs.get(key)
        new = Belief(key, value, source, method, confidence, now, stale_after)
        if old and old.value != value and old.status(now) in (VERIFIED, BELIEVED):
            if method not in OBSERVED_METHODS and old.method in OBSERVED_METHODS:
                old.conflict = {"value": value, "source": source, "at": now}  # (the observation is kept)
                return old
            if method not in OBSERVED_METHODS and old.method not in OBSERVED_METHODS and old.source != source:
                new.conflict = {"value": old.value, "source": old.source, "at": now}
        self.beliefs[key] = new
        if len(self.beliefs) > MAX_BELIEFS:
            for k in sorted(self.beliefs, key=lambda k: self.beliefs[k].observed_at)[: len(self.beliefs) - MAX_BELIEFS]:
                self.beliefs.pop(k, None)
        return new

    def belief(self, key):
        """The Belief for `key`, or None (UNKNOWN)."""
        return self.beliefs.get(key)

    def status_of(self, key):
        b = self.beliefs.get(key)
        return b.status() if b else UNKNOWN

    def relevant_beliefs(self, words=(), limit=6, sources=()):
        """Beliefs whose key or value mentions any of `words`, or that came from one of `sources` (capabilities about
        the same thing), newest first. Nothing to go on: the most recent ones."""
        words = [w.lower() for w in words if len(w) >= 3]
        found = [b for b in self.beliefs.values()
                 if (not words and not sources) or b.source in sources
                 or any(w in f"{b.key} {b.value}".lower() for w in words)]
        return sorted(found, key=lambda b: -b.observed_at)[:limit]

    def forget_beliefs(self, prefix=""):
        for k in [k for k in self.beliefs if k.startswith(prefix)]:
            self.beliefs.pop(k, None)

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
