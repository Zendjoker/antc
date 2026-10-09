"""Deterministic supervision of a running run_task plan (actions/tasks.py calls it; no model call, nothing bypassed).

    before(t, s)   unnecessary repetition: the same action with the same arguments already COMPLETED earlier in this
                   task -> not run again (its earlier result stands)
                   unexpected spending: the task's model / API cost so far is over TASK_MAX_USD -> no further step
                   starts (the rest is cancelled, said plainly)
    after(t, s)    repeated failures: the same tool failed the same way twice -> recovery isn't tried again for it
                   stalled: a step that ran into its timeout
                   invalid progress: the tool said OK but its success check said otherwise
    finish(t)      missing outputs: a file / folder a completed step produced is gone by the end (and no later step of
                   the plan moved it) -> the task isn't reported COMPLETED
Findings are written into the task record (t["supervisor"]) and the report the model reads.
"""

import json
import os


def _key(s):
    return s["tool"] + json.dumps({k: v for k, v in (s["args"] or {}).items() if k != "confidence"}, sort_keys=True,
                                  default=str)


def note(t, text):
    t.setdefault("supervisor", [])
    if text not in t["supervisor"]:
        t["supervisor"].append(text)


def before(t, s, spent_usd=0.0):
    """-> ("run" | "skip" | "stop", why)."""
    from room_agent import config

    if not config.TASK_SUPERVISOR:
        return "run", ""
    if spent_usd > config.TASK_MAX_USD:
        note(t, f"stopped: this task has cost ${spent_usd:.2f}, over the ${config.TASK_MAX_USD:.2f} limit (TASK_MAX_USD)")
        return "stop", f"the task's cost (${spent_usd:.2f}) went over its limit"
    for e in t["steps"]:
        if e is s:
            break
        if e["state"] == "COMPLETED" and _key(e) == _key(s):
            note(t, f"step {s['n']} repeats step {e['n']} exactly: not run again")
            return "skip", f"already done in step {e['n']}"
    return "run", ""


def after(t, s, timed_out=False):
    from room_agent import config

    if not config.TASK_SUPERVISOR:
        return
    if timed_out:
        note(t, f"step {s['n']} ({s['tool']}) stalled: no answer within {s['timeout_s']:.0f}s")
    if s["state"] == "FAILED" and "success check failed" in (s.get("result") or ""):
        note(t, f"step {s['n']} ({s['tool']}): the tool said OK but the check showed otherwise")
    if s["state"] == "FAILED":
        same = [e for e in t["steps"] if e is not s and e["tool"] == s["tool"] and e["state"] == "FAILED"
                and e.get("result", "")[:40] == s.get("result", "")[:40]]
        if same:
            s["recovered_via"] = s.get("recovered_via") or "-"  # (no further alternative: it fails the same way)
            note(t, f"{s['tool']} failed the same way twice: not tried again")


def finish(t):
    """-> True if every output a completed step produced is still there (or moved on by a later step of the plan)."""
    from room_agent import config
    from room_agent.actions import tasks

    if not config.TASK_SUPERVISOR:
        return True
    moved = set()
    for s in t["steps"]:
        if s["tool"] in ("move_file", "delete_file") and s["state"] in ("COMPLETED", "UNVERIFIED"):
            moved.add(os.path.normcase(str((s["args"] or {}).get("file", ""))))
    ok = True
    for s in t["steps"]:
        chk = s.get("check") or {}
        kind = next(iter(chk), None)
        if s["state"] != "COMPLETED" or kind not in ("file_exists", "file_contains", "dir_exists"):
            continue
        target = chk[kind][0] if kind == "file_contains" else chk[kind]
        if os.path.normcase(str(target)) in moved:
            continue
        good, why = tasks.run_check(chk)
        if good is False:
            note(t, f"missing output at the end: step {s['n']} ({s['tool']}) - {why}")
            ok = False
    return ok
