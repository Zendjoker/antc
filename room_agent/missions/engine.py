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
from room_agent.missions import meter, runctx
from room_agent.missions.store import GuardedStore, store

log = logging.getLogger("room-agent")
DONE = ("completed", "skipped")
DEAD = ("failed", "blocked", "cancelled")
LIVE_MISSION = ("planned", "running")
PAUSED = ("paused", "paused_budget", "interrupted")
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


WORKFLOWS = {}
_lock = threading.Lock()
_runner = {"thread": None}
_active = {}       # mission id -> the Token of the step run in progress
_abandoned = []    # (thread, token, mission id, step key): runs cancelled but not yet ended (for the dashboard)


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


def create(kind, params, budget_usd=None):
    """Plan a mission and start it in the background. -> the mission row."""
    wf = WORKFLOWS.get(kind)
    if wf is None:
        raise ValueError(f"no workflow called {kind}")
    budget = float(budget_usd if budget_usd is not None else config.MISSION_DEFAULT_BUDGET_USD)
    budget = max(0.0, min(budget, config.MISSION_MAX_BUDGET_USD))
    title = wf.describe(params)
    mid = time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]
    workspace = Path(config.MISSIONS_DIR) / f"{mid}-{_slug(title)}"
    workspace.mkdir(parents=True, exist_ok=True)
    s = store()
    m = s.create_mission(mid, kind, title, params, budget, workspace)
    for spec in wf.plan(params):
        s.add_step(mid, spec.key, spec.kind, spec.title, spec.args, spec.depends, spec.idempotent, spec.max_attempts)
    s.update_mission(mid, state="running")
    s.event(mid, f"mission planned: {title} (budget ${budget:.2f})")
    _audit("mission_started", mid, title=title, budget=budget)
    start_runner()
    return s.mission(mid)


def add_steps(mid, specs, resume=True):
    """Extend a mission's plan (e.g. 'build demos for the best three' after the research finished)."""
    s = store()
    added = sum(s.add_step(mid, x.key, x.kind, x.title, x.args, x.depends, x.idempotent, x.max_attempts) for x in specs)
    m = s.mission(mid)
    if resume and added and m and m["state"] in ("completed", "finished_with_problems") + PAUSED:
        if _headroom(m) > 0 or m["state"] != "paused_budget":
            s.update_mission(mid, state="running")
            s.event(mid, f"{added} step{'s' if added != 1 else ''} added; running again")
    if added and m and s.mission(mid)["state"] == "running":
        start_runner()
    return added


def _cancel_active(mid, why):
    t = _active.get(mid)
    if t is not None:
        t.cancel(why)


def pause(mid, why="you asked"):
    m = store().mission(mid)
    if m is None or m["state"] not in LIVE_MISSION:
        return False
    store().update_mission(mid, state="paused")
    _cancel_active(mid, f"paused ({why})")
    store().event(mid, f"paused ({why})")
    _audit("mission_paused", mid, why=why)
    return True


def _headroom(m):
    return m["budget_usd"] - m["spent_usd"] - (m.get("reserved_usd") or 0.0)


def raise_budget(mid, extra_usd, via):
    """More budget for a mission (only from an explicit request: a voice tool that always asks, or a dashboard click)."""
    s = store()
    m = s.mission(mid)
    if m is None or extra_usd <= 0:
        return None
    new = min(m["budget_usd"] + float(extra_usd), config.MISSION_MAX_BUDGET_USD)
    s.update_mission(mid, budget_usd=new)
    s.event(mid, f"budget raised to ${new:.2f} ({via})")
    _audit("mission_budget_raised", mid, budget=new, via=via)
    return new


def resume(mid):
    s = store()
    m = s.mission(mid)
    if m is None or m["state"] not in PAUSED:
        return False
    if _headroom(m) <= 0:
        s.event(mid, "not resumed: the budget is used up (raise it to continue)", "warn")
        return False
    s.update_mission(mid, state="running")
    s.event(mid, "resumed")
    _audit("mission_resumed", mid)
    start_runner()
    return True


def stop(mid, why="you asked"):
    """Stop for good: pending steps are cancelled; completed work (leads, sites, drafts) is kept."""
    s = store()
    m = s.mission(mid)
    if m is None or m["state"] in FINAL:
        return False
    s.update_mission(mid, state="cancelled")
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
    """At startup: repair everything a crash / shutdown may have left half-done. -> [notes]"""
    s = store()
    notes = []
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
    for m in s.missions(100, states=LIVE_MISSION):
        for st in s.steps(m["id"]):
            if st["state"] == "running":
                if st["idempotent"]:
                    s.update_step(st["id"], state="pending", run_id="", error="interrupted by a restart; will run again")
                else:
                    s.update_step(st["id"], state="failed", run_id="", error="interrupted by a restart while running; not "
                                                                             "repeated automatically (it may have partly "
                                                                             "happened)")
        s.update_mission(m["id"], state="interrupted")
        s.event(m["id"], "Jarvis stopped while this mission was running: paused; say 'resume the mission' to continue", "warn")
        notes.append(f"mission '{m['title']}' interrupted")
    for m in s.missions(100):
        wf = WORKFLOWS.get(m["kind"])
        if wf and wf.recover and m["state"] not in ("cancelled",):
            try:
                for note in wf.recover(m) or []:
                    s.event(m["id"], note, "warn")
                    notes.append(note)
            except Exception as e:  # noqa: BLE001
                log.warning("mission %s: recovery failed: %s", m["id"], e)
    for note in notes:
        log.info("missions at startup: %s", note)
    return notes


# ---------------------------------------------------------------- running
def start_runner():
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
            store().update_mission(mid, state="paused", error=f"{e.__class__.__name__}: {str(e)[:200]}")
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
        s.update_mission(mid, state="paused", error=f"no workflow '{m['kind']}' in this version")
        return
    s.event(mid, "running")
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
            _finish(wf, m)
            return
        _run_step(wf, m, st, started)


def _budget_pause(mid, m):
    s = store()
    s.update_mission(mid, state="paused_budget")
    s.event(mid, f"paused: the budget is used up (${m['spent_usd']:.4f} spent, ${m.get('reserved_usd') or 0:.4f} reserved, "
                 f"of ${m['budget_usd']:.2f}); raise it to continue", "warn")
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
    if token.cancelled and "r" not in out:
        return "cancelled", token.reason
    if "e" in out:
        if isinstance(out["e"], runctx.Cancelled):
            return "cancelled", token.reason or str(out["e"])
        return "error", out["e"]
    if token.cancelled:  # (finished, but after being cancelled: the result is not trusted / used)
        return "cancelled", token.reason
    return "ok", out.get("r")


def _run_step(wf, m, st, started):
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
    s.update_step(st["id"], state="running", attempts=attempt, started=time.time(), run_id=run_id)
    _active[mid] = token
    spent0 = s.mission(mid)["spent_usd"]
    ctx = Ctx(m, token)
    try:
        kind, res = _call(handler, ctx, st, token)
    finally:
        _active.pop(mid, None)
    cost = s.mission(mid)["spent_usd"] - spent0

    def finish(**fields):
        return s.finish_step(st["id"], run_id, **fields)

    if kind == "error" and isinstance(res, meter.BudgetExceeded):
        finish(state="pending", attempts=attempt - 1, error=str(res), cost_usd=cost)
        _budget_pause(mid, s.mission(mid))
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
    if res.ok:
        if not finish(state="completed", ended=time.time(), result=res.result, evidence=res.evidence[:2000], error="",
                      cost_usd=cost):
            return  # (stopped meanwhile: its outcome isn't recorded, its follow-up steps aren't added)
        for spec in res.add:
            s.add_step(mid, spec.key, spec.kind, spec.title, spec.args, spec.depends, spec.idempotent, spec.max_attempts)
        if res.skip:
            for other in s.steps(mid):
                if other["key"] in res.skip and other["state"] == "pending":
                    s.update_step(other["id"], state="skipped", error="not needed any more")
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
    state = "finished_with_problems" if problems else "completed"
    s._exec("UPDATE missions SET state=?, summary=?, updated=? WHERE id=? AND state='running'",
            (state, summary, time.time(), mid))
    if s.mission(mid)["state"] != state:
        return  # (paused / stopped at the last moment)
    s.event(mid, f"finished ({len(steps) - len(problems)} of {len(steps)} steps done)")
    _audit("mission_finished", mid, state=state)
    _announce(f"The mission '{m['title']}' is finished. " + summary.split("\n")[0][:240], mid)


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
