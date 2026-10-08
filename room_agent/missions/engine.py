"""The mission engine: long workflows run by code, in the background, checkpointed after every step.

A workflow (registered by a feature module, e.g. missions/business.py) says how to plan a mission into steps and how to
run each kind of step. The engine then:
    - runs one step at a time, in plan order, when its dependencies are done ("research:*" = every step whose key starts
      with "research:"); a step whose dependency failed / was cancelled is BLOCKED
    - lets a step add new steps (discovering 40 businesses adds 40 research steps) or skip pending ones (enough found)
    - checkpoints the mission and each step in SQLite before and after it runs
    - retries a failed step (at most its max_attempts, with backoff) only if it's idempotent (safe to repeat)
    - times a step out (STEP_TIMEOUT_S, or the workflow's per-kind value)
    - pauses / resumes / stops on request, on the PC-wide emergency stop, and BEFORE exceeding the mission's budget
    - after a restart: missions that were running come back paused ("interrupted"); a step that was running goes back to
      pending if it's idempotent, otherwise it's marked failed ("not repeated automatically") so nothing runs twice
    - reports progress from the stored state only (never from what a model believes)
Missions run one at a time, in a single background thread.
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
from room_agent.missions import meter
from room_agent.missions.net import Stopped
from room_agent.missions.store import store

log = logging.getLogger("room-agent")
DONE = ("completed", "skipped")
DEAD = ("failed", "blocked", "cancelled")
LIVE_MISSION = ("planned", "running")
PAUSED = ("paused", "paused_budget", "interrupted")
STEP_TIMEOUT_S = 300
BACKOFF_S = (5, 20, 60)


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


WORKFLOWS = {}
_flags = {}                    # mission id -> "pause" / "stop"
_lock = threading.Lock()
_runner = {"thread": None}


def register(workflow):
    WORKFLOWS[workflow.kind] = workflow
    return workflow


class Ctx:
    """What a step handler gets: the mission, its params and folder, a log, and the 'should I stop?' check."""

    def __init__(self, mission, started):
        self.mission = mission
        self.id = mission["id"]
        self.params = mission["params"]
        self.workspace = Path(mission["workspace"])
        self.store = store()
        self._started = started

    def log(self, text, level="info"):
        self.store.event(self.id, text, level)
        (log.warning if level in ("warn", "error") else log.info)("mission %s: %s", self.id, text)

    def stop_requested(self):
        if _flags.get(self.id):
            return True
        try:
            from room_agent import emergency

            if emergency.stopped_at[0] > self._started:
                return True
        except Exception:
            pass
        m = self.store.mission(self.id)
        return m is None or m["state"] != "running"


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
        if m["spent_usd"] < m["budget_usd"] or m["state"] != "paused_budget":
            _flags.pop(mid, None)
            s.update_mission(mid, state="running")
            s.event(mid, f"{added} step{'s' if added != 1 else ''} added; running again")
    if added and m and s.mission(mid)["state"] == "running":
        start_runner()
    return added


def pause(mid, why="you asked"):
    m = store().mission(mid)
    if m is None or m["state"] not in LIVE_MISSION:
        return False
    _flags[mid] = "pause"
    store().update_mission(mid, state="paused")
    store().event(mid, f"paused ({why})")
    _audit("mission_paused", mid, why=why)
    return True


def resume(mid, extra_budget_usd=0.0):
    s = store()
    m = s.mission(mid)
    if m is None or m["state"] not in PAUSED:
        return False
    if extra_budget_usd:
        new = min(m["budget_usd"] + float(extra_budget_usd), config.MISSION_MAX_BUDGET_USD)
        s.update_mission(mid, budget_usd=new)
        s.event(mid, f"budget raised to ${new:.2f}")
    m = s.mission(mid)
    if m["spent_usd"] >= m["budget_usd"]:
        s.event(mid, "not resumed: the budget is used up (raise it to continue)", "warn")
        return False
    _flags.pop(mid, None)
    s.update_mission(mid, state="running")
    s.event(mid, "resumed")
    _audit("mission_resumed", mid)
    start_runner()
    return True


def stop(mid, why="you asked"):
    """Stop for good: pending steps are cancelled; completed work (leads, sites, drafts) is kept."""
    s = store()
    m = s.mission(mid)
    if m is None or m["state"] in ("completed", "cancelled", "finished_with_problems"):
        return False
    _flags[mid] = "stop"
    for st in s.steps(mid):
        if st["state"] in ("pending", "running"):
            s.update_step(st["id"], state="cancelled", error=f"stopped: {why}")
    s.update_mission(mid, state="cancelled")
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
    """At startup: missions that were running come back paused ('interrupted'); see the module doc for steps."""
    s = store()
    cut = []
    for m in s.missions(100, states=LIVE_MISSION):
        for st in s.steps(m["id"]):
            if st["state"] == "running":
                if st["idempotent"]:
                    s.update_step(st["id"], state="pending", error="interrupted by a restart; will run again")
                else:
                    s.update_step(st["id"], state="failed", error="interrupted by a restart while running; not repeated "
                                                                  "automatically (it may have partly happened)")
        s.update_mission(m["id"], state="interrupted")
        s.event(m["id"], "Jarvis stopped while this mission was running: paused; say 'resume the mission' to continue", "warn")
        cut.append(m)
    return cut


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
        live = [m for m in store().missions(50, states=("running",))]
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
    meter.bind(mid)
    s.event(mid, "running")
    while True:
        m = s.mission(mid)
        if m is None or m["state"] != "running":
            return
        flag = _flags.pop(mid, None)
        if flag:
            return  # (pause / stop already set the state)
        ctx = Ctx(m, started)
        if ctx.stop_requested():
            pause(mid, "emergency stop")
            return
        if m["spent_usd"] >= m["budget_usd"] > 0 or (m["budget_usd"] == 0 and m["spent_usd"] > 0):
            _budget_pause(mid, m)
            return
        st = _next_step(s, mid)
        if st is None:
            _finish(wf, ctx)
            return
        _run_step(wf, ctx, st)


def _budget_pause(mid, m):
    s = store()
    s.update_mission(mid, state="paused_budget")
    s.event(mid, f"paused: the budget is used up (${m['spent_usd']:.4f} of ${m['budget_usd']:.2f}); raise it to continue",
            "warn")
    _announce(f"I paused the mission '{m['title']}': it reached its budget of ${m['budget_usd']:.2f}. Raise it if you want "
              "me to continue.", mid)


def _call(handler, ctx, st, timeout):
    """Run a step handler in its own thread (so a hung step can be timed out). -> StepResult, or None on timeout."""
    out = {}

    def go():
        meter.bind(ctx.id)
        try:
            out["r"] = handler(ctx, st)
        except BaseException as e:  # noqa: BLE001 (reported to the engine)
            out["e"] = e

    th = threading.Thread(target=go, name=f"mission-step-{st['kind']}", daemon=True)
    th.start()
    deadline = time.time() + timeout
    while th.is_alive() and time.time() < deadline:
        th.join(0.25)
        if ctx.stop_requested():
            th.join(10)  # (handlers check stop_requested often; give it a moment to end cleanly)
            break
    if "e" in out:
        raise out["e"]
    return out.get("r") if not th.is_alive() else None


def _run_step(wf, ctx, st):
    s = store()
    handler = wf.handlers.get(st["kind"])
    if handler is None:
        s.update_step(st["id"], state="failed", error=f"no handler for step kind '{st['kind']}'")
        return
    attempt = st["attempts"] + 1
    s.update_step(st["id"], state="running", attempts=attempt, started=time.time())
    spent0 = s.mission(ctx.id)["spent_usd"]
    timeout = wf.timeouts.get(st["kind"], STEP_TIMEOUT_S)
    try:
        res = _call(handler, ctx, st, timeout)
    except meter.BudgetExceeded as e:
        s.update_step(st["id"], state="pending", attempts=attempt - 1, error=str(e))
        _budget_pause(ctx.id, s.mission(ctx.id))
        return
    except Stopped:
        s.update_step(st["id"], state="pending", attempts=attempt - 1, error="stopped before it finished; will run again")
        return
    except Exception as e:  # noqa: BLE001
        log.exception("mission %s step %s failed", ctx.id, st["key"])
        res = StepResult(False, error=f"{e.__class__.__name__}: {str(e)[:200]}")
    cost = s.mission(ctx.id)["spent_usd"] - spent0
    if res is None:
        if ctx.stop_requested():
            s.update_step(st["id"], state="pending" if st["idempotent"] else "failed", attempts=attempt - 1,
                          error="stopped while running" + ("" if st["idempotent"] else "; not repeated (may have partly run)"))
            return
        res = StepResult(False, error=f"timed out after {timeout}s")
        if not st["idempotent"]:
            s.update_step(st["id"], state="failed", ended=time.time(), cost_usd=cost,
                          error=res.error + "; not repeated (it may have partly run)")
            ctx.log(f"step '{st['title']}' timed out; not repeated", "error")
            return
    if res.ok:
        s.update_step(st["id"], state="completed", ended=time.time(), result=res.result, evidence=res.evidence[:2000],
                      error="", cost_usd=cost)
        for spec in res.add:
            s.add_step(ctx.id, spec.key, spec.kind, spec.title, spec.args, spec.depends, spec.idempotent, spec.max_attempts)
        if res.skip:
            for other in s.steps(ctx.id):
                if other["key"] in res.skip and other["state"] == "pending":
                    s.update_step(other["id"], state="skipped", error="not needed any more")
        return
    if st["idempotent"] and attempt < st["max_attempts"]:
        wait = BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)]
        s.update_step(st["id"], state="pending", error=f"attempt {attempt} failed: {res.error}; retrying in {wait}s",
                      cost_usd=cost)
        ctx.log(f"step '{st['title']}' failed ({res.error}); retry {attempt} in {wait}s", "warn")
        deadline = time.time() + wait
        while time.time() < deadline and not ctx.stop_requested():
            time.sleep(0.25)
        return
    s.update_step(st["id"], state="failed", ended=time.time(), error=res.error, cost_usd=cost)
    ctx.log(f"step '{st['title']}' failed: {res.error}", "error")


def _finish(wf, ctx):
    s = store()
    steps = s.steps(ctx.id)
    problems = [st for st in steps if st["state"] in DEAD]
    try:
        summary = wf.summarize(ctx)
    except Exception as e:  # noqa: BLE001
        summary = f"(the summary couldn't be written: {e.__class__.__name__})"
    state = "finished_with_problems" if problems else "completed"
    s.update_mission(ctx.id, state=state, summary=summary)
    s.event(ctx.id, f"finished ({len(steps) - len(problems)} of {len(steps)} steps done)")
    _audit("mission_finished", ctx.id, state=state)
    _announce(f"The mission '{ctx.mission['title']}' is finished. " + summary.split("\n")[0][:240], ctx.id)


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
           "budget_usd": m["budget_usd"], "tokens_in": m["tokens_in"], "tokens_out": m["tokens_out"],
           "requests": m["requests"], "workspace": m["workspace"], "error": m["error"], "summary": m["summary"],
           "errors": [e["text"] for e in s.events(mid, 100) if e["level"] in ("warn", "error")][-5:],
           "approvals_pending": len(s.approvals(mid, "pending"))}
    wf = WORKFLOWS.get(m["kind"])
    if wf and wf.progress:
        try:
            out.update(wf.progress(Ctx(m, time.time())))
        except Exception as e:  # noqa: BLE001
            log.debug("mission progress extra failed: %s", e)
    return out


def status_line(mid):
    p = progress(mid)
    if p is None:
        return "There's no such mission."
    wf = WORKFLOWS.get(p["kind"])
    if wf and wf.status_line:
        return wf.status_line(Ctx(store().mission(mid), time.time()), p)
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
    except Exception:
        pass
