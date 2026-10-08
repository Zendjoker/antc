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
    # (cognition/) all optional:
    expect: Optional[Callable[[dict, Any], dict]] = None  # (args, state before) -> what observe() must show afterwards,
                                                   # e.g. {"monitor.num": 2}: checked as EXPECTED vs OBSERVED
    skip_if_satisfied: bool = False                # with expect: if it's already so, don't act at all ("already muted")
    fresh_for: float = 15.0                        # read-only results / observed state count as VERIFIED this long (s)
    reflex: list = field(default_factory=list)     # [(regex with named groups = arguments, default args)]: run without
                                                   # a model call when a request matches exactly (cognition/reflex.py)
    reflex_check: Optional[Callable[[dict], bool]] = None  # a reflex only fires if this agrees (e.g. the app exists)
    reflex_say: Optional[Callable[[Any], str]] = None  # (verified ActionResult) -> the short spoken confirmation
    # How an OK is known to be true (filled in at registration; see VERIFICATION below):
    #   independent  the executor reads the state before and after (observe + verify / expect)
    #   internal     the tool itself checks the outcome before saying OK (`verified_by` says how)
    #   none         nobody checks: an OK is reported as UNVERIFIED, never as a verified success
    #   read         changes nothing
    verification: str = ""
    verified_by: str = ""

    def schema(self):
        """The tool definition the model sees."""
        description = self.description
        if self.examples:
            description += " E.g. " + "; ".join(f"'{e}'" for e in self.examples[:4]) + "."
        if not self.changes_state and self.name != "update_goal":
            description = "Read-only (changes nothing): " + description
        props = (self.parameters or {}).get("properties", {})
        if any(isinstance(s, dict) and s.get("x-ask") for s in props.values()):  # (actions/pending.py collects the rest)
            description += (" Call it as soon as they ask, even with details missing (pass only what they said): it keeps "
                            "the request and tells you what to ask; don't ask for the details yourself first.")
        return {"name": self.name, "description": description, "input_schema": _public(self.parameters)}


def _public(schema):
    """The schema without Jarvis's own "x-..." hints (questions, cues: see actions/pending.py): models get plain JSON
    Schema."""
    if not isinstance(schema, dict):
        return schema
    out = {k: v for k, v in schema.items() if not k.startswith("x-")}
    if schema.get("x-optional-when") and "required" in out:  # (required only sometimes: the description says when)
        out["required"] = [k for k in out["required"] if k not in schema["x-optional-when"]]
    if isinstance(out.get("properties"), dict):
        out["properties"] = {k: _public(v) for k, v in out["properties"].items()}
    return out


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
    if not cap.verification:
        if not cap.changes_state:
            cap.verification = "read"
        elif cap.observe and (cap.verify or cap.expect):
            cap.verification = "independent"
        elif cap.name in VERIFICATION:
            cap.verification, cap.verified_by = "internal", VERIFICATION[cap.name]
        else:
            cap.verification = "none"
    validate.SCHEMAS[cap.name] = cap.parameters
    if cap.risk == Risk.CONFIRM and cap.min_confidence:
        validate.HIGH_IMPACT[cap.name] = cap.min_confidence
    for kind in ([cap.claim] if isinstance(cap.claim, str) else cap.claim or []):
        truth.VERIFIED_BY.setdefault(kind, set()).add(cap.name)
    if cap.changes_state:
        truth.ACTION_TOOLS.add(cap.name)
    return cap


# Tools that check their own outcome before answering OK, and how (the evidence). Anything state-changing that is neither
# here nor independently verified (observe + verify) is reported UNVERIFIED. tests/test_tool_registry.py keeps this honest.
VERIFICATION = {
    "open_url": "the browser's address bar / window title shows the site",
    "browser_search": "the browser's address bar shows the results page",
    "browser_navigate": "the address changed / the tab count grew / the page reloaded / the tab is selected",
    "browser_click": "the address changed to the link's target (links) or the page changed (buttons)",
    "browser_click_sensitive": "the address changed to the link's target (links) or the page changed (buttons)",
    "browser_type": "the field's value is read back",
    "browser_scroll": "the page's scroll position moved",
    "copy_link": "the clipboard is read back",
    "open_research_source": "the browser's address bar shows the source",
    "save_file": "the file is read back from disk",
    "save_research_report": "the file is read back from disk",
    "make_folder": "the folder exists afterwards",
    "move_file": "the file is at the new place and gone from the old one",
    "delete_file": "the file is gone (in the Recycle Bin)",
    "open_file": "a window showing the file appeared",
    "add_to_list": "the list is read back from disk", "check_off": "the list is read back from disk",
    "remove_from_list": "the list is read back from disk", "clear_list": "the list is read back from disk",
    "take_note": "the notes are read back from disk",
    "remind_me_when": "the reminder is read back from disk", "cancel_moment_reminder": "the reminders are read back from disk",
    "set_dark_mode": "the theme setting is read back", "set_brightness": "each monitor's brightness is read back",
    "set_bluetooth": "the radio's state is read back", "set_wifi": "the radio's state is read back",
    "play_music": "Windows' media controls show something new playing",
    "play_pause": "the media session's playback status is read back",
    "next_track": "the media session's track changed", "previous_track": "the media session's track changed",
    "focus_app": "the app's window is the foreground window", "focus_window": "the window is the foreground window",
    "cancel_timer": "the timer is gone from the running timers",
    "remember": "the memory database confirms the row", "forget": "the memory database confirms the rows are gone",
    "emergency_stop": "the turn is cancelled and the speech queue is empty",
    "allow_screen_vision": "the setting is saved", "add_vip": "the setting is saved", "remove_vip": "the setting is saved",
    "set_listening_patience": "the setting is applied", "set_speaking_rate": "the setting is applied",
    "set_speaking_style": "the setting is saved", "set_voice": "the voice is switched and saved",
    "set_pronunciation": "the pronunciation is saved", "forget_pronunciation": "the pronunciation is removed",
    "go_quiet": "the state is quiet mode",
    "text_me": "Twilio accepted the text (delivery to the phone isn't checked)",
    "call_me": "Twilio accepted the call (whether it rang isn't checked)",
    "gmail_create_draft": "Gmail returns the saved draft", "gmail_update_draft": "Gmail returns the updated draft",
    "gmail_send": "Gmail returns the sent message's id",
    "calendar_create_event": "the event is read back from Calendar", "calendar_update_event": "the event is read back from Calendar",
    "calendar_delete_event": "Calendar no longer returns the event",
    "learn_preference": "the preference store confirms it", "forget_preference": "the preference store confirms it",
    "set_automation": "the preference store confirms it",
}


def register_group(group: Group):
    GROUPS[group.name] = group
    return group


# Modules that register capabilities when loaded: one per area (room_agent/abilities/), then the bigger layers.
# A new feature = one module that registers its capabilities (+ group, rules, claims, context) and a line here.
MODULES = ["room_agent.abilities.safety", "room_agent.abilities.info", "room_agent.abilities.location", "room_agent.abilities.timers", "room_agent.abilities.apps",
           "room_agent.abilities.windows", "room_agent.abilities.computer", "room_agent.abilities.lists", "room_agent.abilities.files", "room_agent.abilities.pcsettings", "room_agent.abilities.tasks", "room_agent.abilities.missions", "room_agent.abilities.media", "room_agent.abilities.memory",
           "room_agent.abilities.presence", "room_agent.abilities.voice", "room_agent.abilities.home",
           "room_agent.abilities.undo", "room_agent.abilities.phone", "room_agent.abilities.zigbee", "room_agent.abilities.system", "room_agent.learning.capabilities", "room_agent.integrations.capabilities"]
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


def summaries(capability_type, user_text=None):
    """One line per area for the model's "what I can do" list (built from the registry, not written by hand).
    With `user_text` (a request), an area's details are given only when it's relevant to it (its words, or in use right
    now); the others are listed by name, so every area is known but the list stays short as areas are added."""
    ensure_loaded()
    out = []
    for g in [g for g in GROUPS.values() if g.title]:
        try:
            ok = bool(g.available())
            relevant = (user_text is None or not ok or g.hints is None or g.hints.search(user_text)
                        or bool(g.live()))  # (an area without trigger words is always described)
            out.append(capability_type(g.title, ok, (g.summary() if callable(g.summary) else g.summary) if relevant else ""))
        except Exception:
            out.append(capability_type(g.title, False, "couldn't check right now"))
    for title, available, detail in LINES:
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
