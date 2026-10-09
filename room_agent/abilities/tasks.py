"""Multi-step tasks: run a plan of steps with dependencies, resume / cancel / check it (implementation: actions/tasks.py)."""

import json
import re

from room_agent.abilities._kit import NO_ARGS, params, tool
from room_agent.actions.core import Group, Risk, register_context, register_group

TASK_HINTS = re.compile(r"\band then\b|\bthen\b|after that|\bfirst\b|\bsteps?\b|\btask\b|\bresume\b|continue|carry on|"
                        r"pick up where|each of|all of them|\bfinish\b", re.I)
register_group(Group("tasks", TASK_HINTS, lambda: _live(), "multi-step tasks",
                     "runs several steps with dependencies, pauses for a yes, resumes after a restart", rules=[
    "- For a request with 3 or more actions, or where a later action needs an earlier one to have worked (open X, then "
    "move it, then...): call run_task ONCE with all the steps instead of calling the tools one by one. Give each step "
    "the tool, its args, depends_on (earlier step numbers it needs) and success (what counts as done).",
    "- Report a task exactly as its result says: completed, partly done (which steps), unknown, or waiting for their yes."]))


def _live():
    from room_agent.actions import tasks

    t = tasks.get()
    return bool(t and t["state"] in ("WAITING", "INTERRUPTED", "PARTIAL"))


STEP = {"type": "object", "properties": {
    "tool": {"type": "string", "description": "A tool name you can call in this conversation"},
    "args": {"type": "object", "description": "Its arguments"},
    "depends_on": {"type": "array", "items": {"type": "integer"}, "description": "Earlier step numbers this one needs"},
    "success": {"type": "string", "description": "What counts as done, in a few words"},
    "check": {"type": "object", "description": "Optional independent check run after the step, one of: "
              "{\"file_exists\": path}, {\"file_contains\": [path, text]}, {\"url_contains\": text}, "
              "{\"page_contains\": text}, {\"list_contains\": [list, item]}"}},
    "required": ["tool", "args"]}


def _run(args):
    from room_agent.actions import core, tasks

    steps = args.get("steps") or []
    if not isinstance(steps, list) or not steps:
        return "NEEDS: the steps (at least one)."
    if len(steps) > tasks.MAX_STEPS:
        return f"FAILED: at most {tasks.MAX_STEPS} steps in one task; split it."
    offered = {t["name"] for t in core.offered()}
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict) or not s.get("tool"):
            return f"FAILED: step {i} has no tool."
        if s["tool"] in ("run_task", "resume_task", "cancel_task"):
            return f"FAILED: step {i}: a task can't start or resume another task."
        if s["tool"] not in offered:
            return f"FAILED: step {i}: '{s['tool']}' isn't a tool you can use here. Nothing was run."
        if any(not isinstance(d, int) or not 1 <= d < i for d in s.get("depends_on") or []):
            return f"FAILED: step {i}: depends_on must list earlier step numbers. Nothing was run."
    review = None
    from room_agent import config

    if config.TASK_CONTRACTS:  # (actions/planning.py: contracts, validation, inferred order, default checks)
        from room_agent import runtime as rt
        from room_agent.actions import planning

        spec = None
        if config.GOAL_UNDERSTANDING:
            from room_agent.cognition import understand

            spec = understand.from_words(planning.words_for_plan())
            spec.missing = understand.from_words(rt.turn_text or "").missing  # (only what THIS request leaves open)
        review = planning.review(steps, spec, offered)
        if review.rejected:
            return ("FAILED: the plan isn't valid, so nothing was run: " + "; ".join(review.rejected[:4])
                    + ". Make a valid plan with tools you have.")
        if review.questions:
            return "NEEDS: " + "; ".join(review.questions[:3]) + ". Nothing was run: ask them first."
        steps = review.steps
    t = tasks.new(args.get("goal") or "", steps)
    if review is not None:
        t["review"] = {"left_out": [{"step": i, "tool": tool, "why": why} for i, tool, why in review.left_out],
                       "notes": review.notes[:6], "contracts": [planning.contract_line(c) for c in review.contracts if c]}
        for note in review.notes[:3]:
            tasks.event(t, note)
    return tasks.report(tasks.run(t))


def _resume(args):
    from room_agent.actions import tasks

    t = tasks.get(args.get("task_id"))
    if t is None:
        return "FAILED: there's no task to resume."
    if t["state"] not in ("INTERRUPTED", "WAITING", "PARTIAL", "CANCELED"):
        return f"OK: nothing to resume: task {t['id']} is {t['state']}."
    return tasks.report(tasks.resume(t))


def _cancel(args):
    from room_agent.actions import tasks

    t = tasks.get(args.get("task_id"))
    if t is None:
        return "FAILED: there's no task to cancel."
    return tasks.report(tasks.cancel_task(t))


def _status(args):
    from room_agent.actions import tasks

    t = tasks.get(args.get("task_id"))
    return "OK: no multi-step task yet." if t is None else tasks.report(t).replace("NEEDS_CONFIRMATION:", "OK:", 1)


tool("run_task", "Run several actions as one task: steps in order, each with its tool and args, depends_on (earlier steps "
     "it needs) and success. Code runs them, checks each, skips steps whose dependency failed, pauses for a yes, and "
     "reports every step's outcome.",
     params({"goal": {"type": "string", "description": "The task in a few words"},
             "steps": {"type": "array", "items": STEP, "maxItems": 10}}, ["goal", "steps"]),
     _run, group="tasks", verification="internal", verified_by="each step is checked by its own tool / the executor")
tool("resume_task", "Continue the last task that was interrupted, paused for a yes, or partly done (only steps that never "
     "ran; anything sensitive asks again).", params({"task_id": {"type": "string"}}), _resume, group="tasks",
     verification="internal", verified_by="each step is checked by its own tool / the executor")
tool("cancel_task", "Cancel the remaining steps of the last task.", params({"task_id": {"type": "string"}}), _cancel,
     group="tasks", verification="internal", verified_by="the task record shows the steps cancelled")
tool("task_status", "What happened in the last multi-step task, step by step.", params({"task_id": {"type": "string"}}),
     _status, group="tasks", changes_state=False)


def _knowledge(args):
    from room_agent.cognition import experience_v2 as xp

    procs, pats = xp.listing()
    if not procs and not pats:
        return "OK: no task knowledge is stored yet."
    lines = [f"#{p['id']} {' -> '.join(json.loads(p['tools']))} for \"{p['goal'][:60]}\": worked {p['successes']}x, "
             f"failed {p['failures']}x ({p['source']})" + (" [disabled: fails more than it works]"
                                                           if p["failures"] >= p["successes"] else "") for p in procs[:8]]
    lines += [f"when {x['tool']} fails ({x['kind']}): {x['alternative']} worked {x['successes']}x, failed {x['failures']}x"
              for x in pats[:6]]
    return "OK: what Jarvis learned from finished tasks (tool names and outcomes only, no content):\n" + "\n".join(lines)


def _forget_knowledge(args):
    from room_agent.cognition import experience_v2 as xp

    which = str(args.get("which") or "all")
    n = xp.forget(which)
    return f"OK: forgot {n} entr{'y' if n == 1 else 'ies'} of task knowledge ({which})."


tool("list_task_knowledge", "What Jarvis learned from finished multi-step tasks: verified procedures and which recovery "
     "worked for which failure (tool names and outcomes only).", params({}), _knowledge, group="tasks",
     changes_state=False, examples=["what have you learned about doing tasks"])
tool("forget_task_knowledge", "Forget learned task knowledge: one procedure by its number, 'patterns', or 'all'.",
     params({"which": {"type": "string"}}), _forget_knowledge, group="tasks", risk=Risk.CONFIRM,
     verification="internal", verified_by="the entries are deleted from experience.db")


def _context(user_text):
    from room_agent import config
    from room_agent.actions import tasks

    lines = tasks.context_lines(user_text)
    if config.TASK_EXPERIENCE and user_text and len(user_text.split()) >= 5:
        try:
            from room_agent.cognition import experience_v2 as xp

            lines += ["- experience: " + xp.hint(r) for r in xp.relevant(user_text)]
        except Exception:  # noqa: BLE001
            pass
    return lines


register_context(_context, order=14)
_ = NO_ARGS
