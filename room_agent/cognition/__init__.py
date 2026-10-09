"""CognitiveController: a thin orchestrator over what already exists. It decides how much reasoning a turn deserves,
keeps a Goal for the requests that need one, gives the model a small, relevant context (the goal, what's known and
how fresh, what can be observed, a past experience), enforces the user's constraints and the loop limits, and closes
the goal honestly. It does NOT plan, execute, verify or check permissions itself:

    planning           the model, through its normal tool loop (llm/loop.py, llm/ollama.py), over the registry
    executing, checks  actions/executor.py (validation, permissions, verification, undo) - unchanged authority
    world state        actions/context.py env (+ beliefs with VERIFIED / BELIEVED / STALE / CONFLICTING / UNKNOWN)
    how to talk        social/ (SocialState, ResponseStrategy) - never consulted here
    preferences        learning/ (UserModel, routines)           memory        memory/

    begin_turn(text)            level (REFLEX / FAST / DELIBERATE / DEEP), Goal, interjections, constraints
    context_lines(text)         the goal block for the model (only on goal turns; nothing for REFLEX / FAST)
    check_call(cap, args, subj) the executor asks before acting: forbidden by a constraint? retried too often?
    after_call(cap, result)     the executor reports: metrics + trace (OBSERVE / ACTION / EXPECTED / OBSERVED)
    round_gate(plan, n_calls)   the model loops ask before each tool round: limits, REPLAN detection
    end_turn(said, plan, ...)   goal evaluation, experience, metrics
"""

import logging
import time

from room_agent import config
from room_agent import runtime as rt
from room_agent import trace
from room_agent.cognition import metrics
from room_agent.cognition.goal import (ACTIVE, BLOCKED, CANCELLED, FAILED, OPEN, SATISFIED, WAITING, Goal,
                                       parse_constraints)
from room_agent.cognition.levels import DEEP, DELIBERATE, FAST, REFLEX, WAITING_CUES, classify

log = logging.getLogger("room-agent")
_state = {"goal": None, "waiting": [], "recent": [], "store": None, "failures": {}, "turn": None}
WAITING_ON_USER = ("asked them something", "waiting for their answer or their yes", "waiting for them to finish saying it",
                   "they interrupted")


def store():
    if _state["store"] is None:
        from room_agent.cognition.experience import ExperienceStore

        _state["store"] = ExperienceStore(config.EXPERIENCE_DB)
    return _state["store"]


def active_goal():
    g = _state["goal"]
    return g if g is not None and g.open else None


# ---------------------------------------------------------------- start of a turn
def begin_turn(text):
    """-> {"level", "why", "reflex": (cap, args) or None, "goal"}. Cheap: regexes and the registry, no model call."""
    from room_agent.actions import pending
    from room_agent.cognition import reflex

    goal = active_goal()
    info = {"reflex": None, "goal": None}
    cancelled = False
    if goal and pending.CANCEL.match(text or ""):  # (stop / cancel / never mind: the rest of the goal isn't done)
        goal.set(CANCELLED, "they cancelled it")
        _finish_goal(goal)
        goal, cancelled = None, True
    constraints = parse_constraints(text)
    filling = pending.current() is not None and not pending.current().get("confirm")  # (an answer is expected)
    hit = None if (constraints or filling) or not config.REFLEX else reflex.match(text)
    level, why = classify(text, reflex_hit=bool(hit))
    # A goal waiting for them (BLOCKED) continues only with an answer-like message; a new self-contained request
    # ("put Spotify on the right monitor and...") starts its own goal.
    continues = bool(goal and goal.status == BLOCKED and goal.reason in WAITING_ON_USER and level == FAST and not hit)
    if continues:
        level, why = DELIBERATE, "continues the goal in progress"
    if constraints and level in (REFLEX, FAST) and goal:
        level, why = DELIBERATE, "changes the goal in progress"
    if level in (DELIBERATE, DEEP):
        new_request = goal is None or not (continues or goal.user_request == text
                                           or (constraints and len(text.split()) <= 8))
        if goal and new_request:
            goal.set(CANCELLED, "replaced by a new request")
            _finish_goal(goal)
        if new_request:
            goal = Goal(user_request=text, objective=text, level=level)
            _state["goal"] = goal
            _state["failures"] = {}
            if WAITING_CUES.search(text or ""):
                goal.trigger = text
        goal.turns += 1
        goal.constraints += [c for c in constraints if all(c.text != k.text for k in goal.constraints)]
        if config.GOAL_UNDERSTANDING:
            from room_agent.cognition import understand

            spec = understand.from_words(text)
            goal.spec = understand.merge(spec, getattr(goal, "model_fields", None))
            goal.constraints += [c for c in spec.constraints if all(c.text != k.text for k in goal.constraints)]
        goal.status = WAITING if goal.trigger else ACTIVE
        info["goal"] = goal
    elif goal and constraints:  # ("actually, don't open Chrome": the goal in progress gets the constraint)
        goal.constraints += constraints
        info["goal"] = goal
    if info["goal"] is None:
        _state["failures"] = {}  # (no goal: retries are counted per request)
    info.update(level=level, why=why, reflex=hit)
    rt.turn.level, rt.turn.goal = level, info["goal"]
    metrics.begin_turn(level, getattr(info["goal"], "id", None))
    if cancelled:
        metrics.count("cancellations")
    _state["turn"] = {"started": time.time(), "rounds": 0, "calls": 0, "failed_last_round": False}
    trace.note("LEVEL", f"{level} ({why})")
    if info["goal"]:
        trace.note("GOAL", info["goal"].objective[:120])
    if level != FAST:
        log.info("cognition: %s (%s)%s", level, why, f", goal #{info['goal'].id}" if info["goal"] else "")
    return info


# ---------------------------------------------------------------- what the model is told (goal turns only)
def _relevant_groups(text):
    from room_agent.actions import core

    out = set()
    for name, g in core.GROUPS.items():
        try:
            if g.hints is not None and g.hints.search(text or ""):
                out.add(name)
        except Exception:
            pass
    return out


def context_lines(user_text):
    goal = getattr(rt.turn, "goal", None)
    if goal is None:
        return []
    from room_agent.actions import core
    from room_agent.actions.context import STALE, VERIFIED, env
    from room_agent.memory.text import terms

    lines = ["- " + goal.describe() + " (tracked by code)"]
    words = terms(goal.user_request + " " + (user_text or ""))
    groups = _relevant_groups(goal.user_request + " " + (user_text or ""))
    sources = {c.name for c in core.capabilities() if c.group in groups}
    known = env.relevant_beliefs(words, limit=6, sources=sources)
    fresh = [b.describe() for b in known if b.status() == VERIFIED]
    unsure = [b.describe() for b in known if b.status() != VERIFIED]
    if fresh:
        lines.append("- known now (verified, no need to check again): " + "; ".join(fresh))
    if unsure:
        lines.append("- possibly outdated or disputed (check before relying on it): " + "; ".join(unsure))
    offered = {t["name"] for t in core.offered()}
    reads = [c.name for c in core.capabilities() if not c.changes_state and c.name in offered
             and (c.group in groups or not groups) and c.name != "update_goal"][:8]
    if reads:
        lines.append("- read-only checks available for this (they change nothing): " + ", ".join(reads))
    if goal.level in (DELIBERATE, DEEP):
        try:
            past = store().similar(goal.user_request, k=2)
        except Exception:
            past = []
        if past:
            lines.append("- past experience (a hint, not proof: what was true then may not be now, so check it first): "
                         + " | ".join(store().hint(r) for r in past))
        routines = _routines()
        if routines:
            lines.append("- routines they taught you (use one only if it fits this goal): " + "; ".join(routines))
    if goal.trigger:
        lines.append("- this asks you to watch for something later. There's no background watching yet unless a tool "
                     "above does exactly that: say so honestly and offer the closest thing (like a reminder).")
    lines.append("- how to work on a goal: anything you need that isn't known, check with the smallest useful read-only "
                 "tool first; ask them only if nothing can tell you; never guess. Then do only what's needed, one "
                 "action per thing, check it worked, and stop as soon as the goal is met. If something fails, use what "
                 "you learned (don't repeat the same thing), and say plainly what didn't work. update_goal records the "
                 "desired state, and that it's satisfied, blocked or failed.")
    return lines


def _routines():
    try:
        from room_agent import learning
        from room_agent.learning import model as um

        m = learning.user_model()
        return [m.say(p) for p in m.all() if p["key"].startswith("routine:")][:3]
    except Exception:
        return []


# ---------------------------------------------------------------- executor and loop hooks
def _call_key(name, args):
    return name + repr(sorted((k, str(v)) for k, v in (args or {}).items() if k != "confidence"))


def check_call(cap, args, subject=None):
    """Before an action runs: refused by a constraint, or retried too often? -> refusal text, or None."""
    goal = getattr(rt.turn, "goal", None) or active_goal()
    rules = list(goal.constraints) if goal else []
    if config.GOAL_UNDERSTANDING and cap.changes_state:
        rules += [c for c in words_constraints(rt.turn_text) if all(c.text != k.text for k in rules)]
    if cap.changes_state:
        for c in rules:
            if c.blocks(cap.name, args, subject):
                trace.note("BLOCKED", f"{cap.name} ({c.text})")
                return f"FAILED: not done: they said \"{c.text}\". Leave that out and carry on with the rest."
    if (_state["turn"] is not None and cap.changes_state
            and _state["failures"].get(_call_key(cap.name, args), 0) >= config.COG_MAX_RETRIES):
        metrics.count("refused_retries")
        return (f"FAILED: not run again: {cap.name} with these arguments already failed {config.COG_MAX_RETRIES} times. "
                "Try something different, or tell them plainly it isn't working.")
    return None


_words_cache = {"text": None, "rules": []}


def words_constraints(text):
    """Explicit limits in these words (cognition/understand.py), cached per text: regexes, no model call."""
    if text != _words_cache["text"]:
        from room_agent.cognition import understand

        _words_cache.update(text=text, rules=understand.from_words(text or "").constraints if text else [])
    return _words_cache["rules"]


def after_call(cap, args, result):
    """The executor's report on one call: metrics and the operational trace."""
    if cap is None:
        return
    if result.kind == "already":
        metrics.count("already_satisfied")
    elif result.kind == "cached":
        metrics.count("reused_observations")
    elif cap.changes_state:
        metrics.count("actions")
    else:
        metrics.count("observations")
    if not result.success:
        metrics.count("tool_failures")
        if cap.changes_state and _state["turn"] is not None:  # (retries count within one request or goal)
            key = _call_key(cap.name, args)
            _state["failures"][key] = _state["failures"].get(key, 0) + 1
        if result.error_code == "not_as_expected":
            metrics.count("verification_failures")
    if _state["turn"] is not None and not result.success:
        _state["turn"]["failed_last_round"] = True
    if result.expected is not None:
        trace.note("EXPECTED", result.expected)
        trace.note("OBSERVED", result.observed)
    trace.note("OBSERVE" if not cap.changes_state else "STEP", f"{cap.name} -> {result.message.split(':', 1)[0]}")


def round_gate(plan, n_calls):
    """Before the model's next round of tool calls. -> refusal text (stop: limits reached), or None (go on)."""
    t = _state["turn"]
    if t is None:
        return None
    t["rounds"] += 1
    t["calls"] += n_calls
    if plan is not None:
        plan.round = t["rounds"]
    metrics.count("rounds")
    if t["failed_last_round"] and t["rounds"] > 1:
        metrics.count("replans")
        trace.note("REPLAN", f"round {t['rounds']} after a failure")
    t["failed_last_round"] = False
    if (t["rounds"] > config.COG_MAX_TOOL_ROUNDS or t["calls"] > config.COG_MAX_TOOL_CALLS
            or time.time() - t["started"] > config.COG_MAX_TURN_S):
        trace.note("LIMIT", f"rounds {t['rounds']}, calls {t['calls']}")
        log.warning("cognition: limit reached (%d rounds, %d calls, %.0fs): stopping the tool loop", t["rounds"],
                    t["calls"], time.time() - t["started"])
        return ("FAILED: not run: the step limit for one request was reached. Stop using tools now and tell them "
                "honestly what was done and what wasn't.")
    return None


def reflex_fell_back():
    """A reflex didn't come back OK: the model takes over, so this turn is a FAST one (the attempt is counted)."""
    rt.turn.level = FAST
    m = metrics.current()
    if m is not None:
        m["level"] = FAST
        m["reflex_fallbacks"] = m.get("reflex_fallbacks", 0) + 1
    trace.note("LEVEL", "FAST (the reflex didn't work out; the model takes over)")


def limit_reached():
    t = _state["turn"]
    return bool(t and (t["rounds"] > config.COG_MAX_TOOL_ROUNDS + 1))


# ---------------------------------------------------------------- update_goal (the model's structured account)
def update_goal(args):
    goal = getattr(rt.turn, "goal", None) or active_goal()
    if goal is None:
        return "FAILED: there's no goal in progress."
    for key in ("desired_state", "success_conditions"):
        value = args.get(key)
        if isinstance(value, str):
            value = [value]
        if value:
            setattr(goal, key, [str(v)[:120] for v in value][:6])
    if args.get("summary"):
        goal.summary = str(args["summary"])[:200]
    fields = {k: args.get(k) for k in ("capabilities", "constraints", "permissions", "deliverables", "missing",
                                       "desired_state", "success_conditions", "summary") if args.get(k)}
    note = ""
    if fields and config.GOAL_UNDERSTANDING:
        from room_agent.cognition import understand

        goal.model_fields = {**(getattr(goal, "model_fields", None) or {}), **fields}
        spec = understand.second_pass(understand.merge(understand.from_words(goal.user_request), goal.model_fields))
        goal.spec = spec
        goal.constraints += [c for c in spec.constraints if all(c.text != k.text for k in goal.constraints)]
        if spec.conflicts:
            note = " CONFLICT with their words: " + "; ".join(spec.conflicts) + " (ask them)."
        if spec.missing:
            note += " Still missing (ask, don't guess): " + ", ".join(spec.missing) + "."
    status = str(args.get("status") or "").upper()
    if status in (SATISFIED, BLOCKED, FAILED):
        goal.declared = (status, str(args.get("reason") or "")[:160])
    return "OK: goal noted." + (" (Code double-checks that it's really done.)" if status == SATISFIED else "") + note


# ---------------------------------------------------------------- end of a turn
def _unrecovered(steps):
    """Failed actions that nothing later in the plan made good (same capability and subject succeeded)."""
    from room_agent.actions import core

    out = []
    for i, s in enumerate(steps):
        cap = core.get(s.capability)
        if s.success or s.capability == "update_goal" or (cap and not cap.changes_state):
            continue
        if s.error_code in ("needs_input", "needs_confirmation", "constraint"):
            continue  # (waiting for them, or left out because they said so: not a failure)
        group = getattr(cap, "group", None)
        if not any(t.success and t.subject == s.subject and (t.capability == s.capability or (
                group and getattr(core.get(t.capability), "group", None) == group)) for t in steps[i + 1:]):
            out.append(s)
    return out


def end_turn(said, plan=None, interrupted=False, waiting=False):
    """Evaluate the goal (code decides, the model's declaration is checked), then metrics and experience."""
    goal = getattr(rt.turn, "goal", None)
    steps = list(plan.steps) if plan else []
    if goal is not None and goal.status != CANCELLED:
        declared = getattr(goal, "declared", None)
        failed = _unrecovered(steps)
        asked_now = rt.pending is not None and rt.pending["turn"] == rt.turn_no  # (a question from THIS turn's steps)
        waiting_on_user = asked_now or any(s.error_code in ("needs_input", "needs_confirmation") for s in steps)
        if interrupted:
            goal.set(BLOCKED, "they interrupted")
        elif waiting:
            goal.set(BLOCKED, "waiting for them to finish saying it")
        elif waiting_on_user:
            goal.set(BLOCKED, "waiting for their answer or their yes")
        elif failed:
            goal.set(FAILED, f"{failed[-1].capability} didn't work: {failed[-1].error or ''}"[:160])
        elif declared and declared[0] in (BLOCKED, FAILED):
            goal.set(declared[0], declared[1])
        elif goal.trigger:
            goal.set(BLOCKED, "nothing can watch for this yet")
        elif str(said or "").rstrip().endswith("?") and not (declared and declared[0] == SATISFIED):
            goal.set(BLOCKED, "asked them something")
        else:
            goal.set(SATISFIED, (declared[1] if declared else "") or "done")
        trace.note("STATUS", f"{goal.status}" + (f" ({goal.reason})" if goal.reason and goal.status != SATISFIED else ""))
        if not goal.open:
            _finish_goal(goal, steps)
        elif goal.trigger and goal.reason == "nothing can watch for this yet":
            # (kept for a future event engine, but it isn't the goal in progress: the next request starts fresh)
            _state["waiting"] = (_state["waiting"] + [goal])[-10:]
            _finish_goal(goal, steps)
        else:
            goal._steps = getattr(goal, "_steps", []) + steps
    m = metrics.end_turn()
    if m is not None:
        from room_agent.speech import timing

        m["speech"] = timing.summary()  # (user stop -> first sound, by stage)
        first = rt.turn.timing.get("first_sound") or rt.turn.timing.get("first_words")
        m["first_response_s"] = round(first, 3) if first is not None else None
        try:
            store().log_turn(m)
        except Exception as e:
            log.debug("metrics not saved: %s", e)
    _state["turn"] = None
    return m


def _finish_goal(goal, steps=()):
    """A goal is over (satisfied, failed, cancelled, blocked for good): record the experience for real goals."""
    from room_agent.actions import core

    if _state["goal"] is goal:
        _state["goal"] = None
    _state["recent"] = (_state["recent"] + [goal])[-10:]
    all_steps = getattr(goal, "_steps", []) + list(steps)
    if goal.level in (DELIBERATE, DEEP) and (all_steps or goal.status in (FAILED, CANCELLED)):
        try:
            m = metrics.current() or {}
            store().record(goal, all_steps, lambda name: getattr(core.get(name), "private", False),
                           time.time() - goal.created_at, corrections=m.get("corrections", 0), replans=m.get("replans", 0))
        except Exception as e:
            log.warning("experience not saved: %s", e)
    log.info("cognition: goal #%d %s%s", goal.id, goal.status, f" ({goal.reason})" if goal.reason else "")


def reset():
    _state.update(goal=None, waiting=[], recent=[], failures={}, turn=None)


def _register():
    from room_agent.actions import core
    from room_agent.actions.core import Capability, register

    core.register_context(context_lines, order=27)
    register(Capability(
        "update_goal", "Record what the current goal needs to end up true (desired_state, success_conditions, "
        "deliverables), what it needs (capabilities, permissions), their explicit limits (constraints) and what's missing, and "
        "its status once you know it: satisfied (verified done), blocked (needs them or something unavailable) or "
        "failed. Only while a goal is in progress; changes nothing in the world.",
        {"type": "object", "properties": {
            "desired_state": {"type": "array", "items": {"type": "string"}},
            "success_conditions": {"type": "array", "items": {"type": "string"}},
            "capabilities": {"type": "array", "items": {"type": "string", "enum": [
                "research", "browser", "desktop", "files", "business", "coding", "email", "calendar", "lists", "info"]}},
            "constraints": {"type": "array", "items": {"type": "string"}, "description": "their explicit limits, quoted"},
            "permissions": {"type": "array", "items": {"type": "string", "enum": [
                "send_email", "delete", "move", "paid", "code_change", "calendar_write"]},
                "description": "what it needs their yes / money for (never assumed granted)"},
            "deliverables": {"type": "array", "items": {"type": "string"}},
            "missing": {"type": "array", "items": {"type": "string"}, "description": "what must be asked first"},
            "status": {"type": "string", "enum": ["satisfied", "blocked", "failed"]},
            "reason": {"type": "string"}, "summary": {"type": "string"}}},
        update_goal, group="goal", changes_state=False, available=lambda: getattr(rt.turn, "goal", None) is not None))


_register()
