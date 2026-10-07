"""The capability registry: the one list of what Jarvis can actually do.

A feature plugs in by registering Capability objects (and, optionally, a Group describing the area: when its tools are
worth offering to the model and one line for the "what I can do" list). The model is offered exactly the registered,
available capabilities; nothing else can run. Adding a feature = implement it, register it. No router or prompt edits.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


class Risk(str, Enum):
    SAFE = "safe"            # just do it (open an app, change the volume, focus a window)
    CONFIRM = "confirm"      # do it when the request is clear, otherwise ask first (close an app, cancel a timer)
    SENSITIVE = "sensitive"  # always ask first, even when clear (delete data, send messages, purchases, installs)


@dataclass
class Capability:
    name: str
    description: str
    parameters: dict                               # JSON schema of the arguments
    execute: Callable[[dict], str]                 # does it; returns "OK: ..." / "FAILED: ..." / ... (the model reads it)
    group: Optional[str] = None                    # area (apps, media, window, timers...); None: always offered
    examples: list = field(default_factory=list)   # how people ask for it (semantic hints for the model)
    risk: Risk = Risk.SAFE
    min_confidence: float = 0.0                    # CONFIRM: below this intent confidence, ask first
    available: Callable[[], bool] = lambda: True   # can it work right now?
    observe: Optional[Callable[[dict], Any]] = None    # read the relevant state (before and after)
    verify: Optional[Callable[[dict, Any, Any], bool]] = None  # independent check that the change really happened
    undo: Optional[Callable[[dict, Any, Any], str]] = None     # (args, before, after) -> result text; None: can't undo
    undo_if: Optional[Callable[[Any, Any], bool]] = None  # (before, after): is there anything to undo this time?
    undo_is_symmetric: bool = False                # undo(args, after, before) redoes it (it restores a saved state)
    subject: Optional[Callable[[dict], str]] = None  # what it acts on ("spotify"): later steps on the same thing depend on it
    event: Optional[str] = None                    # emitted on success, e.g. "app.opened"
    claim: Any = None                              # spoken claims this confirms (truth.py kinds: "app", "media"...; or a list)
    changes_state: bool = True                     # False for pure reads (list_monitors, get_volume)
    private: bool = False                          # its results are personal data (email, calendar): never logged,
                                                   # never in learning records or long-term memory
    intent: Any = None                             # regex: the user's own words must ask for this (else: ask first)
    confirm_keys: Any = None                       # args that a "yes" must match (default: all of them)
    prepare: Optional[Callable[[dict], dict]] = None   # resolve references ("current draft" -> its id) before checks
    describe: Optional[Callable[[dict], str]] = None   # what it will do, for the confirmation question

    def schema(self):
        """The tool definition the model sees."""
        description = self.description
        if self.examples:
            description += " E.g. " + "; ".join(f"'{e}'" for e in self.examples[:4]) + "."
        return {"name": self.name, "description": description, "input_schema": self.parameters}


@dataclass
class Group:
    name: str
    hints: Optional[re.Pattern] = None             # recent words that make this area's tools worth offering
    live: Callable[[], bool] = lambda: False       # offered anyway while it's in use (a timer running, an app just opened)
    title: str = ""                                # for the "what I can do" list given to the model
    summary: str = ""
    available: Callable[[], bool] = lambda: True
    rules: list = field(default_factory=list)       # prompt lines for this area (sent only when its tools are)


REGISTRY: dict = {}  # name -> Capability, in registration order
GROUPS: dict = {}
_loaded = False


def register(cap: Capability):
    """Add a capability. Its schema, confidence rule and claim kind are wired into validation and the claim check."""
    from room_agent import truth
    from room_agent.tools import validate

    REGISTRY[cap.name] = cap
    validate.SCHEMAS[cap.name] = cap.parameters
    if cap.risk == Risk.CONFIRM and cap.min_confidence:
        validate.HIGH_IMPACT[cap.name] = cap.min_confidence
    for kind in ([cap.claim] if isinstance(cap.claim, str) else cap.claim or []):
        truth.VERIFIED_BY.setdefault(kind, set()).add(cap.name)
    if cap.changes_state:
        truth.ACTION_TOOLS.add(cap.name)
    return cap


def register_group(group: Group):
    GROUPS[group.name] = group
    return group


# Modules that register capabilities when loaded: one per area (room_agent/abilities/), then the bigger layers.
# A new feature = one module that registers its capabilities (+ group, rules, claims, context) and a line here.
MODULES = ["room_agent.abilities.info", "room_agent.abilities.location", "room_agent.abilities.timers", "room_agent.abilities.apps",
           "room_agent.abilities.windows", "room_agent.abilities.media", "room_agent.abilities.memory",
           "room_agent.abilities.presence", "room_agent.abilities.voice", "room_agent.abilities.home",
           "room_agent.abilities.undo", "room_agent.abilities.phone", "room_agent.learning.capabilities", "room_agent.integrations.capabilities"]
LINES = []    # extra "what I can do" lines that aren't a tool area: (title, available(), detail or detail())
CONTEXT = []  # (order, provider): provider(user_text) -> lines for the runtime context


def ensure_loaded():
    global _loaded
    if not _loaded:
        _loaded = True
        import importlib

        for module in MODULES:
            importlib.import_module(module)


def get(name):
    ensure_loaded()
    return REGISTRY.get(name)


def capabilities():
    ensure_loaded()
    return list(REGISTRY.values())


def offered():
    """Schemas of every capability that can work right now (what the model may call)."""
    out = []
    for cap in capabilities():
        group = GROUPS.get(cap.group)
        try:
            ok = cap.available() and (group is None or group.available())
        except Exception:
            ok = False
        if ok:
            out.append(cap.schema())
    return out


def summaries(capability_type):
    """One line per area for the model's "what I can do" list (built from the registry, not written by hand)."""
    ensure_loaded()
    out = []
    for title, available, detail in [(g.title, g.available, g.summary) for g in GROUPS.values() if g.title] + LINES:
        try:
            out.append(capability_type(title, bool(available()), detail() if callable(detail) else detail))
        except Exception:
            out.append(capability_type(title, False, "couldn't check right now"))
    return out


def register_line(title, detail, available=lambda: True):
    """A "what I can do" line that isn't a tool area (e.g. "snoozing an alarm")."""
    LINES.append((title, available, detail))


def register_context(provider, order=50):
    """provider(user_text) -> lines for the runtime context (live facts this area knows). Lower order comes first."""
    CONTEXT.append((order, provider))
    CONTEXT.sort(key=lambda x: x[0])


def context_lines(user_text):
    ensure_loaded()
    out = []
    for _, provider in CONTEXT:
        try:
            out += [line for line in provider(user_text) or [] if line]
        except Exception as e:
            import logging

            logging.getLogger("room-agent").warning("context provider failed: %s", e)
    return out


def register_claim(kind, pattern, verified_by=()):
    """Spoken claims of this kind ("timer's set") are held back unless one of `verified_by` (or any capability that
    declares claim=kind) returned OK this turn."""
    from room_agent import truth

    truth.add_claim(kind, pattern, verified_by)


def rules(tool_names=None):
    """Prompt rules of the areas whose tools are offered (all areas when tool_names is None)."""
    ensure_loaded()
    used = {c.group for c in REGISTRY.values() if tool_names is None or c.name in tool_names}
    out = [r for name, g in GROUPS.items() if name in used or (tool_names is None and not any(
        c.group == name for c in REGISTRY.values())) for r in g.rules]
    return list(dict.fromkeys(out))  # (areas may share a rule: say it once)
