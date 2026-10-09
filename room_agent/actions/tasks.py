"""Tasks: a request's steps with dependencies, run by code, checkpointed to disk, resumable after a restart.

A task is either
    a plan        the model calls run_task once with explicit steps ({tool, args, depends_on, success}); code runs them
    a turn        an ordinary request whose tools the model called one by one: recorded the same way afterwards
so every request that did something has one traceable record (TASKS_FILE, the last MAX_TASKS): intent, plan, each step's
state, tool result, verification evidence, retries, failures, outcome, time and model cost.

Running a plan (each step through executor.execute: every permission, confirmation and verification rule still applies):
    - in order; a step whose dependency didn't complete is BLOCKED (never run on a broken foundation); independent steps
      still run (partial recovery)
    - a step has a timeout; a state-changing step that times out is UNKNOWN (it may have happened) and is never retried
    - retries (at most MAX_RETRIES, with backoff) only where it's safe: a read that hit a temporary error, or a change the
      executor proved did NOT happen (its read-back showed no change)
    - a step that needs a yes pauses the task (WAITING); "resume" continues after the yes
    - the emergency stop / an interruption: no further step starts (they're CANCELED)
    - checkpointed before and after every step: after a crash, a step that was RUNNING becomes UNKNOWN (never re-run
      blindly) and the task INTERRUPTED; resuming re-runs only steps that never started, and a sensitive step needs a
      fresh yes (confirmations aren't saved, and the resume words don't count as asking for it)

Step states: PENDING RUNNING COMPLETED UNVERIFIED UNKNOWN FAILED BLOCKED WAITING CANCELED
Task states: RUNNING COMPLETED UNVERIFIED PARTIAL FAILED UNKNOWN WAITING CANCELED INTERRUPTED
"""

import json
import logging
import os
import threading
import time
import uuid

from room_agent import config
from room_agent import runtime as rt

log = logging.getLogger("room-agent")
MAX_TASKS = 50
MAX_STEPS = 10
MAX_RETRIES = 2
BACKOFF_S = (0.5, 1.5)
DEFAULT_TIMEOUT_S = 30.0
SLOW_TOOLS = {"research_web": 90.0, "play_music": 30.0, "open_url": 20.0, "browser_search": 20.0}
TRANSIENT = ("didn't answer in time", "couldn't connect", "timed out", "timeout", "network", "temporarily", "try again",
             "http_5", "the site answered 5", "is down")
DONE = ("COMPLETED", "UNVERIFIED")  # (what a dependent step may build on)
_lock = threading.Lock()
_tasks = {}           # id -> task dict (the in-memory copy; TASKS_FILE is the checkpoint)
_announced = set()    # interrupted tasks already mentioned to the model


# ---------------------------------------------------------------- the record
def _redact(text, limit=300):
    """Secrets / numbers / emails out (livelog.redact), and the user's home folder shown as ~ (it holds their name)."""
    import os

    text = str(text or "")
    home = os.path.expanduser("~")
    if home and len(home) > 3:
        text = text.replace(home, "~").replace(home.replace("\\", "/"), "~")
    try:
        from room_agent import livelog

        return livelog.redact(text, limit)
    except Exception:
        return text[:limit]


def _private(tool):
    from room_agent.actions import core

    cap = core.get(tool)
    return bool(cap and cap.private)


def _shown_args(tool, args):
    """What the record keeps of a step's arguments: names only for private tools, redacted values otherwise."""
    if _private(tool):
        return sorted(args or {})
    return {k: _redact(v, 160) if isinstance(v, str) else v for k, v in (args or {}).items() if k != "confidence"}


def new(goal, steps, kind="plan"):
    t = {"id": uuid.uuid4().hex[:8], "kind": kind, "goal": _redact(goal, 200), "intent": _redact(rt.turn_text or "", 300),
         "created": time.time(), "updated": time.time(), "state": "RUNNING", "turn": rt.turn_no, "events": [],
         "cost_usd": 0.0, "seconds": 0.0,
         "steps": [{"n": i + 1, "tool": s["tool"], "args": dict(s.get("args") or {}), "shown": _shown_args(s["tool"], s.get("args")),
                    "depends_on": [int(d) for d in s.get("depends_on") or []], "success": _redact(s.get("success", ""), 200),
                    "timeout_s": float(s.get("timeout_s") or SLOW_TOOLS.get(s["tool"], DEFAULT_TIMEOUT_S)),
                    "check": s.get("check") if isinstance(s.get("check"), dict) else None,
                    "skip": s.get("_skip") or "", "orig": s.get("_orig") or i + 1,
                    "state": "PENDING", "attempts": 0, "result": "", "evidence": "", "started": 0.0, "ended": 0.0}
                   for i, s in enumerate(steps)]}
    with _lock:
        _tasks[t["id"]] = t
    _save()
    return t


def event(t, what):
    t["events"].append({"at": round(time.time(), 3), "what": _redact(what, 240)})
    t["updated"] = time.time()


def _save():
    """Checkpoint: every task's state, without the full arguments of private steps (atomic write)."""
    with _lock:
        items = sorted(_tasks.values(), key=lambda t: t["created"])[-MAX_TASKS:]
        data = [{**t, "steps": [{k: v for k, v in s.items() if k != "args" or not _private(s["tool"])} for s in t["steps"]]}
                for t in items]
    try:
        tmp = config.TASKS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        tmp.replace(config.TASKS_FILE)
    except OSError as e:
        log.warning("tasks: checkpoint not written (%s)", e)


def load():
    """At startup: read the checkpoints; a step that was RUNNING when Jarvis stopped is UNKNOWN, its task INTERRUPTED."""
    try:
        data = json.loads(config.TASKS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    cut = []
    with _lock:
        for t in data if isinstance(data, list) else []:
            if t.get("state") in ("RUNNING",):
                for s in t["steps"]:
                    if s["state"] == "RUNNING":
                        s.update(state="UNKNOWN", result="interrupted by a restart: whether it happened isn't known")
                t["state"] = "INTERRUPTED"
                t["events"].append({"at": time.time(), "what": "Jarvis stopped while this task was running"})
                cut.append(t)
            _tasks[t["id"]] = t
    if cut:
        _save()
        log.warning("tasks: %d task(s) were interrupted by the last shutdown: %s", len(cut), ", ".join(t["goal"][:50] for t in cut))
    return cut


def get(task_id=None):
    with _lock:
        if task_id:
            return _tasks.get(task_id)
        live = [t for t in _tasks.values() if t["kind"] == "plan"]
    return max(live, key=lambda t: t["updated"]) if live else None


def recent(n=10):
    with _lock:
        return sorted(_tasks.values(), key=lambda t: t["updated"])[-n:]


# ---------------------------------------------------------------- independent success checks
# A step may name a check of its outcome, run by code after the tool said OK (never trusted from the tool's own words):
#   {"file_exists": path}  {"file_contains": [path, text]}  {"url_contains": text}  {"page_contains": text}
#   {"list_contains": [list, item]}
# -> (True / False / None, evidence). False = the step FAILED its success check; None = it couldn't be checked (UNVERIFIED).
def _path(p):
    from room_agent.computer import files

    q = os.path.expanduser(str(p))
    return q if os.path.isabs(q) else str(files.HOME / q)


def run_check(check):
    if not isinstance(check, dict) or not check:
        return None, ""
    kind, arg = next(iter(check.items()))
    try:
        if kind in ("file_exists", "file_contains"):
            from room_agent.computer import files

            path, text = (arg, None) if kind == "file_exists" else (arg[0], str(arg[1]))
            path = _path(path)
            ok, why = files.allowed(path)
            if not ok:
                return None, f"not checked: {why}"
            if not os.path.isfile(path):
                return False, f"{os.path.basename(path)} doesn't exist"
            if text is None:
                return True, f"{os.path.basename(path)} exists"
            found = text.lower() in open(path, encoding="utf-8", errors="replace").read().lower()
            return found, f"{os.path.basename(path)} {'contains' if found else 'does NOT contain'} \"{text[:40]}\""
        if kind in ("url_contains", "page_contains"):
            from room_agent.computer import browser_ops, browsers

            key, hwnd = browser_ops.target_window("")
            if not key:
                return None, "no browser window to check"
            if kind == "url_contains":
                url = browsers.read_state(hwnd).get("url", "")
                if not url:
                    return None, "the address bar couldn't be read"
                found = str(arg).lower() in url.lower()
                return found, f"the address {'contains' if found else 'does NOT contain'} \"{arg}\""
            page = browser_ops.read_page(hwnd, key, max_chars=200_000)
            if not page["text"]:
                return None, "the page text couldn't be read"
            found = str(arg).lower() in page["text"].lower()
            return found, f"the page {'shows' if found else 'does NOT show'} \"{str(arg)[:40]}\""
        if kind == "dir_exists":
            from room_agent.computer import files

            path = _path(arg)
            ok, why = files.allowed(os.path.join(path, "x.txt"))
            if not ok:
                return None, f"not checked: {why}"
            return os.path.isdir(path), f"the folder {os.path.basename(path)} {'exists' if os.path.isdir(path) else 'does NOT exist'}"
        if kind == "research_sources":
            from room_agent.computer.context import desk

            r = desk.research
            n = len(r.sources) if r is not None else 0
            return n >= int(arg or 1), f"{n} source(s) were actually read"
        if kind == "citations_valid":
            import re as _re

            from room_agent.computer.context import desk

            r = desk.research
            cited = {int(x) for x in _re.findall(r"\[(\d+)\]", str(arg))}
            have = len(r.sources) if r is not None else 0
            bad = sorted(c for c in cited if not 1 <= c <= have)
            if bad:
                return False, f"the summary cites {', '.join(f'[{b}]' for b in bad)}, but only {have} source(s) were read"
            return (True, f"every citation points at one of the {have} sources read") if cited else (
                None, "the summary cites no source")
        if kind == "draft_recipient":
            from room_agent.actions.context import env
            from room_agent.missions.websites import valid_email

            d = env.item("draft")
            if not d:
                return False, "no draft was recorded"
            ok = bool(valid_email(str(d.get("to", ""))) and str(arg).lower() in str(d.get("to", "")).lower())
            return ok, f"the draft is addressed to {d.get('to')}" + ("" if ok else f", not to {arg}")
        if kind == "list_contains":
            from room_agent.tools import lists

            items = [i["text"].lower() for i in lists._load().get(lists.canonical(arg[0]), []) if not i.get("done")]
            found = str(arg[1]).lower() in items
            return found, f"the {arg[0]} list {'has' if found else 'does NOT have'} \"{arg[1]}\""
    except Exception as e:  # noqa: BLE001 (a check that can't run proves nothing)
        return None, f"not checked ({e.__class__.__name__})"
    return None, f"unknown check '{kind}'"


# ---------------------------------------------------------------- running a plan
def _cancelled():
    from room_agent import cancel

    return cancel.requested()


def _run_with_timeout(plan, tool, args, timeout):
    """-> (ActionResult or None if it timed out)."""
    out = {}
    turn = rt.turn

    def go():
        try:
            out["r"] = plan.run(tool, args)
        except Exception as e:  # noqa: BLE001 (reported as a failure)
            from room_agent.actions.executor import ActionResult

            out["r"] = ActionResult(False, tool, args, f"FAILED: {tool} hit an error ({e.__class__.__name__}).")
    th = threading.Thread(target=go, name=f"task-step-{tool}", daemon=True)
    th.start()
    deadline = time.time() + timeout
    while th.is_alive() and time.time() < deadline:
        th.join(0.1)
        if _cancelled():  # (the step itself checks too; give it a moment to stop cleanly)
            th.join(2.0)
            break
    if rt.turn is not turn:
        rt.turn = turn
    return out.get("r")


def _retry_safe(cap, result):
    if result.outcome == "unknown":
        return False  # (it may have happened)
    msg = result.message.lower()
    if not cap.changes_state:
        return any(w in msg for w in TRANSIENT)
    return result.error_code == "not_as_expected" or "checking afterwards it didn't actually happen" in msg


def _step_state(result):
    return {"verified": "COMPLETED", "unverified": "UNVERIFIED", "unknown": "UNKNOWN", "waiting": "WAITING",
            "canceled": "CANCELED"}.get(result.outcome, "FAILED")


def run(t, only_pending=False):
    """Run (or continue) a plan's steps. -> the task, its state final unless WAITING."""
    from room_agent.actions import core
    from room_agent.actions.executor import Plan
    from room_agent.llm.budget import budget

    t0, cost0 = time.time(), budget.total()
    t["state"] = "RUNNING"
    plan = rt.current_plan if isinstance(rt.current_plan, Plan) else Plan()
    steps = {s["n"]: s for s in t["steps"]}
    for s in t["steps"]:
        if s["state"] in DONE or s["state"] in ("UNKNOWN", "FAILED", "CANCELED") and only_pending:
            continue
        if s["state"] not in ("PENDING", "BLOCKED", "WAITING", "CANCELED"):
            continue
        if s.get("skip") and s["state"] == "PENDING":  # (left out by the plan review: their words ruled it out)
            s.update(state="SKIPPED", result=_redact(f"left out: {s['skip']}", 300))
            event(t, f"step {s['n']} ({s['tool']}) left out: {s['skip']}")
            _save()
            continue
        if _cancelled():
            for rest in t["steps"]:
                if rest["state"] in ("PENDING", "BLOCKED", "WAITING"):
                    rest.update(state="CANCELED", result="not run: stopped")
            event(t, "stopped: the remaining steps were not run")
            break
        bad = [d for d in s["depends_on"] if steps.get(d, {}).get("state") not in DONE]
        if bad:
            s.update(state="BLOCKED", result=f"not run: step {', '.join(map(str, bad))} didn't complete")
            event(t, f"step {s['n']} ({s['tool']}) blocked by step {bad}")
            _save()
            continue
        cap = core.get(s["tool"])
        if cap is None:
            s.update(state="FAILED", result=f"there's no tool called {s['tool']}")
            continue
        from room_agent.actions import supervisor

        verdict, why = supervisor.before(t, s, max(0.0, budget.total() - cost0) + t.get("cost_usd", 0.0))
        if verdict == "skip":
            earlier = next(e for e in t["steps"] if e["state"] == "COMPLETED" and supervisor._key(e) == supervisor._key(s))
            s.update(state="COMPLETED", result=_redact(f"not repeated: {why}", 300), evidence=earlier.get("evidence", ""))
            _save()
            continue
        if verdict == "stop":
            for rest in t["steps"]:
                if rest["state"] in ("PENDING", "BLOCKED"):
                    rest.update(state="CANCELED", result=f"not run: {why}")
            event(t, f"stopped: {why}")
            break
        while True:
            s.update(state="RUNNING", started=time.time(), attempts=s["attempts"] + 1)
            _save()  # (a crash from here on leaves this step UNKNOWN, never re-run blindly)
            result = _run_with_timeout(plan, s["tool"], s["args"], s["timeout_s"])
            s["ended"] = time.time()
            if result is None:
                supervisor.after(t, s, timed_out=True)
                if cap.changes_state:
                    s.update(state="UNKNOWN", result=f"no answer within {s['timeout_s']:.0f}s: it may still have happened")
                else:
                    s.update(state="FAILED", result=f"no answer within {s['timeout_s']:.0f}s")
                event(t, f"step {s['n']} ({s['tool']}) timed out after {s['timeout_s']:.0f}s")
                break
            s.update(state=_step_state(result), result=_redact(result.message, 300), evidence=result.evidence or "")
            if s["state"] in DONE and s.get("check"):  # (the step's own success criterion, checked independently)
                ok, why = run_check(s["check"])
                s["evidence"] = (s["evidence"] + "; " if s["evidence"] else "") + _redact(f"success check: {why}", 200)
                if ok is False:
                    s.update(state="FAILED", result=_redact(f"the tool said OK, but the success check failed: {why}", 300))
                    event(t, f"step {s['n']} ({s['tool']}) failed its success check: {why}")
                elif ok is None and s["state"] == "COMPLETED":
                    s["state"] = "UNVERIFIED"
            if s["state"] == "FAILED" and s["attempts"] <= MAX_RETRIES and _retry_safe(cap, result) and not _cancelled():
                wait = BACKOFF_S[min(s["attempts"] - 1, len(BACKOFF_S) - 1)]
                event(t, f"step {s['n']} ({s['tool']}) failed ({result.message[:80]}); retry {s['attempts']} in {wait}s")
                if plan.steps and plan.steps[-1] is result:
                    plan.steps.pop()  # (the retry replaces this attempt: it isn't "an earlier step that failed")
                deadline = time.time() + wait
                while time.time() < deadline and not _cancelled():
                    time.sleep(0.05)
                continue
            break
        supervisor.after(t, s)
        if s["state"] == "FAILED" and not _cancelled() and config.TASK_RECOVERY:  # (actions/recovery.py: bounded)
            from room_agent.actions import recovery

            rec = t.setdefault("recoveries", [])
            if result is not None and plan.steps and plan.steps[-1] is result:
                plan.steps.pop()  # (the alternative replaces this attempt: it isn't "an earlier step that failed")
            ok, note = recovery.attempt(t, s, plan, s["result"], "success check failed" in s["result"],
                                        lambda tool, args: _run_with_timeout(plan, tool, args, max(s["timeout_s"], 30.0)),
                                        run_check, config.TASK_MAX_RECOVERIES - len(rec))
            if note:
                rec.append(_redact(note, 200))
                event(t, note)
        if s["state"] == "FAILED":
            event(t, f"step {s['n']} ({s['tool']}) failed: {s['result'][:120]}")
        _save()
        if s["state"] == "WAITING":
            event(t, f"step {s['n']} ({s['tool']}) is waiting for their answer: the task pauses here")
            break
        if s["state"] == "CANCELED":
            for rest in t["steps"]:
                if rest["state"] == "PENDING":
                    rest.update(state="CANCELED", result="not run: stopped")
            break
    t["seconds"] = round(t["seconds"] + time.time() - t0, 2)
    t["cost_usd"] = round(t["cost_usd"] + max(0.0, budget.total() - cost0), 5)
    t["state"] = final_state(t)
    if t["state"] == "COMPLETED":
        from room_agent.actions import supervisor

        if not supervisor.finish(t):  # (an output a step produced is gone: not reported as done)
            t["state"] = "PARTIAL"
    if t["state"] in ("COMPLETED", "PARTIAL", "FAILED") and config.TASK_EXPERIENCE:
        try:
            from room_agent.cognition import experience_v2

            experience_v2.record_task(t)
        except Exception as e:  # noqa: BLE001 (experience is a hint store: its failure never fails a task)
            log.debug("task experience not recorded: %s", e)
    t["updated"] = time.time()
    _save()
    _observe(t)
    return t


def final_state(t):
    states = [s["state"] for s in t["steps"] if s["state"] != "SKIPPED"]  # (left out on purpose: not a failure)
    if not states:
        return "FAILED"
    if "WAITING" in states:
        return "WAITING"
    if "RUNNING" in states:
        return "RUNNING"
    if "CANCELED" in states:
        return "CANCELED"
    ok = [x for x in states if x in DONE]
    if len(ok) == len(states):
        return "COMPLETED" if all(x == "COMPLETED" for x in states) else "UNVERIFIED"
    if "UNKNOWN" in states and not any(x in ("FAILED", "BLOCKED") for x in states):
        return "UNKNOWN"
    return "PARTIAL" if ok else "FAILED"


def resume(t):
    """Continue an INTERRUPTED / WAITING / PARTIAL task: only steps that never ran (or waited for a yes, or were blocked
    by something that has since completed). UNKNOWN and FAILED steps are left for them to decide."""
    if t["state"] not in ("INTERRUPTED", "WAITING", "PARTIAL", "CANCELED"):
        return t
    event(t, "resumed")
    for s in t["steps"]:
        if s["state"] in ("BLOCKED", "CANCELED"):
            s["state"] = "PENDING"
    return run(t, only_pending=True)


def cancel_task(t):
    for s in t["steps"]:
        if s["state"] in ("PENDING", "BLOCKED", "WAITING"):
            s.update(state="CANCELED", result="cancelled by them")
    t["state"] = final_state(t) if t["state"] != "RUNNING" else "CANCELED"
    event(t, "cancelled by them")
    t["updated"] = time.time()
    _save()
    return t


# ---------------------------------------------------------------- ordinary turns get a record too
def record_turn(plan, seconds, cost):
    """After a request whose tools the model called one by one: the same kind of record (no record if nothing ran)."""
    steps = [s for s in getattr(plan, "steps", []) or [] if s.capability != "run_task"]
    if not steps:
        return None
    t = new(rt.turn_text or "", [{"tool": s.capability, "args": s.parameters} for s in steps], kind="turn")
    for rec, r in zip(t["steps"], steps):
        rec.update(state=_step_state(r), result=_redact(r.message, 300), evidence=r.evidence or "", attempts=1,
                   started=r.at, ended=r.at)
    t.update(seconds=round(seconds, 2), cost_usd=round(cost, 5), state=final_state(t), updated=time.time())
    _save()
    _observe(t)
    return t


def _observe(t):
    try:
        from room_agent import livelog

        livelog.event("task", id=t["id"], kind_=t["kind"], state=t["state"], steps=len(t["steps"]),
                      done=sum(s["state"] in DONE for s in t["steps"]), seconds=float(t["seconds"]), cost_usd=float(t["cost_usd"]),
                      outcomes=[f"{s['tool']}:{s['state']}" for s in t["steps"]])
    except Exception:
        pass


# ---------------------------------------------------------------- what the model is told
def step_results(task_id=None):
    """[(tool, result)] of a plan task's steps that ran: for the claim check (what each step really did)."""
    t = get(task_id)
    if t is None:
        return []
    return [(s["tool"], s["result"]) for s in t["steps"] if s["result"] and s["state"] not in ("PENDING", "BLOCKED")]


def report(t):
    lines = [f"Task {t['id']} ({t['goal'][:80]}): {t['state']}."]
    for s in t["steps"]:
        lines.append(f"  {s['n']}. {s['tool']}: {s['state']}" + (f" - {s['result'][:160]}" if s["result"] else "")
                     + (f" [checked: {s['evidence']}]" if s["evidence"] and s["state"] == "COMPLETED" else ""))
    rules = {"COMPLETED": "Everything was done and checked.",
             "UNVERIFIED": "Everything ran; some steps couldn't be checked: say so for those.",
             "PARTIAL": "Some steps worked, some didn't: say exactly which, don't claim the rest.",
             "FAILED": "It didn't work: say so plainly.",
             "UNKNOWN": "Some steps may or may not have happened: say that, don't claim them, don't redo them blindly.",
             "WAITING": "It's paused for their answer to the question in the waiting step: ask it; if they agree, call "
                        "resume_task.",
             "CANCELED": "It was stopped: say what ran before it stopped."}
    lines.append(rules.get(t["state"], ""))
    rv = t.get("review") or {}
    if rv.get("left_out"):
        lines.append("Left out on purpose (tell them, in a few words): " + "; ".join(
            f"step {x['step']} ({x['tool']}): {x['why']}" for x in rv["left_out"]))
    if rv.get("notes"):
        lines.append("Plan checks: " + "; ".join(rv["notes"][:3]))
    if t.get("recoveries"):
        lines.append("Recovered: " + "; ".join(t["recoveries"][:3]))
    if t.get("supervisor"):
        lines.append("Supervisor: " + "; ".join(t["supervisor"][:3]))
    prefix = "OK" if t["state"] in ("COMPLETED", "UNVERIFIED") else "NEEDS_CONFIRMATION" if t["state"] == "WAITING" else \
        "UNKNOWN" if t["state"] == "UNKNOWN" else "FAILED"
    return f"{prefix}: " + "\n".join(lines)


def context_lines(user_text):
    """Once: a task cut off by a restart, or waiting for an answer."""
    out = []
    for t in recent(MAX_TASKS):
        if t["kind"] != "plan":
            continue
        if t["state"] == "INTERRUPTED" and t["id"] not in _announced:
            _announced.add(t["id"])
            unknown = [s["tool"] for s in t["steps"] if s["state"] == "UNKNOWN"]
            out.append(f"- task {t['id']} (\"{t['goal'][:70]}\") was INTERRUPTED by a restart"
                       + (f"; these steps may or may not have happened: {', '.join(unknown)}" if unknown else "")
                       + ". Tell them once; offer to resume (only steps that never started run again; anything sensitive "
                         "asks again).")
        elif t["state"] == "WAITING" and time.time() - t["updated"] < 1800:
            out.append(f"- task {t['id']} (\"{t['goal'][:70]}\") is paused waiting for their yes/no; if they agree, call "
                       "resume_task.")
    return out


def snapshot(n=6):
    """For the dashboard: the latest tasks, short."""
    return [{"id": t["id"], "kind": t["kind"], "goal": t["goal"][:90], "state": t["state"], "seconds": t["seconds"],
             "cost_usd": t["cost_usd"], "steps": [{"tool": s["tool"], "state": s["state"]} for s in t["steps"]],
             "updated": t["updated"]} for t in reversed(recent(n))]
