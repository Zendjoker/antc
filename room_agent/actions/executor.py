"""The one place actions happen.

    request -> resolve capability -> validate parameters -> check availability -> check risk -> execute -> verify
            -> update context -> structured result

The model decides WHAT the user means (which capability, which arguments). This decides whether and how it can
actually happen, and what really happened. Several actions in one request run through a Plan, in order; an action
on something whose earlier step failed ("open FakeApp and move it to monitor 2") is not run.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from room_agent import runtime as rt
from room_agent import trace
from room_agent.actions import core
from room_agent.actions.context import env
from room_agent.actions.events import events

log = logging.getLogger("room-agent")
PREFIXES = ("OK", "FAILED", "UNAVAILABLE", "NEEDS_CONFIRMATION", "NEEDS")
ERROR_CODES = {"FAILED": "failed", "UNAVAILABLE": "unavailable", "NEEDS": "needs_input",
               "NEEDS_CONFIRMATION": "needs_confirmation"}
# Extension points: BEFORE_HOOKS (cap, args) -> args may fill in or adjust arguments (e.g. learned preferences);
# AFTER_HOOKS (cap, args, result) -> extra text for the result, after a follow-up action (e.g. an app's usual monitor).
BEFORE_HOOKS, AFTER_HOOKS = [], []
import re as _re

AFFIRM = _re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok(ay)?|do it|go ahead|go for it|send it|send|confirm(ed)?|please( do)?|"
                     r"absolutely|definitely|correct|right|that'?s (right|fine|good)|sounds good|delete it|move it|"
                     r"yes please)\b", _re.I)
NEGATE = _re.compile(r"\b(no|nope|don'?t|do not|wait|stop|cancel|hold on|not yet|never ?mind)\b", _re.I)
PRONOUNS = {"it", "that", "the app", "that app", "same app", "the program"}


@dataclass
class ActionResult:
    success: bool
    capability: str
    parameters: dict
    message: str                  # what the model reads; starts with OK: / FAILED: / UNAVAILABLE: / NEEDS...:
    verified: bool = False        # the new state was read back and matches
    state_before: Any = None
    state_after: Any = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    recoverable: bool = True      # worth asking again / fixing (vs. not possible at all)
    subject: Optional[str] = None  # the app (etc.) it acted on
    kind: Optional[str] = None     # for "undo the volume" / "move it back": a word that names this kind of change
    at: float = field(default_factory=time.time)
    undo_fn: Optional[Callable[[], str]] = None
    flip: Any = None               # (capability, args, state undo restores, state it leaves): makes the undo undoable
    undo_hint: str = ""

    @property
    def can_undo(self):
        return self.undo_fn is not None

    def to_model(self):
        return self.message

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items() if not callable(v)}


def _prefix(message):
    return next((p for p in sorted(PREFIXES, key=len, reverse=True) if message.startswith(p + ":")), None)


def _result(name, args, message, **kw):
    prefix = _prefix(message)
    ok = prefix == "OK"
    return ActionResult(success=ok, capability=name, parameters=args, message=message,
                        error=None if ok else message.split(":", 1)[-1].strip(), error_code=None if ok else ERROR_CODES.get(prefix),
                        recoverable=prefix != "UNAVAILABLE", **kw)


def subject_of(cap, args):
    """The app (or other thing) this call acts on, as a stable name; pronouns resolved the way the tool will."""
    if not cap or not cap.subject:
        return None
    raw = str(cap.subject(args) or "").strip()
    if not raw:
        return None
    from room_agent.tools import apps

    if raw.lower() in PRONOUNS:
        return env.active_app
    try:
        entry, _ = apps.find(raw)
    except Exception:
        entry = None
    return entry["name"] if entry else raw


def _observe(cap, args, before=None):
    if not cap.observe:
        return None
    try:
        return cap.observe(args, before)
    except Exception as e:
        log.debug("couldn't read the state for %s: %s", cap.name, e)
        return None


def _finish(action, result):
    trace.note("ACTION", action)
    trace.note("RESULT", "verified" if result.success else (_prefix(result.message) or "failed").lower())
    return result


def execute(name, args):
    """Run one capability call -> ActionResult."""
    from room_agent.tools.timers import as_timer
    from room_agent.tools.validate import validate

    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except ValueError:
            args = {}
    args = dict(args) if isinstance(args, dict) else {}
    as_countdown = as_timer(name, args, rt.turn_text)
    if as_countdown:  # what they want, not the tool's name: an "alarm in 10 seconds" is a countdown timer
        log.info("%s for a length of time from now: running set_timer(%s)", name, as_countdown)
        if rt.pending and rt.pending["tool"] == name:
            rt.pending["tool"] = "set_timer"  # (so it's cleared once the timer is set)
        name, args = "set_timer", as_countdown
    trace.note("INTENT", name.upper() + (f" (confidence {args['confidence']})" if "confidence" in args else ""))
    trace.note("PARAMS", ", ".join(f"{k}={v!r}" for k, v in args.items() if k != "confidence") or "none")

    cap = core.get(name)  # 1. resolve
    if cap is None:
        return _finish("REFUSED", _result(name, args, f"UNAVAILABLE: there's no tool called {name}."))
    available = name in {t["name"] for t in core.offered()}  # 2. availability
    trace.note("CAPABILITY", f"{name}={str(available).lower()}")
    if not available:
        return _finish("REFUSED", _result(name, args, f"UNAVAILABLE: {name} isn't available right now (that feature is off, "
                                                      "not connected or not built)."))
    clean, problem = validate(name, args)  # 3. parameters (+ the confidence rule for CONFIRM capabilities)
    if problem:
        trace.note("MISSING", ", ".join(rt.pending["missing"]) if problem.startswith("NEEDS:") and rt.pending else "none")
        return _finish("CLARIFY" if problem.startswith("NEEDS") else "REFUSED", _result(name, args, problem))
    trace.note("MISSING", "none")
    if cap.prepare:  # resolve references ("the current draft") so a "yes" is matched against what was really asked
        try:
            clean = cap.prepare(clean)
        except Exception as e:
            say = getattr(e, "say", None)
            return _finish("REFUSED", _result(name, clean, f"FAILED: {say() if say else e}"))
    confirmed = _confirmed(name, clean, cap)
    what = cap.describe(clean) if cap.describe else name
    if cap.intent is not None and not confirmed and not cap.intent.search(rt.turn_text or ""):
        # 4a. the user's own words this turn didn't ask for this (it may come from an email, or a guess): ask first
        rt.pending = {"tool": name, "args": clean, "confirm": True, "turn": rt.turn_no, "at": time.time()}
        return _finish("CLARIFY", _result(name, clean, "NEEDS_CONFIRMATION: nothing was done: they didn't ask for this "
                                                       f"in their own words ({what}). Ask them first, in one short "
                                                       "question; only if they say yes, call it again."))
    if cap.risk == core.Risk.SENSITIVE and not confirmed:  # 4b. risk: always asked first
        rt.pending = {"tool": name, "args": clean, "confirm": True, "turn": rt.turn_no, "at": time.time()}
        return _finish("CLARIFY", _result(name, clean, "NEEDS_CONFIRMATION: nothing was done. This can't be taken back "
                                                       f"easily ({what}), so ask one short yes/no question first; if they "
                                                       "agree, call it again."))

    for hook in BEFORE_HOOKS:
        try:
            clean = hook(cap, clean) or clean
        except Exception as e:
            log.warning("before-hook failed for %s: %s", name, e)
    subject = subject_of(cap, clean)
    app_before = env.active_app
    before = _observe(cap, clean) if cap.changes_state else None
    try:  # 5. execute (the existing implementation)
        out = str(cap.execute(clean))
    except Exception as e:
        log.error("tool %s failed: %s", name, e)
        out = f"FAILED: {name} hit an error ({e.__class__.__name__}: {str(e)[:120]})."
    out = out if _prefix(out) else f"OK: {out}"
    result = _result(name, clean, out, subject=subject, kind=name)
    if result.success and cap.changes_state:  # 6. verify independently
        result.state_before, result.state_after = before, _observe(cap, clean, before)
        result.verified = True
        if cap.verify and before is not None and result.state_after is not None:
            try:
                result.verified = bool(cap.verify(clean, before, result.state_after))
            except Exception as e:
                log.debug("verification of %s failed to run: %s", name, e)
            if not result.verified:
                log.warning("%s said OK but the state didn't change: %r -> %r", name, before, result.state_after)
                result = _result(name, clean, f"FAILED: {name} reported success, but checking afterwards it didn't "
                                              "actually happen.", subject=subject, kind=name,
                                 state_before=before, state_after=result.state_after)
    elif result.success:
        result.verified = True
    if result.success and rt.pending and rt.pending["tool"] == name:
        rt.pending = None  # the unfinished request is done
    if result.success and cap.undo and result.state_before is not None and result.state_after is not None and (
            cap.undo_if is None or cap.undo_if(result.state_before, result.state_after)):  # 7. context, undo, events
        b, a = result.state_before, result.state_after
        result.undo_fn = lambda: cap.undo(clean, b, a)
        result.flip = (cap, clean, b, a) if cap.undo_is_symmetric else None
        result.undo_hint = f"{name}" + (f" on {subject}" if subject else "")
    if env.active_app != app_before and app_before:
        env.previous_app = app_before
    _update_context(cap, result)
    env.record(result)
    if result.success and cap.event:
        events.emit(cap.event, capability=name, app=subject, args=clean, before=result.state_before,
                    after=result.state_after)
    for hook in AFTER_HOOKS:
        try:
            extra = hook(cap, clean, result)
        except Exception as e:
            log.warning("after-hook failed for %s: %s", name, e)
            extra = None
        if extra:
            result.message = f"{result.message.rstrip()} {extra}"
    return _finish(name, result)


def _confirmed(name, args=None, cap=None):
    """A yes counts only if: it was asked on an earlier turn, about this exact action, and the user's own words now say
    yes (never because the model thinks it's probably wanted)."""
    p = rt.pending
    text = rt.turn_text or ""
    if not (p and p.get("confirm") and p["tool"] == name and p["turn"] < rt.turn_no):
        return False
    if not AFFIRM.search(text) or NEGATE.search(text):
        return False
    if args is not None:
        keys = (cap.confirm_keys if cap and cap.confirm_keys else [k for k in p.get("args", {}) if k != "confidence"])
        if any(str(p.get("args", {}).get(k)) != str(args.get(k)) for k in keys):
            return False  # a different action than the one they said yes to
    return True


def _update_context(cap, result):
    after = result.state_after if isinstance(result.state_after, dict) else {}
    if result.success and cap.group in ("window", "apps") and after.get("monitor"):
        env.active_monitor = after["monitor"]
    if result.success and after.get("hwnd"):
        env.active_window = {"app": result.subject, "hwnd": after["hwnd"], "title": after.get("title", "")}
    if result.success and cap.group == "media" and after.get("title") is not None:
        env.current_media = {k: after.get(k) for k in ("title", "artist", "app", "status")}


# ---------------------------------------------------------------- several actions in one request
class Plan:
    """One request's actions, run in order (for one turn: it spans the model's tool rounds). A step whose subject
    ("it", or the same app) failed earlier in the plan is not run: no moving a window that never opened."""

    def __init__(self):
        self.steps = []  # ActionResults

    def run(self, name, args):
        cap = core.get(name)
        args = args if isinstance(args, dict) else {}
        blocker = self._blocked_by(cap, args)
        if blocker:
            result = ActionResult(False, name, args, f"FAILED: not done, because {blocker.capability} for "
                                  f"{blocker.subject} didn't work first.", error="an earlier step failed",
                                  error_code="dependency_failed", recoverable=True, subject=blocker.subject)
            log.info("plan: skipped %s (depends on failed %s)", name, blocker.capability)
        else:
            t0 = time.time()
            result = execute(name, args)
            rt.turn.timing["tools"] = rt.turn.timing.get("tools", 0.0) + time.time() - t0
        self.steps.append(result)
        return result

    def _blocked_by(self, cap, args):
        if not cap or not cap.subject or not self.steps:
            return None
        raw = str(cap.subject(args) or "").strip().lower()
        if raw in PRONOUNS:  # "it" in a plan means the thing the plan is working on
            last = next((s for s in reversed(self.steps) if s.subject), None)
            return last if last and not last.success else None
        name = subject_of(cap, args)
        return next((s for s in self.steps if not s.success and s.subject and name
                     and s.subject.lower() == name.lower()), None)

    def summary(self):
        done = [s for s in self.steps if s.success]
        return f"{len(done)} of {len(self.steps)} done" + "".join(f"; {s.capability} failed: {s.error}"
                                                                  for s in self.steps if not s.success)


# ---------------------------------------------------------------- undo
def undo(action=None, app=None):
    """Undo the newest undoable change (of this kind / on this app). Undoing a state change (volume, a window's place)
    can itself be undone: "undo that" again puts it back."""
    kind = (action or "").strip().lower().replace(" ", "_") or None
    subject = subject_of(core.get("focus_app"), {"app_name": app}) if app and core.get("focus_app") else None
    target = None
    for r in reversed(env.undo_stack):
        if time.time() - r.at > 1800:
            break
        if kind and kind not in r.capability and kind not in (r.kind or ""):
            continue
        if subject and r.subject and subject.lower() != r.subject.lower():
            continue
        target = r
        break
    last = env.last_successful_action
    last_cap = core.get(last.capability) if last else None
    if (target is not None and not kind and last and not last.can_undo and last.at > target.at
            and last_cap and last_cap.changes_state and last.capability != "undo_last_action"):
        target = None  # "undo that" means the LAST change, which can't be undone: never quietly undo an older one
    if target is None:
        if last and not last.can_undo and last.capability != "undo_last_action" and (
                not kind or kind in last.capability):
            return (f"FAILED: the last thing I did ({last.capability}" + (f" on {last.subject}" if last.subject else "")
                    + ") can't be undone. Say so plainly; offer the closest fix if there is one (e.g. reopen an app).")
        return "FAILED: there's nothing recent I can undo" + (f" of that kind ({action})." if kind else ".")
    out = str(target.undo_fn())
    if not out.startswith("OK"):
        return out
    env.forget_undo(target)
    if target.flip:  # (cap, args, restored_to, left_behind): undoing again goes back to what was just left behind
        cap, args, restored, left = target.flip
        env.undo_stack.append(ActionResult(
            True, "undo_last_action", {"undid": target.kind}, out, verified=True, subject=target.subject,
            kind=target.kind, undo_fn=lambda: cap.undo(args, left, restored), flip=(cap, args, left, restored),
            undo_hint=f"redo {target.kind}" + (f" on {target.subject}" if target.subject else "")))
    return out
