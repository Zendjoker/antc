"""The mission engine: long workflows run by code, in the background, checkpointed after every step.

A workflow (registered by a feature module, e.g. missions/business.py) says how to plan a mission into steps and how to
run each kind of step. The engine then:
    - runs one step at a time, in plan order, when its dependencies are done ("research:*" = every step whose key starts
      with "research:"; "~key" = wait for it but run even if it failed); a step whose hard dependency failed is BLOCKED
    - lets a step add new steps (discovering 40 businesses adds 40 research steps) or skip pending ones (enough found)
    - checkpoints the mission and each step in SQLite before and after it runs
    - retries a failed step (at most its max_attempts, with backoff) only if it's idempotent (safe to repeat)
    - pauses / resumes / stops on request, on the PC-wide emergency stop, and BEFORE exceeding the mission's budget
    - reports progress from the stored state only (never from what a model believes)

Cancellation is enforced, not advisory (runctx.py): each run of a step gets a Token. Timeout, pause, stop and the
emergency stop cancel it, and every side effect a step can have (requests, paid calls, database writes, files, the
coding worker's processes) checks it first. A run's outcome is recorded only if that run still owns the step (run_id),
so a stopped or timed-out run can never mark a step done afterwards, and its late result is ignored.

After a restart (load): missions that were running come back "interrupted" (resume by voice / dashboard); a step that was
running goes back to pending if idempotent, otherwise it's marked failed ("not repeated automatically"); budget
reservations left open are counted at their full estimate; approvals left mid-way become "unknown"; half-swapped demo
sites are finished or rolled back; any Google Places content an older version stored is purged.
Missions run one at a time, in a single background thread (plus one thread per step run, for timeouts).

Goal-driven missions (missions/goals.py; a `goals` row next to the mission) also get:
    - plan validation BEFORE anything runs or is added: every step kind has a handler, no duplicate keys, no dependency
      on a step that doesn't exist, no dependency cycle (validate_plan)
    - step contracts (Workflow.contracts): purpose, the existing capability used, expected output, a deterministic
      read-only verification, failure handling, estimated cost. A step is recorded completed only when its handler
      succeeded AND its contract's verification passed (MISSION_VERIFY); a failed verification is a failure (retried only
      if the step is idempotent)
    - bounded replanning (Workflow.replan, MISSION_REPLAN, at most MISSION_MAX_REPLANS times): when every step has ended
      but a success criterion isn't met, the workflow may add steps (e.g. the next-best business for a demo that failed
      its checks). Every change is logged with its reason. Never a paid or irreversible action that wasn't asked for.
    - at the end the goal's success criteria are checked against the database and files, not against any model.
A mission never ends "completed" with steps that never ran (an unreachable step is marked blocked).
"""

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from room_agent import config
from room_agent.missions import crashpoints, meter, ownership, runctx
from room_agent.missions.store import GuardedStore, store

log = logging.getLogger("room-agent")
DONE = ("completed", "skipped")
DEAD = ("failed", "blocked", "cancelled", "uncertain")  # (uncertain: interrupted, its effect unknown: needs checking)
LIVE_MISSION = ("planned", "running")
PAUSED = ("paused", "paused_budget", "paused_daily", "interrupted")
RESTARTABLE = ("completed", "finished_with_problems")  # the only states new work may start a new run from
WHY_NOT_RUNNING = {
    "paused": "the mission is paused: say 'resume the mission' to run it",
    "interrupted": "the mission was interrupted by a restart: say 'resume the mission' to run it",
    "paused_budget": "the mission's budget is used up: raise it (and resume) to run it",
    "paused_daily": "today's model budget (DAILY_BUDGET_USD) is used up: it can run after it resets",
    "planned": "the mission hasn't started yet",
}
FINAL = ("completed", "cancelled", "finished_with_problems")
STEP_TIMEOUT_S = 300
BACKOFF_S = (5, 20, 60)
GRACE_S = 10  # after cancelling a run, how long the engine waits for its thread to reach a checkpoint and end


@dataclass
class StepSpec:
    key: str
    kind: str
    title: str
    args: dict = field(default_factory=dict)
    depends: list = field(default_factory=list)
    idempotent: bool = True
    max_attempts: int = 3


@dataclass
class StepResult:
    ok: bool
    result: dict = field(default_factory=dict)
    evidence: str = ""                      # what proves it's done (checked by the handler before returning ok)
    add: list = field(default_factory=list)  # [StepSpec] to append to the plan
    skip: list = field(default_factory=list)  # keys of pending steps that are no longer needed
    error: str = ""
    final: bool = False                     # a failure that retrying can't fix (don't retry)
    committed: bool = False                 # its side effect is done (e.g. a site swapped in): record it even if the
                                            # run was cancelled while finishing up


@dataclass
class Contract:
    """What a step kind promises, checked by code. verify(ctx, step, result) -> [problems] must be read-only and
    deterministic (files, database, the step's own result); never a model's opinion."""
    purpose: str
    capability: str                          # the existing component that does it
    expected: str                            # the output it must produce
    verify: Callable = None
    on_failure: str = ""                     # what happens when it fails
    cost: Callable = None                    # (mission params, step args) -> estimated USD
    parallel_safe: bool = False              # may run at the same time as other steps (MISSION_CONCURRENCY)
    resource: Callable = None                # (step args) -> key: two steps with the same key never run at once


@dataclass
class Workflow:
    kind: str
    title: str
    plan: Callable                          # (params) -> [StepSpec]
    handlers: dict                          # step kind -> fn(ctx, step) -> StepResult
    describe: Callable                      # (params) -> short title of the mission
    summarize: Callable                     # (ctx) -> summary text at the end
    progress: Callable = None               # (ctx) -> extra progress facts (e.g. lead counts)
    status_line: Callable = None            # (ctx, progress) -> one spoken sentence
    timeouts: dict = field(default_factory=dict)  # step kind -> seconds
    recover: Callable = None                # (mission row) -> [notes]: workflow-specific repair at startup
    reconcile: Callable = None              # (mission row, step row) -> (state, note) for a NON-idempotent step that was
                                            # running when Jarvis stopped: "completed" (its effect is in place),
                                            # "pending" (provably not applied, safe to run again), "uncertain"
    contracts: dict = field(default_factory=dict)  # step kind -> Contract
    replan: Callable = None                 # (ctx, criteria) -> ([StepSpec], why): steps that could still meet an unmet
                                            # success criterion (goal-driven missions only)


def validate_plan(wf, specs, existing=None):
    """-> [problems] ([] = valid). existing: {key: depends} of steps the mission already has."""
    existing = existing or {}
    problems = []
    keys = [x.key for x in specs]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        problems.append(f"duplicate step keys: {', '.join(dup[:5])}")
    known = set(existing) | set(keys)
    for x in specs:
        if x.kind not in wf.handlers:
            problems.append(f"'{x.key}': no capability for step kind '{x.kind}'")
        elif wf.contracts and x.kind not in wf.contracts:
            problems.append(f"'{x.key}': step kind '{x.kind}' has no contract (expected output / verification)")
        for d in x.depends or []:
            d = d.lstrip("~")
            if not d.endswith(":*") and d not in known:
                problems.append(f"'{x.key}' depends on '{d}', which isn't part of the plan")
    graph = {k: [d.lstrip("~") for d in deps or [] if not d.endswith(":*")] for k, deps in existing.items()}
    graph.update({x.key: [d.lstrip("~") for d in x.depends or [] if not d.lstrip("~").endswith(":*")] for x in specs})
    state = {}

    def cyclic(k):
        if state.get(k) == 1:
            return True
        if state.get(k) == 2:
            return False
        state[k] = 1
        if any(cyclic(d) for d in graph.get(k, []) if d in graph):
            return True
        state[k] = 2
        return False

    loops = sorted(k for k in graph if k in keys and cyclic(k))
    if loops:
        problems.append(f"dependency cycle through: {', '.join(loops[:5])}")
    return problems


def _existing_plan(mid):
    return {st["key"]: st["depends"] for st in store().steps(mid)}


WORKFLOWS = {}
_lock = threading.Lock()
# Mission control operations (add work / pause / resume / stop) are serialized: each one reads the state and acts on it
# as one unit, so e.g. work can't be added to a mission in the moment between "it's paused" and "it's stopped".
_control = threading.RLock()


def _controlled(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **k):
        with _control:
            return fn(*a, **k)
    return wrapper
_runner = {"thread": None}
_active = {}       # mission id -> {step key: the Token of its run in progress}
_abandoned = []    # (thread, token, mission id, step key): runs cancelled but not yet ended (for the dashboard)
_budget_hit = {}   # mission id -> daily?: a parallel step hit the budget; the scheduler pauses once the others ended


def register(workflow):
    WORKFLOWS[workflow.kind] = workflow
    return workflow


def _mission_should_stop(mid, started):
    """-> reason to stop (or ""): the mission isn't running any more, or the emergency stop fired since `started`."""
    try:
        from room_agent import emergency

        if emergency.stopped_at[0] > started:
            return "emergency stop"
    except Exception:  # noqa: BLE001
        pass
    m = store().mission(mid)
    if m is None:
        return "the mission is gone"
    return "" if m["state"] == "running" else f"mission {m['state']}"


class Ctx:
    """What a step handler gets: the mission, its params and folder, a guarded store, a log, and its token."""

    def __init__(self, mission, token):
        self.mission = mission
        self.id = mission["id"]
        self.params = mission["params"]
        self.workspace = Path(mission["workspace"])
        self.token = token
        self.store = GuardedStore(store(), token)  # (every write checks the token first)

    def log(self, text, level="info"):
        self.store.event(self.id, text, level)
        (log.warning if level in ("warn", "error") else log.info)("mission %s: %s", self.id, text)

    def stop_requested(self):
        return self.token.cancelled

    def check(self):
        self.token.check()


# ---------------------------------------------------------------- creating and controlling
def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:40] or "mission"


def create(kind, params, budget_usd=None, goal=None):
    """Plan a mission and start it in the background. -> the mission row. Raises ownership.NotOwner if another Jarvis
    process runs this database's missions, ValueError for an invalid plan (nothing is created then).
    goal: the structured goal (missions/goals.py) it serves; stored with it (criteria, decisions, replanning)."""
    ownership.acquire()
    wf = WORKFLOWS.get(kind)
    if wf is None:
        raise ValueError(f"no workflow called {kind}")
    specs = list(wf.plan(params))
    problems = validate_plan(wf, specs)
    if problems:
        raise ValueError("the plan isn't valid, so nothing was started: " + "; ".join(problems[:4]))
    budget = float(budget_usd if budget_usd is not None else config.MISSION_DEFAULT_BUDGET_USD)
    budget = max(0.0, min(budget, config.MISSION_MAX_BUDGET_USD))
    title = wf.describe(params)
    mid = time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]
    workspace = Path(config.MISSIONS_DIR) / f"{mid}-{_slug(title)}"
    workspace.mkdir(parents=True, exist_ok=True)
    s = store()
    m = s.create_mission(mid, kind, title, params, budget, workspace)
    if goal is not None:
        s.set_goal(mid, goal)
    for spec in specs:
        s.add_step(mid, spec.key, spec.kind, spec.title, spec.args, spec.depends, spec.idempotent, spec.max_attempts)
    s.set_state(mid, "running", "created")
    s.event(mid, f"mission planned: {title} (budget ${budget:.2f})")
    _audit("mission_started", mid, title=title, budget=budget)
    start_runner()
    return s.mission(mid)


class MissionClosed(ValueError):
    """New work for a mission that was stopped for good."""


@_controlled
def add_steps(mid, specs):
    """Extend a mission's plan (e.g. 'build demos for the best three'). Never resumes a paused / interrupted / budget-
    paused mission: the steps wait until it's resumed explicitly. A finished mission starts a new run for them.
    -> {"added": n, "state": the mission's state afterwards, "running": bool, "why_not": text when not running}
    Raises MissionClosed for a cancelled (stopped) mission."""
    s = store()
    m = s.mission(mid)
    if m is None:
        raise MissionClosed("there's no such mission")
    if m["state"] == "cancelled":
        raise MissionClosed("that mission was stopped for good; start a new one")
    wf = WORKFLOWS.get(m["kind"])
    problems = validate_plan(wf, specs, _existing_plan(mid)) if wf else []
    if problems:
        raise ValueError("those steps aren't a valid plan: " + "; ".join(problems[:4]))
    added = sum(s.add_step(mid, x.key, x.kind, x.title, x.args, x.depends, x.idempotent, x.max_attempts) for x in specs)
    if added and m["state"] in RESTARTABLE:
        s.set_state(mid, "running", f"{added} step(s) added", only_from=RESTARTABLE)
        s.event(mid, f"{added} step{'s' if added != 1 else ''} added; running again")
    state = s.mission(mid)["state"]
    if added and state == "running":
        start_runner()
    elif added:
        s.event(mid, f"{added} step{'s' if added != 1 else ''} added; waiting ({state})")
    return {"added": added, "state": state, "running": state == "running",
            "why_not": "" if state == "running" else WHY_NOT_RUNNING.get(state, f"the mission is {state}")}


def _cancel_active(mid, why):
    for t in list((_active.get(mid) or {}).values()):
        t.cancel(why)


@_controlled
def pause(mid, why="you asked"):
    m = store().mission(mid)
    if m is None or m["state"] not in LIVE_MISSION:
        return False
    if not store().set_state(mid, "paused", why, only_from=LIVE_MISSION):
        return False
    _cancel_active(mid, f"paused ({why})")
    store().event(mid, f"paused ({why})")
    _audit("mission_paused", mid, why=why)
    return True


def _headroom(m):
    return m["budget_usd"] - m["spent_usd"] - (m.get("reserved_usd") or 0.0)


def raise_budget(mid, extra_usd, via):
    """More budget for a mission (only from an explicit request: a voice tool that always asks, or a dashboard click).
    -> (old budget, new budget), or None for no such mission / a non-positive amount. new == old when the ceiling
    (MISSION_MAX_BUDGET_USD) allowed no increase; new < old + extra when it was capped."""
    s = store()
    m = s.mission(mid)
    if m is None or extra_usd <= 0:
        return None
    old = m["budget_usd"]
    new = min(old + float(extra_usd), config.MISSION_MAX_BUDGET_USD)
    if new <= old + 1e-9:
        return old, old
    s.update_mission(mid, budget_usd=new)
    s.event(mid, f"budget raised to ${new:.2f} ({via})")
    _audit("mission_budget_raised", mid, budget=new, via=via)
    return old, new


def resume_blocker(mid):
    """Why the mission can't be resumed right now ("" = it can)."""
    m = store().mission(mid)
    if m is None:
        return "there's no such mission"
    if m["state"] not in PAUSED:
        return f"it isn't paused (it's {m['state'].replace('_', ' ')})"
    if _headroom(m) <= 0 and (m["budget_usd"] > 0 or m["spent_usd"] > 0):
        return (f"its budget is used up (${m['spent_usd']:.2f} spent + ${m.get('reserved_usd') or 0:.2f} in progress of "
                f"${m['budget_usd']:.2f}); raise the mission budget first")
    if m["state"] == "paused_daily" and meter.daily_exhausted():
        return ("today's model budget (DAILY_BUDGET_USD) is still used up; raising the mission's budget won't help. "
                "It resets tomorrow")
    return ""


@_controlled
def resume(mid):
    s = store()
    why = resume_blocker(mid)
    if why:
        if s.mission(mid) is not None:
            s.event(mid, f"not resumed: {why}", "warn")
        return False
    if not s.set_state(mid, "running", "resumed", only_from=PAUSED):  # (stopped / resumed meanwhile)
        return False
    s.event(mid, "resumed")
    _audit("mission_resumed", mid)
    start_runner()
    return True


@_controlled
def stop(mid, why="you asked"):
    """Stop for good: pending steps are cancelled; completed work (leads, sites, drafts) is kept."""
    s = store()
    m = s.mission(mid)
    if m is None or m["state"] in FINAL:
        return False
    if not s.set_state(mid, "cancelled", why, only_from=tuple(x for x in LIVE_MISSION + PAUSED + RESTARTABLE
                                                              if x not in FINAL)):
        return False
    _cancel_active(mid, f"stopped ({why})")
    for st in s.steps(mid):
        if st["state"] in ("pending", "running"):
            s.update_step(st["id"], state="cancelled", error=f"stopped: {why}", run_id="")
    s.event(mid, f"stopped ({why}); the work done so far is kept")
    _audit("mission_stopped", mid, why=why)
    return True


def pause_all(why):
    """The PC-wide emergency stop: every running mission pauses at once (resumable)."""
    n = 0
    for m in store().missions(50, states=LIVE_MISSION):
        n += pause(m["id"], why)
    return n


def load():
    """At startup: repair everything a crash / shutdown may have left half-done. -> [notes]
    Order matters: ledger (open reservations -> uncertain, at full estimate) -> approvals (mid-way -> unknown) ->
    Places purge -> files (site swaps finished / undone) -> interrupted steps (reconciled from operations + files) ->
    interrupted missions."""
    s = store()
    notes = []
    try:
        ownership.acquire(wait_s=15)  # (a crashed predecessor's lock can take a moment to be released by the OS)
    except ownership.NotOwner as e:  # (another live Jarvis owns these missions: recovering them here would corrupt them)
        log.warning("missions: not recovered here: %s", e)
        return [f"missions not recovered: {e}"]
    n = meter.settle_orphans()
    if n:
        notes.append(f"{n} paid call{'s' if n != 1 else ''} cut off by the shutdown counted at the full estimate")
    try:
        from room_agent.missions import outreach

        n = outreach.recover_approvals()
        if n:
            notes.append(f"{n} Gmail draft approval{'s' if n != 1 else ''} interrupted: outcome unknown, check Gmail")
    except Exception as e:  # noqa: BLE001
        log.warning("missions: approvals not recovered: %s", e)
    try:
        n = s.purge_places_content()
        if n:
            notes.append(f"Google Places content removed from {n} stored lead{'s' if n != 1 else ''}")
    except Exception as e:  # noqa: BLE001
        log.warning("missions: Places purge failed: %s", e)
    # 1. repair files first (a half-done site swap is finished or undone), so step reconciliation below sees the real
    #    state of the world; 2. then decide every interrupted step from the recorded operations and the files.
    for m in s.missions(100):
        wf = WORKFLOWS.get(m["kind"])
        if wf and wf.recover and m["state"] not in ("cancelled",):
            try:
                for note in wf.recover(m) or []:
                    s.event(m["id"], note, "warn")
                    notes.append(note)
            except Exception as e:  # noqa: BLE001
                log.warning("mission %s: recovery failed: %s", m["id"], e)
    for m in s.missions(100, states=LIVE_MISSION):
        wf = WORKFLOWS.get(m["kind"])
        for st in s.steps(m["id"]):
            if st["state"] != "running":
                continue
            if st["idempotent"]:
                s.update_step(st["id"], state="pending", run_id="", error="interrupted by a restart; will run again")
                continue
            state, note = "uncertain", ("interrupted by a restart while running; its effect is unknown, so it isn't "
                                        "repeated automatically")
            if wf is not None and wf.reconcile is not None:
                try:
                    state, note = wf.reconcile(m, st)
                except Exception as e:  # noqa: BLE001
                    log.warning("mission %s: reconciling %s failed: %s", m["id"], st["key"], e)
            fields = {"state": state, "run_id": "", "error": "" if state == "completed" else note}
            if state == "completed":
                fields.update(ended=time.time(), evidence=note[:2000])
            s.update_step(st["id"], **fields)
            notes.append(f"{m['title']}: step '{st['title']}' -> {state} ({note[:120]})")
        s.set_state(m["id"], "interrupted", "Jarvis stopped while it was running", only_from=LIVE_MISSION)
        s.event(m["id"], "Jarvis stopped while this mission was running: paused; say 'resume the mission' to continue", "warn")
        notes.append(f"mission '{m['title']}' interrupted")
    for note in notes:
        log.info("missions at startup: %s", note)
    return notes


# ---------------------------------------------------------------- running
def start_runner():
    try:
        ownership.acquire()
    except ownership.NotOwner as e:
        log.warning("missions: runner not started: %s", e)
        return
    with _lock:
        t = _runner["thread"]
        if t is not None and t.is_alive():
            return  # (it re-reads the running missions before it ends)
        t = threading.Thread(target=_run_all, name="missions", daemon=True)
        _runner["thread"] = t
        t.start()


def _run_all():
    while True:
        live = store().missions(50, states=("running",))
        if not live:
            with _lock:  # (re-checked under the lock, so a mission started right now isn't left without a runner)
                if not store().missions(1, states=("running",)):
                    _runner["thread"] = None
                    return
            continue
        mid = sorted(live, key=lambda m: m["created"])[0]["id"]
        try:
            _run_mission(mid)
        except Exception as e:  # noqa: BLE001 (a crash in one mission must not take down the runner)
            log.exception("mission %s crashed", mid)
            store().update_mission(mid, error=f"{e.__class__.__name__}: {str(e)[:200]}")
            store().set_state(mid, "paused", f"internal error {e.__class__.__name__}")
            store().event(mid, f"paused after an internal error ({e.__class__.__name__}); see the log", "error")


def _deps(steps_by_key, depends):
    """-> [(step, soft)]. "~key" is a soft dependency: wait until it has ended, but run even if it failed."""
    out = []
    for d in depends or []:
        soft = d.startswith("~")
        d = d.lstrip("~")
        if d.endswith(":*"):
            out += [(s, soft) for k, s in steps_by_key.items() if k.startswith(d[:-1])]
        elif d in steps_by_key:
            out.append((steps_by_key[d], soft))
    return out


def _next_step(s, mid):
    for _ in range(1000):  # (each pass may block one step; then look again)
        steps = s.steps(mid)
        by_key = {st["key"]: st for st in steps}
        blocked_one = False
        for st in steps:
            if st["state"] != "pending":
                continue
            deps = _deps(by_key, st["depends"])
            dead = [d["key"] for d, soft in deps if not soft and d["state"] in DEAD]
            if dead:
                s.update_step(st["id"], state="blocked", error=f"not run: {', '.join(dead[:3])} didn't complete")
                blocked_one = True
                break
            if all(d["state"] in DONE or (soft and d["state"] in DEAD) for d, soft in deps):
                return st
        if not blocked_one:
            return None
    return None


def _run_mission(mid):
    s = store()
    started = time.time()
    m = s.mission(mid)
    wf = WORKFLOWS.get(m["kind"])
    if wf is None:
        s.update_mission(mid, error=f"no workflow '{m['kind']}' in this version")
        s.set_state(mid, "paused", "no workflow for this kind")
        return
    s.event(mid, "running")
    if config.MISSION_CONCURRENCY > 1 and wf.contracts:
        return _run_parallel(wf, mid, started)
    while True:
        m = s.mission(mid)
        if m is None or m["state"] != "running":
            return
        why = _mission_should_stop(mid, started)
        if why == "emergency stop":
            pause(mid, why)
            return
        if _headroom(m) <= 0 and (m["budget_usd"] > 0 or m["spent_usd"] > 0):
            _budget_pause(mid, m)
            return
        st = _next_step(s, mid)
        if st is None:
            if _replan(wf, m):
                continue
            _finish(wf, m)
            return
        _run_step(wf, m, st, started)


def _replan(wf, m):
    """Every step has ended. If this is a goal-driven mission with an unmet success criterion, the workflow may add
    steps that could still meet it - bounded (MISSION_MAX_REPLANS), validated, logged with the reason. -> added any?"""
    if wf.replan is None or not config.MISSION_REPLAN:
        return False
    s = store()
    mid = m["id"]
    row = s.goal(mid)
    if row is None:
        return False
    from room_agent.missions import goals

    crit = goals.evaluate(mid)
    if not crit or all(c["met"] for c in crit):
        return False
    if row["replans"] >= config.MISSION_MAX_REPLANS:
        if not any(d.get("type") == "replan_limit" for d in row.get("decisions") or []):
            s.add_decision(mid, {"type": "replan_limit", "why": f"replanning limit reached ({config.MISSION_MAX_REPLANS}); "
                                                                "reporting what's done and what isn't"})
        return False
    try:
        specs, why = wf.replan(Ctx(m, runctx.Token(mid, "replan")), crit)
    except Exception as e:  # noqa: BLE001 (a replanning bug must not take the mission down: it just ends)
        log.exception("mission %s: replanning failed", mid)
        s.add_decision(mid, {"type": "replan_error", "why": f"replanning failed ({e.__class__.__name__})"})
        return False
    if not specs:
        if why and not any(d.get("why") == why for d in row.get("decisions") or []):
            s.add_decision(mid, {"type": "no_replan", "why": why})
        return False
    problems = validate_plan(wf, specs, _existing_plan(mid))
    if problems:
        s.add_decision(mid, {"type": "replan_rejected", "why": "; ".join(problems[:3])})
        return False
    added = sum(s.add_step(mid, x.key, x.kind, x.title, x.args, x.depends, x.idempotent, x.max_attempts) for x in specs)
    if not added:
        return False
    s.add_decision(mid, {"type": "replan", "why": why, "added": [x.key for x in specs]}, replan=True)
    s.event(mid, f"plan changed: {why} ({added} step{'s' if added != 1 else ''} added)", "warn")
    _audit("mission_replanned", mid, why=why[:200], added=added)
    return True


def _verify(wf, ctx, st, res):
    """The step's contract check (deterministic, read-only). -> [problems]"""
    c = wf.contracts.get(st["kind"]) if config.MISSION_VERIFY else None
    if c is None or c.verify is None:
        return []
    try:
        return [str(x) for x in (c.verify(ctx, st, res) or [])]
    except Exception as e:  # noqa: BLE001
        return [f"its verification couldn't run ({e.__class__.__name__}: {str(e)[:120]})"]


def _release_active(mid, key):
    with _lock:
        runs = _active.get(mid) or {}
        runs.pop(key, None)
        if not runs:
            _active.pop(mid, None)


def _ready_steps(s, mid, busy):
    """Pending steps whose dependencies are done, in plan order (blocking those whose hard dependency failed).
    busy: keys running right now (a step waiting out its retry backoff counts as busy)."""
    for _ in range(1000):
        steps = s.steps(mid)
        by_key = {st["key"]: st for st in steps}
        blocked_one = False
        ready = []
        for st in steps:
            if st["state"] != "pending" or st["key"] in busy:
                continue
            deps = _deps(by_key, st["depends"])
            dead = [d["key"] for d, soft in deps if not soft and d["state"] in DEAD]
            if dead:
                s.update_step(st["id"], state="blocked", error=f"not run: {', '.join(dead[:3])} didn't complete")
                blocked_one = True
                break
            if all(d["state"] in DONE or (soft and d["state"] in DEAD) for d, soft in deps):
                ready.append(st)
        if not blocked_one:
            return ready
    return []


def _run_parallel(wf, mid, started):
    """Like the loop in _run_mission, with up to MISSION_CONCURRENCY steps at once. Only steps whose contract says
    parallel_safe run together; two steps with the same resource key (the same business / site) never overlap; a step
    that isn't parallel-safe runs alone. Budget reservations are atomic (meter), so parallel paid calls can't overspend;
    pause / stop / the emergency stop cancel every run in progress; each run records its outcome only if it still owns
    its step (run_id), exactly as one at a time."""
    s = store()
    running = {}  # step key -> (thread, resource key or None, parallel_safe)

    def reap():
        for k in [k for k, v in running.items() if not v[0].is_alive()]:
            running.pop(k)

    def drain():
        while running:
            reap()
            time.sleep(0.05)

    _budget_hit.pop(mid, None)
    while True:
        reap()
        m = s.mission(mid)
        if m is None or m["state"] != "running":
            drain()
            return
        if mid in _budget_hit:  # (no new step starts; the ones in flight - their money reserved - end normally)
            drain()
            daily = _budget_hit.pop(mid, False)
            m = s.mission(mid)
            if m["state"] == "running":
                _budget_pause(mid, m, daily=daily)
            return
        why = _mission_should_stop(mid, started)
        if why == "emergency stop":
            pause(mid, why)
            drain()
            return
        if _headroom(m) <= 0 and (m["budget_usd"] > 0 or m["spent_usd"] > 0):
            drain()
            m = s.mission(mid)
            if m["state"] == "running":
                _budget_pause(mid, m)
            return
        started_one = False
        alone = any(not v[2] for v in running.values())
        for st in _ready_steps(s, mid, set(running)):
            if alone or len(running) >= config.MISSION_CONCURRENCY:
                break
            c = wf.contracts.get(st["kind"])
            safe = bool(c and c.parallel_safe)
            if running and not safe:
                continue  # (waits until nothing else runs)
            res_key = None
            if c is not None and c.resource is not None:
                try:
                    res_key = c.resource(st["args"])
                except Exception:  # noqa: BLE001
                    res_key = f"step:{st['key']}"
            if res_key is not None and any(v[1] == res_key for v in running.values()):
                continue
            th = threading.Thread(target=_run_step, args=(wf, m, st, started, True), name=f"mission-run-{st['key']}",
                                  daemon=True)
            running[st["key"]] = (th, res_key, safe)
            th.start()
            started_one = True
            if not safe:
                break
        if not running and not started_one:
            if _replan(wf, m):
                continue
            _finish(wf, m)
            return
        time.sleep(0.02)


def _budget_pause(mid, m, daily=False):
    """Pause for the limit that was actually hit: the mission's own budget, or the PC's daily model budget."""
    s = store()
    if daily:
        s.set_state(mid, "paused_daily", "today's model budget used up", only_from=("running",))
        s.event(mid, "paused: today's model budget (DAILY_BUDGET_USD) is used up; the mission's own budget isn't. It can "
                     "resume after the daily budget resets", "warn")
        _announce(f"I paused the mission '{m['title']}': today's overall model budget is used up (the mission still has "
                  "budget left). It can continue tomorrow.", mid)
        return
    s.set_state(mid, "paused_budget", "mission budget used up", only_from=("running",))
    s.event(mid, f"paused: the mission budget is used up (${m['spent_usd']:.4f} spent, ${m.get('reserved_usd') or 0:.4f} "
                 f"reserved, of ${m['budget_usd']:.2f}); raise it to continue", "warn")
    _announce(f"I paused the mission '{m['title']}': it reached its budget of ${m['budget_usd']:.2f}. Raise it if you want "
              "me to continue.", mid)


def _call(handler, ctx, st, token):
    """Run a step handler in its own thread, bound to its token. -> ("ok", result) / ("error", exception) /
    ("cancelled", reason): on cancel (timeout / pause / stop) the run is abandoned; its thread can't act any more."""
    out = {}

    def go():
        runctx.bind(token)
        meter.bind(ctx.id, st["key"])
        try:
            out["r"] = handler(ctx, st)
        except BaseException as e:  # noqa: BLE001 (reported to the engine)
            out["e"] = e

    th = threading.Thread(target=go, name=f"mission-step-{st['kind']}", daemon=True)
    th.start()
    while th.is_alive() and not token.cancelled:
        th.join(0.25)
    if th.is_alive():  # cancelled: give it a moment to reach a checkpoint
        th.join(GRACE_S)
        if th.is_alive():
            _abandoned.append((th, token, ctx.id, st["key"]))
            log.warning("mission %s: step %s abandoned (%s); it can't act any more", ctx.id, st["key"], token.reason)
            return "cancelled", token.reason
    _abandoned[:] = [a for a in _abandoned if a[0].is_alive()]
    if "e" in out:
        if token.cancelled or isinstance(out["e"], runctx.Cancelled):
            return "cancelled", token.reason or str(out["e"])
        return "error", out["e"]
    if token.cancelled and "r" not in out:
        return "cancelled", token.reason
    r = out.get("r")
    if token.cancelled and not (isinstance(r, StepResult) and r.committed):
        return "cancelled", token.reason  # (finished, but after being cancelled: the result is not trusted / used)
    return "ok", r


def _run_step(wf, m, st, started, defer_budget_pause=False):
    s = store()
    mid = m["id"]
    handler = wf.handlers.get(st["kind"])
    if handler is None:
        s.update_step(st["id"], state="failed", error=f"no handler for step kind '{st['kind']}'")
        return
    attempt = st["attempts"] + 1
    run_id = uuid.uuid4().hex[:12]
    timeout = wf.timeouts.get(st["kind"], STEP_TIMEOUT_S)
    token = runctx.Token(mid, st["key"], deadline=time.time() + timeout,
                         should_stop=lambda: _mission_should_stop(mid, started))
    crashpoints.hit("before_operation")
    with _lock:
        _active.setdefault(mid, {})[st["key"]] = token  # (registered before the claim: a stop / pause cancels this run)
    if not s.claim_step(st["id"], mid, run_id, attempt, owner=ownership.token() or "-"):
        _release_active(mid, st["key"])  # (the step was cancelled / the mission stopped or paused since it was read)
        return
    spent0 = s.mission(mid)["spent_usd"]
    ctx = Ctx(m, token)
    try:
        kind, res = _call(handler, ctx, st, token)
    finally:
        _release_active(mid, st["key"])
    cost = s.mission(mid)["spent_usd"] - spent0

    def finish(**fields):
        return s.finish_step(st["id"], run_id, **fields)

    if kind == "error" and isinstance(res, meter.BudgetExceeded):
        finish(state="pending", attempts=attempt - 1, error=str(res), cost_usd=cost)
        if defer_budget_pause:  # (steps running beside it already reserved their money: they finish first)
            _budget_hit[mid] = _budget_hit.get(mid, False) or isinstance(res, meter.DailyBudgetExceeded)
        else:
            _budget_pause(mid, s.mission(mid), daily=isinstance(res, meter.DailyBudgetExceeded))
        return
    if kind == "cancelled":
        reason = res or "stopped"
        if reason == "timed out":
            res = StepResult(False, error=f"timed out after {timeout}s")
            if not st["idempotent"]:
                finish(state="failed", ended=time.time(), cost_usd=cost, error=res.error + "; not repeated (it may "
                                                                                           "have partly run)")
                s.event(mid, f"step '{st['title']}' timed out; not repeated", "error")
                return
        else:  # (paused / stopped / emergency: the step waits; a non-idempotent one isn't repeated blindly)
            if st["idempotent"]:
                finish(state="pending", attempts=attempt - 1, cost_usd=cost, error=f"{reason}; will run again")
            else:
                finish(state="failed", ended=time.time(), cost_usd=cost, error=f"{reason} while running; not repeated "
                                                                               "(it may have partly run)")
            return
    elif kind == "error":
        log.error("mission %s step %s failed: %s", mid, st["key"], res, exc_info=res)
        res = StepResult(False, error=f"{res.__class__.__name__}: {str(res)[:200]}")
    elif not isinstance(res, StepResult):
        res = StepResult(False, error="the step returned nothing")
    if res.ok and res.add:
        problems = validate_plan(wf, res.add, _existing_plan(mid))
        if problems:
            res = StepResult(False, error="its follow-up steps aren't a valid plan: " + "; ".join(problems[:3]),
                             final=True)
    if res.ok:
        problems = _verify(wf, ctx, st, res)
        if problems:
            if res.committed:  # (its change happened, but it isn't what was promised: recorded, never silently "done")
                finish(state="failed", ended=time.time(), cost_usd=cost, evidence=res.evidence[:2000],
                       error="applied, but verification failed: " + "; ".join(problems)[:400])
                s.event(mid, f"step '{st['title']}' was applied but failed its verification: {problems[0][:200]}", "error")
                return
            res = StepResult(False, error="verification failed: " + "; ".join(problems)[:400])
        else:
            c = wf.contracts.get(st["kind"])
            if c is not None and c.verify is not None and config.MISSION_VERIFY:
                res.evidence = (res.evidence + f" | verified: {c.expected}")[:2000]
    if res.ok:
        crashpoints.hit("before_db_commit")
        # the completion, the follow-up steps and the skips are ONE transaction: never a completed step without them
        if not s.complete_step(st["id"], run_id, {"state": "completed", "ended": time.time(), "result": res.result,
                                                  "evidence": res.evidence[:2000], "error": "", "cost_usd": cost},
                               adds=res.add, skip_keys=res.skip):
            if res.committed:  # (the mission was stopped meanwhile, but this step's change did happen: say so)
                s.event(mid, f"'{st['title']}' was completed just before the stop: {res.evidence[:300]}", "warn")
            return  # (stopped meanwhile: its outcome isn't recorded as the step's, its follow-up steps aren't added)
        crashpoints.hit("after_db_commit")
        return
    if st["idempotent"] and attempt < st["max_attempts"] and not res.final:
        wait = BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)]
        if not finish(state="pending", error=f"attempt {attempt} failed: {res.error}; retrying in {wait}s", cost_usd=cost):
            return
        s.event(mid, f"step '{st['title']}' failed ({res.error}); retry {attempt} in {wait}s", "warn")
        waiter = runctx.Token(mid, st["key"], should_stop=lambda: _mission_should_stop(mid, started))
        try:
            waiter.wait(wait)
        except runctx.Cancelled:
            pass
        return
    finish(state="failed", ended=time.time(), error=res.error, cost_usd=cost)
    s.event(mid, f"step '{st['title']}' failed: {res.error}", "error")


def _finish(wf, m):
    s = store()
    mid = m["id"]
    steps = s.steps(mid)
    problems = [st for st in steps if st["state"] in DEAD]
    ctx = Ctx(m, runctx.Token(mid, "summary"))
    try:
        summary = wf.summarize(ctx)
    except Exception as e:  # noqa: BLE001
        summary = f"(the summary couldn't be written: {e.__class__.__name__})"
    never = [st for st in steps if st["state"] == "pending"]  # (no runnable order exists, e.g. a dependency cycle)
    for st in never:
        s.update_step(st["id"], state="blocked", error="not run: its dependencies can never complete")
    problems += never
    row = s.goal(mid)
    if row is not None:
        from room_agent.missions import goals

        crit = goals.evaluate(mid)
        if crit:
            met = [c for c in crit if c["met"]]
            summary += "\nGoal: " + f"{len(met)} of {len(crit)} success criteria met" + "".join(
                f"; {c['id']} {c['got']}/{c['target']}" + ("" if c["met"] else " (NOT met)") for c in crit
                if c["metric"] != "sent")
            try:
                goals.remember(mid)
            except Exception as e:  # noqa: BLE001
                log.debug("goal knowledge not saved: %s", e)
    state = "finished_with_problems" if problems else "completed"
    if not s.set_state(mid, state, "all steps ended", only_from=("running",)):
        return  # (paused / stopped at the last moment)
    s.update_mission(mid, summary=summary)
    s.event(mid, f"finished ({len(steps) - len(problems)} of {len(steps)} steps done)")
    _audit("mission_finished", mid, state=state)
    _announce(f"The mission '{m['title']}' is finished. " + summary.split("\n")[0][:240], mid)


# ---------------------------------------------------------------- reconciliation while running (no restart needed)
def plan_view(mid):
    """The plan as the user should see it: each step with its contract (purpose, capability, expected output,
    verification, failure handling, estimated cost), state and evidence. No payloads."""
    s = store()
    m = s.mission(mid)
    if m is None:
        return []
    wf = WORKFLOWS.get(m["kind"])
    out = []
    for st in s.steps(mid):
        c = wf.contracts.get(st["kind"]) if wf else None
        est = None
        if c is not None and c.cost is not None:
            try:
                est = round(float(c.cost(m["params"], st["args"])), 4)
            except Exception:  # noqa: BLE001
                est = None
        out.append({"key": st["key"], "title": st["title"], "state": st["state"], "depends": st["depends"],
                    "purpose": c.purpose if c else "", "capability": c.capability if c else st["kind"],
                    "expected": c.expected if c else "", "verified_by": "code check" if c and c.verify else "",
                    "on_failure": c.on_failure if c else "", "est_usd": est, "evidence": (st["evidence"] or "")[:300],
                    "error": (st["error"] or "")[:300]})
    return out


def reconcile_now(mid=None):
    """Re-check every unresolved item with READ-ONLY checks (files, Gmail), without restarting Jarvis. Nothing is ever
    re-run or re-sent here; items that can't be decided stay as they are and appear in pending_actions(). -> [notes]"""
    from room_agent.missions import outreach

    s = store()
    notes = []
    missions = [s.mission(mid)] if mid else s.missions(200)
    for m in [x for x in missions if x]:
        wf = WORKFLOWS.get(m["kind"])
        with _control:
            for st in s.steps(m["id"]):
                if st["state"] != "uncertain" or wf is None or wf.reconcile is None:
                    continue
                try:
                    state, note = wf.reconcile(m, st)
                except Exception as e:  # noqa: BLE001
                    notes.append(f"{st['key']}: couldn't be checked ({e.__class__.__name__})")
                    continue
                if state == "completed":
                    s.update_step(st["id"], state="completed", error="", evidence=note[:2000], ended=time.time())
                    notes.append(f"'{st['title']}': completed ({note})")
                elif state == "pending":
                    # provably not applied and nothing unknown involved: it MAY run, but only when you resume / retry
                    s.update_step(st["id"], error=f"can be retried safely: {note}")
                    notes.append(f"'{st['title']}': can be retried safely ({note})")
                else:
                    notes.append(f"'{st['title']}': still uncertain ({note[:120]})")
        for a in s.approvals(m["id"], "unknown"):
            ok, msg = outreach.retry(a["id"], "reconcile")
            notes.append(f"approval #{a['id']}: {msg}")
    return notes


def pending_actions(mid):
    """What needs YOUR decision, with the safe choices for each (for the dashboard and voice). No secrets / payloads."""
    from room_agent.missions import sitegen

    s = store()
    m = s.mission(mid)
    if m is None:
        return []
    out = []
    for st in s.steps(mid):
        if st["state"] == "uncertain":
            out.append({"type": "step", "id": st["key"], "what": st["title"], "why": st["error"][:300],
                        "actions": ["retry", "accept"]})
    for a in s.approvals(mid, "unknown"):
        out.append({"type": "approval", "id": str(a["id"]), "what": a["summary"][:200], "why": (a["result"] or "")[:300],
                    "actions": ["check", "mark_created", "mark_not_created"]})
    for slug, pend in sitegen.pending_conflicts(Path(m["workspace"])):
        out.append({"type": "conflict", "id": slug, "what": f"demo site {slug}",
                    "why": "the site was edited by hand; Jarvis's newer version was set aside, not applied",
                    "actions": ["keep_mine", "use_jarvis"]})
    for c in s.charges(mid, 500):
        if c["state"] == "uncertain":
            out.append({"type": "charge", "id": str(c["id"]), "what": c["what"],
                        "why": f"counted at the full estimate ${c['actual_usd']:.4f}: the provider may or may not have "
                               "billed it (check the provider's usage page)", "actions": []})
    return out


@_controlled
def resolve(mid, kind, item, action, via="you"):
    """Carry out a choice from pending_actions(). Paid / external work never starts without it being chosen here, and
    sending-type actions still need their own approval. -> (ok, message)"""
    from room_agent.missions import outreach, sitegen

    s = store()
    m = s.mission(mid)
    if m is None:
        return False, "there's no such mission"
    if kind == "step":
        st = s.step(mid, item)
        if st is None or st["state"] != "uncertain":
            return False, "that step isn't waiting for a decision"
        if m["state"] == "cancelled":
            return False, "the mission was stopped for good"
        if action == "retry":
            n = s.supersede_step_ops(mid, item)
            s.update_step(st["id"], state="pending", run_id="", attempts=0,
                          error=f"retried by {via} (it may cost again: {n} earlier paid attempt(s) kept on record)")
        elif action == "accept":
            s.update_step(st["id"], state="skipped", error=f"accepted as is by {via}")
        else:
            return False, f"unknown action '{action}'"
        s.event(mid, f"'{st['title']}': {action} chosen by {via}")
        if s.set_state(mid, "running", f"{action} of '{st['title']}' chosen", only_from=RESTARTABLE):
            start_runner()
        state = s.mission(mid)["state"]
        return True, ("done" + ("" if state == "running" else f"; the mission is {state}: resume it to continue"))
    if kind == "approval":
        try:
            aid = int(item)
        except (TypeError, ValueError):
            return False, "bad approval number"
        if action == "check":
            return outreach.retry(aid, via)
        if action in ("mark_created", "mark_not_created"):
            return outreach.resolve(aid, "created" if action == "mark_created" else "not_created", via)
        return False, f"unknown action '{action}'"
    if kind == "conflict":
        proj = next((p for p in s.projects(mid) if p["slug"] == item), None)
        if proj is None:
            return False, "no such demo site in this mission"
        return sitegen.resolve_conflict(Path(m["workspace"]), item, action, s.lead(proj["lead_id"]))
    return False, f"unknown item type '{kind}'"


# ---------------------------------------------------------------- what's going on (from the stored state only)
def progress(mid):
    s = store()
    m = s.mission(mid)
    if m is None:
        return None
    steps = s.steps(mid)
    by_kind = {}
    for st in steps:
        k = by_kind.setdefault(st["kind"], {})
        k[st["state"]] = k.get(st["state"], 0) + 1
    running = next((st for st in steps if st["state"] == "running"), None)
    out = {"id": mid, "title": m["title"], "kind": m["kind"], "state": m["state"], "steps_total": len(steps),
           "steps_done": sum(st["state"] in DONE for st in steps), "steps_failed": sum(st["state"] in DEAD for st in steps),
           "by_kind": by_kind, "current": running["title"] if running else "", "spent_usd": round(m["spent_usd"], 4),
           "reserved_usd": round(m.get("reserved_usd") or 0.0, 4), "uncertain_usd": round(m.get("uncertain_usd") or 0.0, 4),
           "budget_usd": m["budget_usd"], "tokens_in": m["tokens_in"], "tokens_out": m["tokens_out"],
           "requests": m["requests"], "workspace": m["workspace"], "error": m["error"], "summary": m["summary"],
           "errors": [e["text"] for e in s.events(mid, 100) if e["level"] in ("warn", "error")][-5:],
           "approvals_pending": len(s.approvals(mid, "pending")),
           "approvals_unknown": len(s.approvals(mid, "unknown")),
           "abandoned_runs": sum(1 for a in _abandoned if a[2] == mid and a[0].is_alive())}
    wf = WORKFLOWS.get(m["kind"])
    if wf and wf.progress:
        try:
            out.update(wf.progress(Ctx(m, runctx.Token(mid, "progress"))))
        except Exception as e:  # noqa: BLE001
            log.debug("mission progress extra failed: %s", e)
    return out


def status_line(mid):
    p = progress(mid)
    if p is None:
        return "There's no such mission."
    wf = WORKFLOWS.get(p["kind"])
    if wf and wf.status_line:
        return wf.status_line(Ctx(store().mission(mid), runctx.Token(mid, "status")), p)
    return f"{p['title']}: {p['state']}, {p['steps_done']} of {p['steps_total']} steps done."


def latest(states=None):
    rows = store().missions(1, states=states) if states else store().missions(1)
    return rows[0] if rows else None


def _announce(text, mid):
    try:
        from room_agent import triggers

        triggers.say(text, "reminder", f"mission:{mid}:{time.time():.3f}")
    except Exception as e:  # noqa: BLE001
        log.debug("mission announcement not said: %s", e)


def _audit(kind, mid, **fields):
    try:
        from room_agent import audit

        audit.event(kind, mission=mid, **fields)
    except Exception:  # noqa: BLE001
        pass
