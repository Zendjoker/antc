"""Contracts and validation for run_task plans (actions/tasks.py runs them; nothing here executes a tool).

Every step gets a contract from its tool family (built from the capability registry, plus what code knows per tool):
    goal / capability / inputs (its required parameters) / expected output / verification (a check run by code after
    the tool says OK) / cost estimate / risk / retry policy / required permission / resource it uses

review(steps, words) - BEFORE anything runs:
    rejected   a tool that doesn't exist or isn't available, a dependency that isn't an earlier step, a cycle,
               a state-changing step whose outcome nothing could verify            -> nothing runs (FAILED)
    questions  information the words leave open ("send it to him"), a required input the plan doesn't give, a file
               step that would act on "whatever is selected" inside a multi-step plan   -> nothing runs (NEEDS)
    left out   a step their words rule out ("don't send it", "don't touch the text files") or that needs a permission
               their words never implied (deleting while asked to summarize): not run, reported, no question needed
    reordered  a step that needs what a later step creates (saving into a folder made afterwards) is moved after it,
               with the dependency recorded
    checks     each step without its own success check gets its family's default check (file on disk, folder exists,
               research has sources, citations point at real sources, draft addressed to a valid recipient...)
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PERMISSION = {"gmail_send": "send_email", "delete_file": "delete", "clear_list": "delete", "calendar_delete_event": "delete",
              "move_file": "move", "calendar_create_event": "calendar_write", "calendar_update_event": "calendar_write",
              "apply_code_fix": "code_change", "fix_code": "code_change"}
FAMILY = {"research": "research", "web": "research", "browser": "browser", "files": "files", "apps": "desktop",
          "window": "desktop", "media": "desktop", "screen": "desktop", "pc": "desktop", "gmail": "email",
          "calendar": "calendar", "lists": "lists", "missions": "business", "coding": "coding", "weather": "info",
          "time": "info", "zigbee": "home", "home": "home"}
COST = {"start_business_mission": "within the mission budget", "fix_code": "paid fixer, up to CODING_MAX_USD"}
STANDARD = ("desktop", "documents", "docs", "downloads", "pictures", "music", "videos")
FILE_TARGET = ("move_file", "delete_file", "open_file", "read_file")


@dataclass
class Contract:
    tool: str
    family: str
    inputs: list
    expected: str
    verification: str
    cost: str
    risk: str
    retry: str
    permission: str
    resource: str
    check: dict = None


@dataclass
class Review:
    steps: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    left_out: list = field(default_factory=list)    # [(original step number, why)]
    notes: list = field(default_factory=list)
    contracts: list = field(default_factory=list)


def _folder(name):
    from room_agent.computer import filewrite

    return filewrite.folder(name or "desktop")


def _file_path(args):
    p = str(args.get("file") or "")
    if not p:
        return None
    q = Path(os.path.expanduser(p))
    return q if q.is_absolute() else None


def _produces(tool, a):
    from room_agent.computer import filewrite

    try:
        if tool == "make_folder":
            return [("dir", str(_folder(a.get("folder", "documents")) / a.get("name", "")).lower())]
        if tool == "save_file":
            return [("file", str(_folder(a.get("folder", "desktop")) / filewrite._safe_name(a.get("name", ""))).lower())]
        if tool == "move_file" and _file_path(a):
            dest = _folder(a.get("to_folder")) if a.get("to_folder") else _file_path(a).parent
            return [("file", str(dest / (a.get("new_name") or _file_path(a).name)).lower())]
    except Exception:  # noqa: BLE001
        pass
    return []


def _needs(tool, a):
    out = []
    try:
        if tool == "save_file" and str(a.get("folder", "desktop")).lower() not in STANDARD:
            out.append(("dir", str(_folder(a.get("folder"))).lower()))
        if tool == "move_file" and a.get("to_folder") and str(a["to_folder"]).lower() not in STANDARD:
            out.append(("dir", str(_folder(a["to_folder"])).lower()))
        if tool in FILE_TARGET and _file_path(a):
            out.append(("file", str(_file_path(a)).lower()))
    except Exception:  # noqa: BLE001
        pass
    return out


def contract(tool, args):
    from room_agent.actions import core

    cap = core.get(tool)
    if cap is None:
        return None
    required = list((cap.parameters or {}).get("required") or [])
    family = FAMILY.get(cap.group or "", cap.group or "other")
    retry = ("reads: retried on temporary errors" if not cap.changes_state else
             "changes: retried only when the read-back proves it didn't happen; an unknown outcome is never retried")
    return Contract(tool, family, required, _expected(tool, args), cap.verification or "none", COST.get(tool, "free"),
                    cap.risk.name.lower(), retry, PERMISSION.get(tool, ""), _resource(cap, args),
                    default_check(tool, args))


def _expected(tool, a):
    return {"save_file": "the file exists with that content", "make_folder": "the folder exists",
            "move_file": "the file is in the new place, not the old one", "research_web": "passages from sources read",
            "save_research_report": "a report whose citations point at the sources read",
            "gmail_create_draft": "a draft (not sent) to a recipient they named", "gmail_send": "the email is in Sent",
            "open_url": "the page shown in the browser", "run_tests": "the test results", "fix_code":
            "a fix that makes the tests pass in a sandbox copy", "apply_code_fix": "the fix applied, tests passing"}.get(
        tool, "what the tool reports, checked by the executor")


def _resource(cap, a):
    if cap.group in ("apps", "window", "media", "screen", "pc"):
        return "desktop"
    if cap.group == "browser":
        return "browser"
    if cap.group == "files" and cap.changes_state:
        return "files"
    return "none" if not cap.changes_state else (cap.group or "other")


def default_check(tool, a):
    """The family's check of a step's outcome (run by actions/tasks.run_check after the tool said OK)."""
    from room_agent.computer import filewrite

    try:
        if tool == "save_file":
            return {"file_contains": [str(_folder(a.get("folder", "desktop")) / filewrite._safe_name(a.get("name", ""))),
                                      str(a.get("content", ""))[:60]]}
        if tool == "make_folder":
            return {"dir_exists": str(_folder(a.get("folder", "documents")) / a.get("name", ""))}
        if tool == "move_file" and _produces(tool, a):
            return {"file_exists": _produces(tool, a)[0][1]}
        if tool == "research_web":
            return {"research_sources": 1}
        if tool == "save_research_report":
            return {"citations_valid": str(a.get("summary", ""))}
        if tool == "gmail_create_draft":
            return {"draft_recipient": str(a.get("to", ""))}
        if tool == "add_to_list" and a.get("item"):
            return {"list_contains": [a.get("list") or "todo", a["item"]]}
    except Exception:  # noqa: BLE001
        return None
    return None


def review(steps, words_spec, offered):
    """-> Review. steps: the model's plan ({tool, args, depends_on, success, check}); words_spec: understand.Spec."""
    from room_agent.actions import core

    r = Review()
    n = len(steps)
    for i, s in enumerate(steps, 1):
        tool = s.get("tool") if isinstance(s, dict) else None
        if not tool:
            r.rejected.append(f"step {i} has no tool")
            continue
        if tool not in offered:
            r.rejected.append(f"step {i}: '{tool}' isn't a tool that exists / is available here")
        for d in s.get("depends_on") or []:
            if not isinstance(d, int) or not 1 <= d <= n or d == i:
                r.rejected.append(f"step {i}: depends_on {d} isn't another step of this plan")
    if r.rejected:
        return r
    # what the words leave open must be asked before anything changes
    changing = [s for s in steps if (core.get(s["tool"]) and core.get(s["tool"]).changes_state)]
    if words_spec is not None and words_spec.missing and changing:
        r.questions.append("their request leaves this open: " + ", ".join(words_spec.missing)
                           + " - ask them before doing anything")
    for i, s in enumerate(steps, 1):
        cap = core.get(s["tool"])
        args = s.get("args") or {}
        req = [k for k in (cap.parameters or {}).get("required") or [] if args.get(k) in (None, "")]
        if req:
            r.questions.append(f"step {i} ({s['tool']}) is missing {', '.join(req)}")
        if s["tool"] in ("move_file", "delete_file") and len(steps) > 1 and not (args.get("file") or args.get("number")):
            r.questions.append(f"step {i} ({s['tool']}) doesn't say which file (inside a plan it can't act on 'whatever "
                               "is selected')")
        if cap.changes_state and cap.verification == "none" and not s.get("check") and default_check(s["tool"], args) is None:
            r.rejected.append(f"step {i} ({s['tool']}): nothing could verify its outcome; use a tool whose result can be "
                              "checked or add a check")
    if r.rejected or r.questions:
        return r
    # their words rule some steps out, or never asked for what they need permission for
    drop = {}
    if words_spec is not None:
        from room_agent.cognition import understand

        for i, why in understand.plan_conflicts(words_spec, steps):
            drop[i] = f"they said \"{why}\""
        for i, s in enumerate(steps, 1):
            perm = PERMISSION.get(s["tool"])
            if i not in drop and perm and perm not in words_spec.permissions:
                drop[i] = f"their request didn't ask for that ({perm.replace('_', ' ')} needs them to ask)"
    for i in sorted(drop):
        r.left_out.append((i, steps[i - 1]["tool"], drop[i]))
    # inferred dependencies (a step needs what another one creates) + a stable topological order
    deps = {i: set(d for d in (s.get("depends_on") or [])) for i, s in enumerate(steps, 1)}
    made = {}
    for i, s in enumerate(steps, 1):
        for res in _produces(s["tool"], s.get("args") or {}):
            made.setdefault(res, i)
    for i, s in enumerate(steps, 1):
        for res in _needs(s["tool"], s.get("args") or {}):
            j = made.get(res)
            if j and j != i and j not in deps[i]:
                deps[i].add(j)
                r.notes.append(f"step {i} ({s['tool']}) needs what step {j} ({steps[j - 1]['tool']}) creates: it waits "
                               "for it")
    order, done, visiting = [], set(), set()

    def visit(i):
        if i in done:
            return True
        if i in visiting:
            return False
        visiting.add(i)
        ok = all(visit(d) for d in sorted(deps[i]))
        visiting.discard(i)
        done.add(i)
        order.append(i)
        return ok

    if not all(visit(i) for i in range(1, n + 1)):
        r.rejected.append("the steps' dependencies form a cycle")
        return r
    if order != list(range(1, n + 1)):
        r.notes.append("reordered so every step runs after what it needs: " + ", ".join(str(i) for i in order))
    new_no = {old: k for k, old in enumerate(order, 1)}
    for old in order:
        s = dict(steps[old - 1])
        s["depends_on"] = sorted(new_no[d] for d in deps[old])
        s["_orig"] = old
        if old in drop:
            s["_skip"] = drop[old]
        if not s.get("check"):
            chk = default_check(s["tool"], s.get("args") or {})
            if chk:
                s["check"] = chk
        r.steps.append(s)
        r.contracts.append(contract(s["tool"], s.get("args") or {}))
    return r


def words_for_plan():
    """The user's own words behind a plan: this turn, the goal in progress, their last few messages."""
    from room_agent import runtime as rt
    from room_agent.actions import pending

    texts = list(pending._recent_user_text())
    goal = getattr(rt.turn, "goal", None)
    if goal is not None and goal.user_request not in texts:
        texts.append(goal.user_request)
    return " . ".join(dict.fromkeys(t for t in texts if t))


def contract_line(c):
    return (f"{c.tool}: {c.family}; expects {c.expected}; verified by {c.verification}"
            + (f" + check" if c.check else "") + f"; risk {c.risk}; cost {c.cost}"
            + (f"; needs permission: {c.permission}" if c.permission else ""))
