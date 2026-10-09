"""Goal: what should become TRUE, tracked by code. Only for requests where state, dependencies or reasoning matter
(levels.py decides); "open Spotify" never gets one.

The model may fill in desired_state / success_conditions (the update_goal capability) inside its normal tool loop, so
interpreting a goal costs no extra model call. Code owns the status: SATISFIED is only accepted when no action of the
goal is left failed or waiting for the user.

Constraints are the user's own limits, enforced by the executor for the rest of the goal ("actually don't open
Chrome" blocks open_app Chrome). They're matched generically against capability names and arguments: no
capability-specific code.
"""

import itertools
import re
import time
from dataclasses import dataclass, field

PENDING, ACTIVE, WAITING, BLOCKED, SATISFIED, FAILED, CANCELLED = (
    "PENDING", "ACTIVE", "WAITING", "BLOCKED", "SATISFIED", "FAILED", "CANCELLED")
OPEN = (PENDING, ACTIVE, WAITING, BLOCKED)
_ids = itertools.count(1)

VERB_FORMS = {"open": "open", "opening": "open", "launch": "open", "launching": "open", "start": "open",
              "starting": "open", "close": "close", "closing": "close", "quit": "close", "kill": "close", "move": "move",
              "moving": "move", "send": "send", "sending": "send", "delete": "delete", "deleting": "delete",
              "remove": "delete", "play": "play", "playing": "play", "minimize": "minimize", "minimise": "minimize",
              "maximize": "maximize", "maximise": "maximize", "mute": "mute", "change": "", "touch": "", "use": ""}
FORBID = re.compile(r"\b(?:don'?t|do not|no need to|never|skip|stop)\s+(?:bother\s+)?(?:with\s+)?(" + "|".join(VERB_FORMS) +
                    r")\s+(?:the\s+|my\s+|a\s+)?([a-z0-9][\w .+'&-]{1,30}?)\s*(?=[,.!?;]|$|\s+(?:and|but|or|then|yet)\b)",
                    re.I)


@dataclass
class Constraint:
    text: str        # as they said it
    verb: str        # "open", "close"... ("" = anything that changes it)
    subject: str     # "chrome"

    def blocks(self, capability, args, subject=None):
        """Does this constraint forbid running `capability` with `args`? (Read-only capabilities are never blocked.)"""
        if self.verb and self.verb not in capability.lower().split("_"):
            return False
        haystack = " ".join([str(subject or "")] + [str(v) for v in (args or {}).values()]).lower()
        return bool(self.subject) and self.subject.lower() in haystack


def parse_constraints(text):
    out = []
    for m in FORBID.finditer(text or ""):
        subject = m.group(2).strip().lower()
        if subject in ("it", "that", "this", "them", "anything"):
            subject = ""
        out.append(Constraint(m.group(0).strip(), VERB_FORMS.get(m.group(1).lower(), ""), subject))
    return [c for c in out if c.subject]


@dataclass
class Goal:
    user_request: str
    objective: str = ""
    level: str = "DELIBERATE"
    desired_state: list = field(default_factory=list)       # "Spotify open on monitor 2", "dev server reachable"
    constraints: list = field(default_factory=list)         # Constraint
    success_conditions: list = field(default_factory=list)
    relevant_entities: dict = field(default_factory=dict)   # what it's about: {"app": "Spotify"}
    priority: str = "normal"
    status: str = ACTIVE
    trigger: str = ""        # WAITING goals: what would wake it up ("Andrew replies"); no event engine yet
    reason: str = ""         # why BLOCKED / FAILED / CANCELLED / WAITING
    summary: str = ""        # the model's own one-line account (update_goal), never its reasoning
    id: int = field(default_factory=lambda: next(_ids))
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    turns: int = 0

    def set(self, status, reason=""):
        self.status, self.updated_at = status, time.time()
        if reason:
            self.reason = reason

    @property
    def open(self):
        return self.status in OPEN

    def describe(self):
        parts = [f"goal: {self.objective or self.user_request!r} ({self.status.lower()}"
                 + (f": {self.reason}" if self.reason and self.status != ACTIVE else "") + ")"]
        if self.desired_state:
            parts.append("should end up true: " + "; ".join(self.desired_state[:4]))
        if self.success_conditions:
            parts.append("done when: " + "; ".join(self.success_conditions[:3]))
        if self.constraints:
            parts.append("they said: " + "; ".join(c.text for c in self.constraints))
        spec = getattr(self, "spec", None)
        if spec is not None and (spec.permissions or spec.missing or spec.conflicts):
            parts.append(spec.describe())
        return ". ".join(parts)
