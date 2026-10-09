"""Missions, executed for real against an isolated world: the real engine, store, meter, network layer, SDK clients,
site generator, coding-worker path, outreach and voice tools, run on a temporary database and folders.

Nothing leaves this process:
    - every socket connect / DNS lookup to anything but localhost raises (a guard installed first)
    - HTTP goes to a fake router (requests.request is replaced); model SDKs are the REAL openai / anthropic clients on
      an httpx2 MockTransport (so the SDK's own error wrapping is exercised); Gmail is a fake provider
    - the coding worker's model call is scripted; Claude Code is never started
    - MISSIONS_DB / MISSIONS_DIR / spend / diagnostics are temp paths (tests/__init__.py), checked before anything runs

Covers: mission states and transitions, add-work-never-resumes, stop / resume / add races, billing classification for
model calls and Places requests, ledger transactions (concurrency, failed insert / update rollback, orphans), Places
discovery checkpoints across a budget pause and a lost page token, Gmail recipient validation and local MIME
construction, site content rules and owner-content isolation, legacy mission folders / previews / edits, and the
voice / dashboard callers of every changed function.

Run:  .venv\\Scripts\\python -m tests.test_missions
"""

import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


from tests.harness import setup_env  # noqa: E402

setup_env(MISSION_PREVIEW_PORT=str(_free_port()), MISSION_SENDER_NAME="Test Sender",
          MISSION_SENDER_EMAIL="sender@example.org", MISSION_SENDER_ADDRESS="1 Test Way, Testville, TS 00000",
          MISSION_DEFAULT_BUDGET_USD="1.00", MISSION_MAX_BUDGET_USD="10.00", DAILY_BUDGET_USD="5.00")

# ---------------------------------------------------------------- network guard (before anything else is imported)
_real_connect = socket.socket.connect
_real_getaddrinfo = socket.getaddrinfo
LOCAL = ("127.0.0.1", "::1", "localhost")


def _guard_connect(self, addr):
    if (addr[0] if isinstance(addr, tuple) else addr) not in LOCAL:
        raise OSError(f"test network guard: connection to {addr!r} blocked")
    return _real_connect(self, addr)


def _guard_getaddrinfo(host, *a, **k):
    if host not in LOCAL:
        raise socket.gaierror(f"test network guard: DNS lookup of {host!r} blocked")
    return _real_getaddrinfo(host, *a, **k)


socket.socket.connect = _guard_connect
socket.getaddrinfo = _guard_getaddrinfo

import httpx2  # noqa: E402
import openai  # noqa: E402
import requests  # noqa: E402
import urllib3.exceptions as u3  # noqa: E402
from requests.structures import CaseInsensitiveDict  # noqa: E402

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.computer import pages  # noqa: E402
from room_agent.llm.budget import budget  # noqa: E402
from room_agent.missions import (business, coder, discovery, engine, llm, meter, net, outreach, places,  # noqa: E402
                                 preview, runctx, sitegen, websites)
from room_agent.missions import store as storemod  # noqa: E402
from room_agent.missions.engine import StepResult, StepSpec, Workflow  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()

# ---------------------------------------------------------------- isolation, verified before any write
REPO_DB = (config.HERE / "missions.db").resolve()
assert Path(config.MISSIONS_DB).resolve() != REPO_DB, "refusing to run: MISSIONS_DB is the real one"
assert "jarvis-test" in str(config.MISSIONS_DB) or tempfile.gettempdir() in str(config.MISSIONS_DB), config.MISSIONS_DB
assert tempfile.gettempdir() in str(config.MISSIONS_DIR), config.MISSIONS_DIR
assert tempfile.gettempdir() in str(config.SPEND_FILE), config.SPEND_FILE
assert config.TEST_MODE, "JARVIS_TEST must be set (tests/__init__.py)"
S = storemod.store()
print(f"isolated: MISSIONS_DB={config.MISSIONS_DB}")

ANN = []
engine._announce = lambda text, mid: ANN.append((mid, text))  # (nothing is spoken)
engine.BACKOFF_S = (0.05, 0.05, 0.05)
budget.limit = 5.0
MAX_HOPS = {}


def wait_for(cond, timeout=10.0, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


def mstate(mid):
    return S.mission(mid)["state"]


def step_state(mid, key):
    return S.step(mid, key)["state"]


# ---------------------------------------------------------------- fake HTTP (requests) + public-address policy
PRIVATE_HOSTS = {"127.0.0.1", "localhost", "192.168.1.1", "10.0.0.5", "169.254.169.254"}


def fake_public(url):
    import urllib.parse

    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False, "not a web address"
    if p.hostname in PRIVATE_HOSTS:
        return False, "it points to this PC or the local network"
    return True, ""


# (the network guard's two seams: name resolution + the pinned connection. Everything else in netguard / net runs.)
from room_agent import netguard  # noqa: E402

netguard.resolve_public = lambda host, port=443, allow_private=False: (
    (None, "it points to this PC or a private network") if host in PRIVATE_HOSTS else ("93.184.216.34", ""))


class FakeResp:
    def __init__(self, status=200, body=b"", headers=None, url=""):
        self.status_code, self.url, self.encoding = status, url, "utf-8"
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.headers = CaseInsensitiveDict(headers or {})

    @property
    def is_redirect(self):
        return "location" in self.headers and self.status_code in (301, 302, 303, 307, 308)

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


HTTP = []          # (method, url, kwargs) of every request
ROUTES = []        # [(predicate(method, url, kw), handler(method, url, kw) -> FakeResp | exception)]


def fake_request(method, url, **kw):
    HTTP.append((method, url, kw))
    for pred, handler in ROUTES:
        if pred(method, url, kw):
            r = handler(method, url, kw)
            if isinstance(r, BaseException):
                raise r
            return r
    return FakeResp(404, b"not found", url=url)


netguard.http = lambda method, url, ip, **kw: fake_request(method, url, **kw)


def route(pred, handler):
    ROUTES.insert(0, (pred, handler))
    return (pred, handler)


def unroute(r):
    if r in ROUTES:
        ROUTES.remove(r)


SEARCH = {}
from room_agent.tools import web  # noqa: E402

web.search = lambda q, news=False: SEARCH.get(q, [])


def conn_error(kind):
    """A requests.ConnectionError shaped like the real one (urllib3 MaxRetryError -> reason)."""
    if kind == "dns":
        reason = u3.NameResolutionError("places.googleapis.com", None, socket.gaierror("getaddrinfo failed"))
    elif kind == "refused":
        reason = u3.NewConnectionError(None, "Failed to establish a new connection: [WinError 10061] refused")
    else:  # dropped mid-response
        reason = u3.ProtocolError("Connection aborted.", ConnectionResetError(10054, "reset"))
    return requests.ConnectionError(u3.MaxRetryError(None, "/", reason=reason))


# ---------------------------------------------------------------- real model SDKs on a mock transport
SDK = {"handler": None, "calls": 0}


def _sdk_transport(req):
    SDK["calls"] += 1
    return SDK["handler"](req)


def _ok_openai(req):
    return httpx2.Response(200, json={"id": "c1", "object": "chat.completion", "created": 1, "model": "gpt-5-mini",
                                      "choices": [{"index": 0, "finish_reason": "stop",
                                                   "message": {"role": "assistant", "content": "polished"}}],
                                      "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}})


def _ok_claude(req):
    return httpx2.Response(200, json={"id": "m1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
                                      "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                                      "usage": {"input_tokens": 1000, "output_tokens": 200}})


OAI = openai.OpenAI(api_key="sk-test-not-real", base_url="https://api.openai.test/v1",
                    http_client=httpx2.Client(transport=httpx2.MockTransport(_sdk_transport)), max_retries=0)
import anthropic  # noqa: E402

ANT = anthropic.Anthropic(api_key="sk-ant-test-not-real", base_url="https://api.anthropic.test",
                          http_client=httpx2.Client(transport=httpx2.MockTransport(_sdk_transport)), max_retries=0)
import room_agent.llm.client as llm_client  # noqa: E402
import room_agent.llm.openai_backend as oai_backend  # noqa: E402

oai_backend.openai_client = lambda: OAI
llm_client.client = lambda: ANT


# ---------------------------------------------------------------- a small test workflow for engine states
GATES = {}


def gate(mid, key):
    return GATES.setdefault((mid, key), threading.Event())


def h_block(ctx, st):
    g = gate(ctx.id, st["key"])
    while not g.is_set():
        ctx.check()
        time.sleep(0.02)
    return StepResult(True, evidence="released")


def h_quick(ctx, st):
    ctx.store.event(ctx.id, f"ran {st['key']}")
    return StepResult(True, evidence="done")


def h_commit(ctx, st):
    """Waits (without checkpoints) until released, then reports its change as committed (like a site swapped in)."""
    g = gate(ctx.id, st["key"])
    g.wait(15)
    return StepResult(True, evidence="applied", committed=True)


def h_nocommit(ctx, st):
    g = gate(ctx.id, st["key"])
    g.wait(15)
    return StepResult(True, evidence="computed only")


def h_budget(ctx, st):
    raise meter.BudgetExceeded(1.0, 1.0, 0.5, "a test call")


def h_daily(ctx, st):
    raise meter.DailyBudgetExceeded(5.0, 5.0, 0.1, "a test call (today's model budget, DAILY_BUDGET_USD)")


engine.register(Workflow(kind="t_states", title="test", plan=lambda p: [StepSpec(k, kind, k) for k, kind in p["steps"]],
                         handlers={"block": h_block, "quick": h_quick, "budget": h_budget, "daily": h_daily, "commit": h_commit,
                                   "nocommit": h_nocommit},
                         describe=lambda p: p.get("title", "test mission"), summarize=lambda ctx: "summary"))


def mission(steps, title="test mission", budget_usd=1.0):
    return engine.create("t_states", {"steps": steps, "title": title}, budget_usd)["id"]


def specs(*keys):
    return [StepSpec(k, "quick", k) for k in keys]


# =============================================================================================== 1-2. states
print("\n1-2. Mission states; adding work never resumes an inactive mission")
mid = mission([["a", "block"]])
t.check("running: the mission runs its first step", wait_for(lambda: step_state(mid, "a") == "running")
        and mstate(mid) == "running", (mstate(mid), step_state(mid, "a")))
res = engine.add_steps(mid, specs("r1"))
t.check("running: added work is reported as running", res["running"] and res["added"] == 1 and res["state"] == "running", res)

t.check("pause -> paused", engine.pause(mid, "test") and mstate(mid) == "paused")
t.check("paused: the running step goes back to pending (its run was cancelled)",
        wait_for(lambda: step_state(mid, "a") == "pending"), step_state(mid, "a"))
res = engine.add_steps(mid, specs("p1"))
time.sleep(0.6)
t.check("paused: add_steps reports queued-not-running with the reason", not res["running"] and res["state"] == "paused"
        and "paused" in res["why_not"], res)
t.check("paused: adding work did NOT resume it", mstate(mid) == "paused" and step_state(mid, "p1") == "pending",
        (mstate(mid), step_state(mid, "p1")))
t.check("resume -> running, and it continues", engine.resume(mid) and wait_for(lambda: step_state(mid, "a") == "running"))
gate(mid, "a").set()
t.check("completed: every step ran", wait_for(lambda: mstate(mid) == "completed")
        and all(st["state"] == "completed" for st in S.steps(mid)), [(st["key"], st["state"]) for st in S.steps(mid)])
res = engine.add_steps(mid, specs("c1"))
t.check("completed: added work starts a new run", res["running"] and wait_for(lambda: step_state(mid, "c1") == "completed")
        and wait_for(lambda: mstate(mid) == "completed"), (res, mstate(mid)))

# interrupted (a restart while a step runs): load() marks it, the old run can't record anything
mid = mission([["a", "block"]])
wait_for(lambda: step_state(mid, "a") == "running")
engine.load()
t.check("interrupted: load() marks the running mission interrupted", mstate(mid) == "interrupted")
t.check("interrupted: its running step is back to pending (idempotent)", step_state(mid, "a") == "pending")
gate(mid, "a").set()
time.sleep(1.5)
t.check("interrupted: the old run finishing later can't mark the step done", step_state(mid, "a") == "pending"
        and mstate(mid) == "interrupted", (step_state(mid, "a"), mstate(mid)))
res = engine.add_steps(mid, specs("i1"))
time.sleep(0.5)
t.check("interrupted: add_steps doesn't resume it", not res["running"] and mstate(mid) == "interrupted"
        and "restart" in res["why_not"], res)
t.check("interrupted: resume runs it to completion", engine.resume(mid) and wait_for(lambda: mstate(mid) == "completed"))

# budget-paused
mid = mission([["b", "budget"], ["q", "quick"]])
t.check("budget-paused: a BudgetExceeded pauses as paused_budget", wait_for(lambda: mstate(mid) == "paused_budget"),
        mstate(mid))
t.check("budget-paused: the step waits (pending, attempt not counted)", step_state(mid, "b") == "pending"
        and S.step(mid, "b")["attempts"] == 0, S.step(mid, "b"))
t.check("budget-paused: the announcement names the mission budget",
        any(m == mid and "reached its budget" in x for m, x in ANN), ANN[-2:])
res = engine.add_steps(mid, specs("bp1"))
time.sleep(0.5)
t.check("budget-paused: add_steps doesn't resume it", not res["running"] and mstate(mid) == "paused_budget"
        and "budget" in res["why_not"], res)
S.add_spend(mid, usd=1.0)
why = engine.resume_blocker(mid)
t.check("budget-paused, no headroom: resume is refused with the mission-budget reason",
        "mission budget" in why and not engine.resume(mid) and mstate(mid) == "paused_budget", why)

# daily-paused
mid = mission([["d", "daily"]])
t.check("daily-paused: DailyBudgetExceeded pauses as paused_daily", wait_for(lambda: mstate(mid) == "paused_daily"),
        mstate(mid))
t.check("daily-paused: the announcement names the DAILY budget, not the mission's",
        any(m == mid and "today's overall model budget" in x for m, x in ANN), [x for m, x in ANN if m == mid])
res = engine.add_steps(mid, specs("dp1"))
t.check("daily-paused: add_steps doesn't resume it", not res["running"] and mstate(mid) == "paused_daily"
        and "DAILY_BUDGET_USD" in res["why_not"], res)
budget.limit = 0.001
budget.record("openai", "gpt-5-mini", fresh_in=100000)  # (today's spend in the temp spend file: over the tiny limit)
why = engine.resume_blocker(mid)
t.check("daily-paused while the daily budget is used up: resume refused, daily reason, no mission-budget advice",
        "today's model budget" in why and "raise the mission budget first" not in why and not engine.resume(mid), why)
budget.limit = 5.0
t.check("daily-paused after the daily budget allows it again: resume works", engine.resume_blocker(mid) == "")

# stopped
mid = mission([["a", "block"]])
wait_for(lambda: step_state(mid, "a") == "running")
t.check("stop -> cancelled", engine.stop(mid, "test") and mstate(mid) == "cancelled")
gate(mid, "a").set()
time.sleep(0.5)
t.check("stopped: its step stays cancelled (the old run can't complete it)", step_state(mid, "a") == "cancelled")
try:
    engine.add_steps(mid, specs("s1"))
    closed = False
except engine.MissionClosed:
    closed = True
t.check("stopped: add_steps refuses new work (MissionClosed)", closed and S.step(mid, "s1") is None)
t.check("stopped: resume is refused", not engine.resume(mid) and mstate(mid) == "cancelled")

# committed vs not, when a pause lands while the step is finishing
mid = mission([["c", "commit"]], title="committed")
wait_for(lambda: step_state(mid, "c") == "running")
engine.pause(mid, "lands during bookkeeping")
time.sleep(0.3)
gate(mid, "c").set()
t.check("a COMMITTED result that finishes after a pause is recorded as completed (not re-run / not failed)",
        wait_for(lambda: step_state(mid, "c") == "completed", 15), step_state(mid, "c"))
mid = mission([["n", "nocommit"]], title="not committed")
wait_for(lambda: step_state(mid, "n") == "running")
engine.pause(mid, "lands during work")
time.sleep(0.3)
gate(mid, "n").set()
t.check("a NON-committed result that finishes after a pause isn't trusted: the step waits to run again",
        wait_for(lambda: step_state(mid, "n") == "pending", 15), step_state(mid, "n"))
engine.stop(mid, "cleanup")

# =============================================================================================== 3. races
print("\n3. Stop / resume / add-work races (concurrent threads)")
bad = []
for i in range(25):
    mid = mission([["a", "block"]], title=f"race {i}")
    wait_for(lambda: step_state(mid, "a") == "running")
    engine.pause(mid, "race setup")
    wait_for(lambda: step_state(mid, "a") == "pending")
    barrier = threading.Barrier(12)
    outcomes = {"stop": [], "resume": [], "add": []}

    def do(kind, n, mid=mid, barrier=barrier, outcomes=outcomes):
        barrier.wait()
        try:
            if kind == "stop":
                outcomes["stop"].append(engine.stop(mid, "race"))
            elif kind == "resume":
                outcomes["resume"].append(engine.resume(mid))
            else:
                outcomes["add"].append(engine.add_steps(mid, specs(f"x{n}"))["added"])
        except engine.MissionClosed:
            outcomes["add"].append("closed")

    ths = [threading.Thread(target=do, args=(k, n)) for n, k in enumerate(["stop"] * 4 + ["resume"] * 4 + ["add"] * 4)]
    [th.start() for th in ths]
    [th.join(10) for th in ths]
    gate(mid, "a").set()
    time.sleep(0.4)
    st_ = mstate(mid)
    live = [s for s in S.steps(mid) if s["state"] in ("pending", "running")]
    done_after = [s for s in S.steps(mid) if s["state"] == "completed"]
    if any(outcomes["stop"]) and (st_ != "cancelled" or live):
        bad.append((i, st_, [(s["key"], s["state"]) for s in live], outcomes))
    if st_ == "cancelled" and done_after and not any(outcomes["resume"]):
        bad.append((i, "completed after cancel without resume", [s["key"] for s in done_after]))
t.check("25 stop/resume/add races: once stopped, the mission is cancelled with no pending / running steps left",
        not bad, bad[:3])

# deterministic: a stop arriving while add_steps is inserting waits for it, then cancels what it added
mid = mission([["a", "block"]], title="add vs stop")
wait_for(lambda: step_state(mid, "a") == "running")
engine.pause(mid, "setup")
wait_for(lambda: step_state(mid, "a") == "pending")
orig_add = S.add_step
stopper = {}


def add_then_stop(*a, **k):
    th = threading.Thread(target=lambda: stopper.update(r=engine.stop(mid, "mid-insert")))
    th.start()
    stopper["th"] = th
    time.sleep(0.3)  # (the stop is now waiting for the control lock)
    return orig_add(*a, **k)


S.add_step = add_then_stop
res = engine.add_steps(mid, specs("during"))
S.add_step = orig_add
stopper["th"].join(5)
t.check("add_steps vs a concurrent stop: the stop waits, then also cancels the step that was just added",
        res["added"] == 1 and stopper.get("r") and mstate(mid) == "cancelled" and step_state(mid, "during") == "cancelled",
        (res, stopper.get("r"), mstate(mid), step_state(mid, "during")))
gate(mid, "a").set()

# deterministic: a runner that read a step as pending can't claim it once it's cancelled / the mission isn't running
mid = mission([["a", "block"], ["b", "quick"]], title="claim")
wait_for(lambda: step_state(mid, "a") == "running")
engine.pause(mid, "setup")
wait_for(lambda: step_state(mid, "a") == "pending")
sid_b = S.step(mid, "b")["id"]
t.check("claim_step: a pending step of a PAUSED mission can't be claimed", not S.claim_step(sid_b, mid, "r1", 1)
        and step_state(mid, "b") == "pending")
engine.stop(mid, "test")
t.check("claim_step: a step of a STOPPED mission can't be claimed (stays cancelled)", not S.claim_step(sid_b, mid, "r2", 1)
        and step_state(mid, "b") == "cancelled")
gate(mid, "a").set()

# =============================================================================================== 4. billing
print("\n4. Billing classification (real SDKs on a mock transport; requests faked)")
BILL = engine.create("t_states", {"steps": [], "title": "billing"}, 5.0)["id"]
wait_for(lambda: mstate(BILL) == "completed")


def last_charge(mid=BILL):
    rows = S.charges(mid, 1)
    return rows[0] if rows else None


def run_bound(fn, token=None):
    """Run fn on a thread bound to the billing mission and a token. -> (result or exception, charge row or None)"""
    out = {}
    before = (S.charges(BILL, 1) or [{"id": 0}])[0]["id"]

    def go():
        meter.bind(BILL, "billing-test")
        runctx.bind(token or runctx.Token(BILL, "billing-test"))
        try:
            out["r"] = fn()
        except BaseException as e:  # noqa: BLE001
            out["r"] = e
    th = threading.Thread(target=go)
    th.start()
    th.join(30)
    row = last_charge()
    return out.get("r"), (row if row and row["id"] != before else None)


def totals():
    m = S.mission(BILL)
    return round(m["spent_usd"], 6), round(m["reserved_usd"], 6), round(m["uncertain_usd"], 6)


def sdk(handler, provider="openai"):
    SDK["handler"] = handler
    config.OPENAI_KEY = "sk-test-not-real" if provider == "openai" else ""
    return lambda: llm.complete("hello " * 50, tier="light", max_tokens=100, what="billing test")


def raise_(exc_factory):
    def h(req):
        raise exc_factory(req)
    return h


# a) stopped before sending: token cancelled before the call -> no reservation at all
tok = runctx.Token(BILL, "x")
tok.cancel("stopped")
calls0 = SDK["calls"]
r, ch = run_bound(sdk(_ok_openai), tok)
t.check("stopped before sending (before reserving): CancelledBeforeSend, nothing reserved, nothing sent",
        isinstance(r, runctx.CancelledBeforeSend) and ch is None and SDK["calls"] == calls0, (r, ch))

# b) stopped after the reservation, right before sending -> reservation released
orig_reserve = S.reserve
tok = runctx.Token(BILL, "x")


def reserve_then_stop(*a, **k):
    cid = orig_reserve(*a, **k)
    tok.cancel("paused")
    return cid


S.reserve = reserve_then_stop
r, ch = run_bound(sdk(_ok_openai), tok)
S.reserve = orig_reserve
t.check("stopped after reserving, before sending: released, $0, nothing sent",
        isinstance(r, runctx.CancelledBeforeSend) and ch and ch["state"] == "released" and ch["actual_usd"] == 0
        and SDK["calls"] == calls0, (r, ch and dict(ch)))

cases = [
    ("DNS failure (SDK: APIConnectionError from httpx2.ConnectError)",
     raise_(lambda req: httpx2.ConnectError("[Errno 11001] getaddrinfo failed", request=req)), "released"),
    ("connection refused (SDK: APIConnectionError from httpx2.ConnectError)",
     raise_(lambda req: httpx2.ConnectError("[WinError 10061] refused", request=req)), "released"),
    ("connect timeout (SDK: APITimeoutError from httpx2.ConnectTimeout)",
     raise_(lambda req: httpx2.ConnectTimeout("connect timed out", request=req)), "released"),
    ("read timeout after sending (SDK: APITimeoutError from httpx2.ReadTimeout)",
     raise_(lambda req: httpx2.ReadTimeout("read timed out", request=req)), "uncertain"),
    ("connection dropped mid-response (httpx2.RemoteProtocolError)",
     raise_(lambda req: httpx2.RemoteProtocolError("peer closed connection", request=req)), "uncertain"),
    ("provider 400 bad request", lambda req: httpx2.Response(400, json={"error": {"message": "bad input"}}), "released"),
    ("provider 429 rate limit", lambda req: httpx2.Response(429, json={"error": {"message": "slow down"}}), "released"),
    ("provider 500 server error", lambda req: httpx2.Response(500, json={"error": {"message": "oops"}}), "uncertain"),
]
for name, handler, want in cases:
    s0 = totals()
    r, ch = run_bound(sdk(handler))
    s1 = totals()
    est = ch["estimate_usd"] if ch else 0
    ok = ch is not None and ch["state"] == want and isinstance(r, Exception)
    if want == "released":
        ok = ok and ch["actual_usd"] == 0 and abs(s1[0] - s0[0]) < 1e-9
    else:
        ok = ok and abs(ch["actual_usd"] - est) < 1e-9 and abs(s1[0] - s0[0] - est) < 1e-9 and abs(s1[2] - s0[2] - est) < 1e-9
    t.check(f"model: {name} -> {want}", ok and s1[1] == 0, (type(r).__name__, ch and dict(ch), s0, s1))

s0 = totals()
r, ch = run_bound(sdk(_ok_openai))
expected = (1000 * 0.25 + 200 * 2.00) / 1e6  # (gpt-5-mini prices from llm/budget.py)
t.check("model: success -> settled at the provider's usage", r == "polished" and ch and ch["state"] == "settled"
        and abs(ch["actual_usd"] - expected) < 1e-9 and ch["tokens_in"] == 1000 and ch["tokens_out"] == 200
        and abs(totals()[0] - s0[0] - expected) < 1e-9, (r, ch and dict(ch)))
r, ch = run_bound(sdk(_ok_claude, "claude"))
t.check("model (Anthropic SDK): success -> settled", r == "ok" and ch and ch["state"] == "settled" and ch["actual_usd"] > 0,
        (r, ch and dict(ch)))
r, ch = run_bound(sdk(raise_(lambda req: httpx2.ConnectError("refused", request=req)), "claude"))
t.check("model (Anthropic SDK): connection refused -> released", ch and ch["state"] == "released", ch and dict(ch))
config.OPENAI_KEY = "sk-test-not-real"

# Places (requests through missions/net.py)
config.GOOGLE_PLACES_API_KEY = "test-places-key-not-real"
PL = places.SEARCH_URL


def places_call():
    return places._request("POST", PL, {"textQuery": "x"}, "places.id", config.PLACES_COST_PER_REQUEST, "places test")


pl_cases = [
    ("DNS failure", lambda m, u, k: conn_error("dns"), "released"),
    ("connection refused", lambda m, u, k: conn_error("refused"), "released"),
    ("connect timeout", lambda m, u, k: requests.exceptions.ConnectTimeout("connect timed out"), "released"),
    ("read timeout after sending", lambda m, u, k: requests.exceptions.ReadTimeout("read timed out"), "uncertain"),
    ("connection dropped mid-response", lambda m, u, k: conn_error("dropped"), "uncertain"),
    ("provider 403", lambda m, u, k: FakeResp(403, b'{"error": "denied"}', url=u), "released"),
    ("provider 500", lambda m, u, k: FakeResp(500, b'{"error": "oops"}', url=u), "uncertain"),
]
for name, handler, want in pl_cases:
    rr = route(lambda m, u, k: u == PL, handler)
    s0 = totals()
    r, ch = run_bound(places_call)
    s1 = totals()
    unroute(rr)
    ok = ch is not None and ch["state"] == want and isinstance(r, places.PlacesError)
    if want == "released":
        ok = ok and ch["actual_usd"] == 0 and abs(s1[0] - s0[0]) < 1e-9
    else:
        ok = ok and abs(ch["actual_usd"] - config.PLACES_COST_PER_REQUEST) < 1e-9
    t.check(f"Places: {name} -> {want}", ok and s1[1] == 0, (type(r).__name__, str(r)[:80], ch and dict(ch)))

rr = route(lambda m, u, k: u == PL, lambda m, u, k: FakeResp(200, b'{"places": []}', url=u))
r, ch = run_bound(places_call)
t.check("Places: success -> settled at the configured price", isinstance(r, dict) and ch and ch["state"] == "settled"
        and abs(ch["actual_usd"] - config.PLACES_COST_PER_REQUEST) < 1e-9, (r, ch and dict(ch)))
tok = runctx.Token(BILL, "x")
S.reserve = reserve_then_stop
n0 = len(HTTP)
r, ch = run_bound(places_call, tok)
S.reserve = orig_reserve
unroute(rr)
t.check("Places: stopped after reserving, before sending -> released, no request made",
        isinstance(r, runctx.CancelledBeforeSend) and ch and ch["state"] == "released" and len(HTTP) == n0,
        (type(r).__name__, ch and dict(ch)))

# Claude Code worker: preflight before reserving; release only on evidence nothing was sent
from room_agent.missions import sandbox as sbx  # noqa: E402

CLI = {"runs": [], "script": []}
orig_which, orig_bash, orig_run = coder.shutil.which, sbx.git_bash, sbx.run
coder.shutil.which = lambda name, *a, **k: "C:/fake/claude.exe" if name == config.CODER_CLI else orig_which(name, *a, **k)


def fake_run(cmd, sandbox_dir, env_vars, timeout_s, stdin_text=None):
    CLI["runs"].append((cmd, env_vars, stdin_text))
    return CLI["script"].pop(0)


sbx.run = fake_run


def cli_case(script, bash="C:/fake/Git/bin/bash.exe"):
    CLI["runs"].clear()
    CLI["script"][:] = script
    sbx.git_bash = lambda: bash
    sb = sbx.make({"index.html": b"<html></html>"})
    try:
        return run_bound(lambda: coder._run_cli(sb, "make the title bigger"))
    finally:
        sbx.remove(sb)


r, ch = cli_case([], bash=None)
t.check("Claude Code without Git Bash: refused before anything runs or is reserved",
        isinstance(r, coder.CoderUnavailable) and ch is None and not CLI["runs"], (r, ch))
r, ch = cli_case([(1, "", "Claude Code on Windows requires git-bash")])
t.check("Claude Code preflight fails: refused, no reservation, only the free --version ran",
        isinstance(r, coder.CoderUnavailable) and ch is None and len(CLI["runs"]) == 1
        and CLI["runs"][0][0][-1] == "--version", (r, ch, [c[0][-1] for c in CLI["runs"]]))
t.check("the worker's environment carries CLAUDE_CODE_GIT_BASH_PATH but no other secrets",
        CLI["runs"][0][1].get("CLAUDE_CODE_GIT_BASH_PATH") == "C:/fake/Git/bin/bash.exe"
        and not any(k for k in CLI["runs"][0][1] if "OPENAI" in k or "DEEPGRAM" in k or "ELEVEN" in k),
        sorted(CLI["runs"][0][1]))
r, ch = cli_case([(0, "2.0.0 (Claude Code)", ""), (1, "", "error: unknown option '--strict-mcp-config'")])
t.check("Claude Code exits with its own option error (no request made): reservation released",
        isinstance(r, RuntimeError) and ch and ch["state"] == "released" and ch["actual_usd"] == 0, (r, ch and dict(ch)))
r, ch = cli_case([(0, "2.0.0 (Claude Code)", ""), (1, "", "API Error: 500 Internal Server Error")])
t.check("Claude Code fails after starting, no cost reported: counted at the full reservation (uncertain)",
        isinstance(r, RuntimeError) and ch and ch["state"] == "uncertain"
        and abs(ch["actual_usd"] - config.CODER_MAX_USD) < 1e-9, (r, ch and dict(ch)))
r, ch = cli_case([(0, "2.0.0 (Claude Code)", ""),
                  (0, json.dumps({"result": "done", "total_cost_usd": 0.1234,
                                  "usage": {"input_tokens": 5000, "output_tokens": 800}}), "")])
t.check("Claude Code succeeds and reports its cost: settled at that cost", r == "done" and ch and ch["state"] == "settled"
        and abs(ch["actual_usd"] - 0.1234) < 1e-9 and ch["tokens_in"] == 5000, (r, ch and dict(ch)))
coder.shutil.which, sbx.git_bash, sbx.run = orig_which, orig_bash, orig_run

# =============================================================================================== 5. ledger transactions
print("\n5. Budget reservations and settlements: concurrency and rollback")
LED = engine.create("t_states", {"steps": [], "title": "ledger"}, 1.0)["id"]
wait_for(lambda: mstate(LED) == "completed")
got = []
barrier = threading.Barrier(40)


def grab():
    barrier.wait()
    got.append(S.reserve(LED, 0.1, "concurrent"))


ths = [threading.Thread(target=grab) for _ in range(40)]
[th.start() for th in ths]
[th.join(10) for th in ths]
ok_ids = [c for c in got if c]
m = S.mission(LED)
t.check("40 concurrent reservations of $0.10 against $1.00: exactly 10 granted, reserved = $1.00",
        len(ok_ids) == 10 and abs(m["reserved_usd"] - 1.0) < 1e-9, (len(ok_ids), m["reserved_usd"]))
for c in ok_ids:
    S.settle(c, 0.05, 10, 5)
m = S.mission(LED)
ledger = sum(r["actual_usd"] for r in S.charges(LED, 100))
t.check("settling all: reserved back to 0, spent = ledger total", abs(m["reserved_usd"]) < 1e-9
        and abs(m["spent_usd"] - 0.5) < 1e-9 and abs(ledger - 0.5) < 1e-9, (m["reserved_usd"], m["spent_usd"], ledger))
t.check("settling twice can't double-count", S.settle(ok_ids[0], 0.05) is None and abs(S.mission(LED)["spent_usd"] - 0.5) < 1e-9)

with S._lock:
    S.db.execute("CREATE TEMP TRIGGER fail_charge BEFORE INSERT ON charges BEGIN SELECT RAISE(ABORT, 'test: insert fails'); END")
    S.db.commit()
before_rows, before_res = len(S.charges(LED, 1000)), S.mission(LED)["reserved_usd"]
try:
    S.reserve(LED, 0.1, "failing insert")
    raised = False
except Exception:
    raised = True
with S._lock:
    S.db.execute("DROP TRIGGER fail_charge")
    S.db.commit()
S.event(LED, "an unrelated write after the failure")  # (would have committed a leaked half-transaction)
t.check("failed ledger insert: reserve raises and nothing is left reserved",
        raised and len(S.charges(LED, 1000)) == before_rows and abs(S.mission(LED)["reserved_usd"] - before_res) < 1e-9,
        (raised, S.mission(LED)["reserved_usd"]))
cid = S.reserve(LED, 0.1, "settle fails")
with S._lock:
    S.db.execute("CREATE TEMP TRIGGER fail_settle BEFORE UPDATE OF spent_usd ON missions BEGIN SELECT RAISE(ABORT, 'test'); END")
    S.db.commit()
try:
    S.settle(cid, 0.07)
    raised = False
except Exception:
    raised = True
with S._lock:
    S.db.execute("DROP TRIGGER fail_settle")
    S.db.commit()
row = [r for r in S.charges(LED, 1000) if r["id"] == cid][0]
t.check("failed settle: rolled back (the charge is still reserved, totals unchanged)",
        raised and row["state"] == "reserved" and abs(S.mission(LED)["reserved_usd"] - 0.1) < 1e-9, (raised, dict(row)))
n = meter.settle_orphans()
row = [r for r in S.charges(LED, 1000) if r["id"] == cid][0]
t.check("orphaned reservation at startup: counted at its full estimate as uncertain",
        n >= 1 and row["state"] == "uncertain" and abs(row["actual_usd"] - 0.1) < 1e-9
        and abs(S.mission(LED)["reserved_usd"]) < 1e-9, (n, dict(row)))

# =============================================================================================== 6. Places discovery
print("\n6. Google Places discovery: checkpoints across a budget pause, a lost page token, recovery")
SF = [{"lat": "37.7749", "lon": "-122.4194", "display_name": "San Francisco, California", "boundingbox": ["1", "2", "3", "4"],
       "osm_type": "relation", "osm_id": 111968, "address": {"city": "San Francisco"}}]
OSM = {"elements": [
    {"type": "node", "id": 1, "lat": 37.7750, "lon": -122.4195,
     "tags": {"name": "Luigi's Trattoria", "amenity": "restaurant", "phone": "+1 415 555 0101", "addr:housenumber": "12",
              "addr:street": "Main St", "addr:city": "San Francisco"}},
    {"type": "node", "id": 2, "lat": 37.7760, "lon": -122.4180,
     "tags": {"name": "Golden Wok", "amenity": "restaurant", "addr:housenumber": "40", "addr:street": "Pine St"}}]}
PAGE1 = {"places": [
    {"id": "ChIJmatchLuigi0001", "displayName": {"text": "Luigi's Trattoria"}, "nationalPhoneNumber": "(415) 555-0101",
     "formattedAddress": "12 Main St, San Francisco", "location": {"latitude": 37.7750, "longitude": -122.4195},
     "businessStatus": "OPERATIONAL"},
    {"id": "ChIJgoogleOnly0002", "displayName": {"text": "Secret Noodle Bar"}, "nationalPhoneNumber": "(415) 555-0199",
     "formattedAddress": "99 Hidden Ln, San Francisco", "location": {"latitude": 37.78, "longitude": -122.41},
     "businessStatus": "OPERATIONAL"}], "nextPageToken": "tok-page-2"}
PAGE2 = {"places": [
    {"id": "ChIJgoogleOnly0003", "displayName": {"text": "Page Two Cafe"}, "formattedAddress": "5 Second St",
     "location": {"latitude": 37.79, "longitude": -122.40}, "businessStatus": "OPERATIONAL"}]}
DETAILS = {"ChIJgoogleOnly0002": PAGE1["places"][1], "ChIJgoogleOnly0003": PAGE2["places"][0]}


def places_search_handler(m, u, k):
    body = k.get("json") or {}
    return FakeResp(200, json.dumps(PAGE2 if body.get("pageToken") == "tok-page-2" else PAGE1), url=u)


R_NOM = route(lambda m, u, k: "nominatim.openstreetmap.org" in u, lambda m, u, k: FakeResp(200, json.dumps(SF), url=u))
R_OVR = route(lambda m, u, k: "overpass" in u, lambda m, u, k: FakeResp(200, json.dumps(OSM), url=u))
R_PLS = route(lambda m, u, k: u == places.SEARCH_URL, places_search_handler)
R_DET = route(lambda m, u, k: u.startswith("https://places.googleapis.com/v1/places/") and m == "GET",
              lambda m, u, k: FakeResp(200, json.dumps(DETAILS[u.rsplit("/", 1)[1]]), url=u))


def search_requests():
    pages_ = [(k.get("json") or {}).get("pageToken") for m, u, k in HTTP if u == places.SEARCH_URL]
    return pages_.count(None), pages_.count("tok-page-2")


def discovery_mission(budget_usd):
    return business.start({"category": "restaurants", "location": "San Francisco", "count": 2, "demos": 0,
                           "outreach": False, "use_places": True}, budget_usd)["id"]


HTTP.clear()
D1 = discovery_mission(0.05)  # (one $0.035 page fits; the second doesn't)
t.check("discovery: the second paid page doesn't fit -> paused_budget", wait_for(lambda: mstate(D1) == "paused_budget", 20),
        mstate(D1))
cp = S.cache_get(f"discover:{D1}", 10 ** 9) or {}
leads = S.leads(mission_id=D1, limit=100)
names = sorted(x["name"] for x in leads)
t.check("checkpoint: OSM done, 1 Places page, not complete", cp.get("osm_done") and cp.get("places_pages") == 1
        and not cp.get("places_complete") and not cp.get("done"), cp)
t.check("page 1's businesses were stored before the pause (OSM 2 + 1 Google-only)", len(leads) == 3
        and any(n.startswith("Google place ChIJgoogle") for n in names), names)
t.check("Google content isn't stored: the Google-only lead has a placeholder name, no phone / address",
        all(not (x["name"] == "Secret Noodle Bar" or x["phone"] == "(415) 555-0199" or "Hidden Ln" in x["address"])
            for x in leads), [(x["name"], x["phone"], x["address"]) for x in leads])
match = [x for x in leads if x["place_id"] == "ChIJmatchLuigi0001"]
t.check("the Google result matching an OSM business merged into it (OSM data + place id)",
        len(match) == 1 and match[0]["key"] == "osm:node/1" and match[0]["phone"] == "+1 415 555 0101", match)
t.check("page 1 was bought once", search_requests() == (1, 0), search_requests())
engine.raise_budget(D1, 1.0, "test")
t.check("resume after raising the budget", engine.resume(D1))
t.check("the resumed mission finishes", wait_for(lambda: mstate(D1) in ("completed", "finished_with_problems"), 30), mstate(D1))
t.check("resume continued from the page token: page 1 NOT bought again, page 2 bought once",
        search_requests() == (1, 1), search_requests())
cp = S.cache_get(f"discover:{D1}", 10 ** 9) or {}
t.check("checkpoint is done", cp.get("done") is True, cp)
leads = S.leads(mission_id=D1, limit=100)
t.check("no duplicate leads after the resume", len(leads) == len({x["key"] for x in leads}) == 4, [x["key"] for x in leads])
charges = [c for c in S.charges(D1, 100) if c["what"] == "Google Places search"]
t.check("ledger: exactly two Places searches charged", len([c for c in charges if c["state"] == "settled"]) == 2,
        [(c["state"], c["actual_usd"]) for c in charges])

# lost page token (restart / >2 min): never re-buy page 1
HTTP.clear()
business.FRESH_S = 0  # (D2 finds the same businesses as D1: research them again instead of reusing D1's results)
D2 = discovery_mission(0.05)
wait_for(lambda: mstate(D2) == "paused_budget", 20)
with places._lock:
    places._next_tokens.clear()
places.forget_all()  # (as after a restart: the in-memory Google content is gone too)
engine.raise_budget(D2, 1.0, "test")
engine.resume(D2)
t.check("lost token: the mission still finishes", wait_for(lambda: mstate(D2) in ("completed", "finished_with_problems"), 30),
        mstate(D2))
t.check("lost token: page 1 NOT bought again, page 2 not bought (can't continue without paying twice)",
        search_requests() == (1, 0), search_requests())
t.check("lost token: the reason is logged", any("can't be continued" in e["text"] for e in S.events(D2, 100)))
det = [u for m, u, k in HTTP if m == "GET" and "/v1/places/" in u]
t.check("lost memory: the Google-only lead's details were re-fetched once (Place Details, budgeted)",
        len(det) == 1 and any(c["what"] == "Google Place Details" and c["state"] == "settled" for c in S.charges(D2, 100)),
        (det, [(c["what"], c["state"]) for c in S.charges(D2, 100)]))
for r_ in (R_NOM, R_OVR, R_PLS, R_DET):
    unroute(r_)

# =============================================================================================== 7. Gmail
print("\n7. Gmail drafts: recipient validation and local MIME construction (nothing is sent)")
GM = {"drafts": [], "fail": None}


class FakeGmail:
    def __init__(self, provider, account=None):
        pass

    def create_draft(self, to, subject, body, reply_to=None, message_id="", marker=""):
        if GM["fail"]:
            raise GM["fail"]
        GM["drafts"].append((to, subject, body))
        GM.setdefault("ids", {})[message_id] = f"d{len(GM['drafts'])}"
        GM.setdefault("markers", {})[marker] = f"d{len(GM['drafts'])}"
        return {"id": f"d{len(GM['drafts'])}", "to": to, "subject": subject, "body": body}

    def find_draft(self, message_id="", marker="", scan_limit=300):
        hit = GM.get("ids", {}).get(message_id) or GM.get("markers", {}).get(marker)
        return {"found": hit, "complete": True, "scanned": len(GM["drafts"]), "how": "fake"}


import room_agent.integrations as integ  # noqa: E402
import room_agent.integrations.google.gmail as gmailmod  # noqa: E402

integ.provider = lambda pid: NS(can=lambda service, level: True)
gmailmod.GmailService = FakeGmail
emails = websites._emails('<a href="mailto:info@shop.example,owner@shop.example">x</a>'
                          '<a href="mailto:evil@shop.example%0D%0ABcc:spy@evil.example">y</a>'
                          '<a href="mailto:Bad Address@shop.example">z</a> contact: hello@shop.example', "shop.example")
t.check("scraped emails: mailto lists split, each a single valid address; CRLF / spaces rejected",
        sorted(emails) == ["hello@shop.example", "info@shop.example", "owner@shop.example"], emails)
for bad_ in ("a@b.example,c@d.example", "x@y.example\r\nBcc: z@w.example", "Name <a@b.example>", "a..b@c.example",
             "noat.example", "a@b", "a@b.example;c@d.example"):
    t.check(f"valid_email rejects {bad_!r}", websites.valid_email(bad_) == "")
t.check("valid_email accepts a plain address", websites.valid_email(" Info@Shop.Example ") == "info@shop.example")

GMAIL_MID = engine.create("t_states", {"steps": [], "title": "gmail"}, 1.0)["id"]
lid, _ = S.upsert_lead({"key": "osm:node/900", "name": "Draft Diner", "category": "restaurant", "city": "Testville",
                        "email": "x@y.example\r\nBcc: z@w.example", "sources": ["https://www.openstreetmap.org/node/900"]},
                       GMAIL_MID)
lead = S.lead(lid)
ws = Path(config.MISSIONS_DIR) / "gmail-ws"
out, problems = outreach.prepare(lead, GMAIL_MID, ws, has_demo=False)
t.check("prepare: an invalid scraped email is not used as the recipient, and no approval is queued",
        out["approval_id"] is None and any("isn't one valid address" in p for p in problems), (out, problems))
S.update_lead(lid, email="owner@draftdiner.example")
lead = S.lead(lid)
out, problems = outreach.prepare(lead, GMAIL_MID, ws, has_demo=False)
aid = out["approval_id"]
t.check("prepare: a valid email -> one approval", aid is not None)
out2, _ = outreach.prepare(lead, GMAIL_MID, ws, has_demo=False)
t.check("prepare again (resumed step): same draft, same approval, no duplicate",
        out2["approval_id"] == aid and len(S.outreach(GMAIL_MID)) == 1, (out2, len(S.outreach(GMAIL_MID))))
oid = out["outreach_id"]
S._exec("UPDATE outreach SET recipient=? WHERE id=?", ("a@b.example,c@d.example", oid))
ok, msg = outreach.approve(aid, "test")
t.check("approve: a multi-recipient value is rejected locally; nothing claimed or sent",
        not ok and "exactly one valid email" in msg and S.approval(aid)["status"] == "pending" and not GM["drafts"], msg)
S._exec("UPDATE outreach SET recipient=?, subject=? WHERE id=?", ("owner@draftdiner.example", "Hi\r\nBcc: x@y.example", oid))
ok, msg = outreach.approve(aid, "test")
t.check("approve: a header-injection subject fails the LOCAL MIME build; nothing claimed or sent",
        not ok and "can't be built" in msg and S.approval(aid)["status"] == "pending" and not GM["drafts"], msg)
S._exec("UPDATE outreach SET subject=? WHERE id=?", ("A website idea for Draft Diner", oid))
from room_agent.integrations.base import NotConnected, Unavailable  # noqa: E402

GM["fail"] = NotConnected("Google", "test")
ok, msg = outreach.approve(aid, "test")
t.check("approve: Gmail not connected (provably nothing created) -> back to pending",
        not ok and S.approval(aid)["status"] == "pending" and S.outreach(GMAIL_MID)[0]["status"] == "draft", msg)
GM["fail"] = Unavailable("Google", "ConnectionError")
ok, msg = outreach.approve(aid, "test")
t.check("approve: a network failure during the call -> UNKNOWN (may exist), never auto-retried",
        not ok and S.approval(aid)["status"] == "unknown" and S.outreach(GMAIL_MID)[0]["status"] == "unknown", msg)
ok, msg = outreach.approve(aid, "test")
t.check("approve again while unknown: refused", not ok and S.approval(aid)["status"] == "unknown")
GM["fail"] = None
ok, msg = outreach.retry(aid, "test")
t.check("retry right away: Gmail is only checked (read-only); too early to conclude 'absent', so nothing is created",
        not ok and S.approval(aid)["status"] == "unknown" and not GM["drafts"] and ("min" in msg), msg)
ok, msg = outreach.resolve(aid, "not_created", "test")
t.check("manual resolution 'it's not in Gmail' -> waiting for approval again (no draft created by the resolution)",
        ok and S.approval(aid)["status"] == "pending" and not GM["drafts"], msg)
ok, msg = outreach.approve(aid, "test")
t.check("a new explicit approve creates the draft exactly once, read back, never sent",
        ok and S.approval(aid)["status"] == "done" and len(GM["drafts"]) == 1
        and GM["drafts"][0][0] == "owner@draftdiner.example" and S.outreach(GMAIL_MID)[0]["status"] == "gmail_draft", msg)
t.check("approve after done: refused (no second draft)", not outreach.approve(aid, "test")[0] and len(GM["drafts"]) == 1)
claims = []
aid2 = S.add_approval(GMAIL_MID, lid, "gmail_draft", "race", {"outreach_id": 99999})
bar = threading.Barrier(8)


def claimer():
    bar.wait()
    claims.append(S.claim_approval(aid2, "race"))


ths = [threading.Thread(target=claimer) for _ in range(8)]
[th.start() for th in ths]
[th.join(5) for th in ths]
t.check("8 simultaneous claims of one approval: exactly one wins", claims.count(True) == 1, claims)

# =============================================================================================== 8. site content
print("\n8. Website content rules and owner-content isolation")
SITE_MID = engine.create("t_states", {"steps": [], "title": "sites"}, 1.0)["id"]
SWS = Path(S.mission(SITE_MID)["workspace"])
lid, _ = S.upsert_lead({"key": "osm:node/901", "name": "Top Rated Plumbing #1", "category": "plumber",
                        "address": "12 Main St #1, Springfield", "city": "Springfield", "phone": "+1 555 0100",
                        "sources": ["https://www.openstreetmap.org/node/901"]}, SITE_MID)
plumber = S.lead(lid)
folder, problems = sitegen.build(plumber, SWS)
t.check("a sourced name / address with 'Top Rated' and '#1' builds (facts aren't claims)", not problems, problems)
page = (folder / "index.html").read_text(encoding="utf-8")
t.check("...and the facts are on the page", "Top Rated Plumbing #1" in page and "12 Main St #1" in page)
bad_page = page.replace("</main>", "<p>Rated 5 stars by our happy customers! Testimonials: amazing.</p></main>")
(folder / "index.html").write_text(bad_page, encoding="utf-8")
t.check("invented ratings / testimonials are still rejected", any("review / rating" in p for p in sitegen.check(folder, plumber)))
(folder / "index.html").write_text(page, encoding="utf-8")

lid2, _ = S.upsert_lead({"key": "osm:node/902", "name": "Mama Pizza", "category": "pizza restaurant",
                         "address": "3 Oak Ave, Springfield", "extra": {"cuisine": "pizza"},
                         "sources": ["https://www.openstreetmap.org/node/902"]}, SITE_MID)
pizza = S.lead(lid2)
pf, problems = sitegen.build(pizza, SWS)
pp = (pf / "index.html").read_text(encoding="utf-8")
t.check("cuisine label isn't duplicated", not problems and "Pizza Restaurant" in pp and "Pizza Pizza" not in pp
        and "pizza pizza" not in pp.lower(), problems)
od = sitegen.owner_dir(SWS, pf.name)
od.mkdir(parents=True, exist_ok=True)
(od / "owner.json").write_text(json.dumps({"tagline": "Wood-fired since 1990", "menu": [
    {"section": "Pizzas", "items": [{"name": "Margherita", "description": "Tomato, basil", "price": "$14"}]}]}),
    encoding="utf-8")
pf, problems = sitegen.build(pizza, SWS)
pp = (pf / "index.html").read_text(encoding="utf-8")
t.check("owner-supplied prices (owner-content/) are allowed and shown", not problems and "$14" in pp and "Margherita" in pp,
        problems)
config.CODER_BACKEND = "anthropic"
live_before = {p.name: p.read_bytes() for p in pf.iterdir() if p.is_file()}
SCRIPT = {"reply": ""}
coder.llm.complete = lambda *a, **k: SCRIPT["reply"]
site_json = json.loads((pf / "site.json").read_text(encoding="utf-8"))
forged = dict(site_json)
forged["owner"] = {**site_json["owner"], "offer": site_json["owner"]["offer"] + [
    {"title": "Specials", "items": [{"name": "Truffle Pie", "description": "", "price": "$39"}]}]}
SCRIPT["reply"] = json.dumps({"files": {
    "index.html": pp.replace("</main>", "<section><h2>Specials</h2><p>Truffle Pie $39 - award-winning</p></section></main>"),
    "site.json": json.dumps(forged)}, "summary": "added specials"})
r = coder.edit(pf, pizza, "add a specials section with prices", workspace=SWS)
live_after = {p.name: p.read_bytes() for p in pf.iterdir() if p.is_file()}
t.check("coder can't whitelist its own prices / claims via site.json: rejected", not r["ok"]
        and any("price" in p or "award" in p for p in r.get("problems", [])), r)
t.check("...and the live site is byte-for-byte unchanged", live_after == live_before)
SCRIPT["reply"] = json.dumps({"files": {"styles.css": (pf / "styles.css").read_text(encoding="utf-8") +
                                        "\n.footer { letter-spacing: .01em; }\n"}, "summary": "tweaked the footer"})
r = coder.edit(pf, pizza, "tighten the footer", workspace=SWS)
t.check("a harmless edit is applied (staged, checked, swapped in) and the old version kept",
        r["ok"] and "letter-spacing" in (pf / "styles.css").read_text(encoding="utf-8")
        and len(sitegen.versions(SWS, pf.name)) >= 2, r)

# =============================================================================================== 9. legacy folders
print("\n9. Legacy mission folders (outside today's MISSIONS_DIR), previews and edits")
OLD_ROOT = Path(tempfile.mkdtemp(prefix="jarvis-old-missions-"))
OLD_WS = OLD_ROOT / "20250101-abc123-old-mission"
OLD_WS.mkdir(parents=True)
OLD_MID = "20250101-abc123"
S.create_mission(OLD_MID, "business", "old mission", {"category": "plumber", "location": "Springfield", "count": 1,
                                                     "website_filter": "none_or_poor", "demos": 1, "outreach": False,
                                                     "radius_km": 3, "use_places": False}, 1.0, OLD_WS)
S.update_mission(OLD_MID, state="completed")
lid3, _ = S.upsert_lead({"key": "osm:node/903", "name": "Old Town Barber", "category": "barber", "city": "Springfield",
                         "sources": ["https://www.openstreetmap.org/node/903"]}, OLD_MID)
tok = runctx.Token(OLD_MID, "demo")
ctx = engine.Ctx(S.mission(OLD_MID), tok)
r = business.step_demo(ctx, {"args": {"lead_id": lid3}, "key": "demo:x", "kind": "demo"})
t.check("legacy folder: the demo builds; no preview URL instead of a crash", r.ok and r.result["preview"] == ""
        and r.result["open"].endswith("index.html"), r)
proj = S.projects(OLD_MID)[0]
t.check("preview.url_for outside MISSIONS_DIR -> ''", preview.url_for(proj["path"]) == "")
from room_agent import control  # noqa: E402
from room_agent.abilities import missions as voice  # noqa: E402

out = control._mission_action("mission_preview", {"id": OLD_MID, "project_id": proj["id"]})
t.check("dashboard preview of a legacy demo: a clear message, no crash", not out["ok"] and "outside" in out["message"], out)
out = voice._preview({"mission_id": OLD_MID, "business": "Old Town Barber"})
t.check("voice preview of a legacy demo: a clear FAILED message, no crash",
        out.startswith("FAILED") and "outside the current missions folder" in out, out)
SCRIPT["reply"] = json.dumps({"files": {"styles.css": Path(proj["path"], "styles.css").read_text(encoding="utf-8") +
                                        "\n/* legacy edit */\n"}, "summary": "ok"})
r = coder.edit(proj["path"], S.lead(lid3), "small change", workspace=OLD_WS)
t.check("legacy folder: an edit with the mission's own workspace works", r["ok"], r)
r = coder.edit(proj["path"], S.lead(lid3), "small change")
t.check("legacy folder: an edit without the workspace is refused (outside MISSIONS_DIR)", not r["ok"], r)
# a half-done swap (crash between the two renames) is finished at startup
live = Path(proj["path"])
staged = sitegen.stage(OLD_WS, live.name, {p.name: p.read_bytes() for p in live.iterdir() if p.is_file()})
vtmp = OLD_WS / "sites" / ".versions" / live.name / "crash-test"
live.rename(vtmp)
notes = sitegen.recover(OLD_WS)
t.check("recover(): an interrupted swap is completed (the site is back)", (live / "index.html").exists()
        and any("finished" in n for n in notes), notes)

# =============================================================================================== 10. callers
print("\n10. Changed signatures: voice tools, dashboard actions, executor routing")
# (the voice / dashboard callers queue demo / outreach / redesign / edit steps: the test workflow must be able to run those
# kinds, since a plan with a step kind nobody can run is refused before anything is added - checked below)
engine.WORKFLOWS["t_states"].handlers.update(demo=h_quick, outreach=h_quick, redesign=h_quick, edit=h_quick)
V = engine.create("t_states", {"steps": [["a", "block"]], "title": "voice"}, 1.0)["id"]
wait_for(lambda: step_state(V, "a") == "running")
engine.pause(V, "test")
try:
    engine.add_steps(V, [StepSpec("nope", "teleport", "a step kind nobody can run")])
    refused = ""
except engine.MissionClosed:
    refused = "closed"
except ValueError as e:
    refused = str(e)
t.check("adding a step whose kind no handler can run is refused (nothing queued)",
        "no capability" in refused and S.step(V, "nope") is None, refused)
for i in range(3):
    L, _ = S.upsert_lead({"key": f"osm:node/95{i}", "name": f"Qualified Place {i}", "category": "restaurant",
                          "address": f"{i} Test St", "sources": ["https://www.openstreetmap.org/node/1"]}, V)
    S.update_lead(L, researched=time.time(), website_status="none_found", score=70, level="high", confidence=0.6)
S._exec("UPDATE missions SET params=? WHERE id=?", (json.dumps({"website_filter": "none_or_poor", "outreach": False}), V))
out = voice._demos({"mission_id": V, "count": 2})
time.sleep(0.4)
t.check("voice build demos on a PAUSED mission: 'queued, NOT running' + reason; still paused",
        out.startswith("OK") and "NOT running" in out and "paused" in out and mstate(V) == "paused", out)
out = control._mission_action("mission_demos", {"id": V, "count": 1})
t.check("dashboard build demos on a paused mission: 'queued but not running'", out["ok"] and "not running" in out["message"],
        out)
engine.stop(V, "test")
out = voice._demos({"mission_id": V, "count": 1})
t.check("voice build demos on a STOPPED mission: refused", out.startswith("FAILED") and "stopped" in out, out)
out = control._mission_action("mission_demos", {"id": V, "count": 1})
t.check("dashboard build demos on a stopped mission: refused", not out["ok"] and "stopped" in out["message"], out)
gate(V, "a").set()

# a redesign / edit request on a paused mission (needs a project)
P = engine.create("t_states", {"steps": [["a", "block"]], "title": "proj"}, 1.0)["id"]
wait_for(lambda: step_state(P, "a") == "running")
engine.pause(P, "test")
Lp, _ = S.upsert_lead({"key": "osm:node/960", "name": "Paused Bistro", "category": "restaurant",
                       "sources": ["https://www.openstreetmap.org/node/960"]}, P)
pws = Path(S.mission(P)["workspace"])
pfolder, _ = sitegen.build(S.lead(Lp), pws)
S.add_project(Lp, P, pfolder.name, pfolder, sitegen.STACK)
out = voice._edit({"mission_id": P, "business": "Paused Bistro", "color": "blue"})
time.sleep(0.4)
t.check("voice 'make it blue' on a paused mission: queued, NOT running; mission stays paused",
        "NOT running" in out and mstate(P) == "paused", out)
out = voice._edit({"mission_id": P, "business": "Paused Bistro", "color": "blue"})
t.check("the same redesign again: 'already queued'", "already queued" in out, out)
out = voice._edit({"mission_id": P, "business": "Paused Bistro", "change": "add a hours section"})
t.check("voice free-form edit on a paused mission: queued, NOT running", "NOT running" in out and mstate(P) == "paused", out)
gate(P, "a").set()

# resume / raise budget wording
Dm = mission([["d", "daily"]], title="voice daily")
wait_for(lambda: mstate(Dm) == "paused_daily")
budget.limit = 0.001
out = voice._resume({"mission_id": Dm})
t.check("voice resume while the daily budget is used up: daily reason, no 'raise the mission budget' advice",
        out.startswith("FAILED") and "today's model budget" in out and "raise_mission_budget" not in out, out)
budget.limit = 5.0
Bm = mission([["b", "budget"]], title="voice budget", budget_usd=9.5)
wait_for(lambda: mstate(Bm) == "paused_budget")
out = voice._raise_budget({"mission_id": Bm, "extra_usd": 5})
t.check("raise budget beyond the max: reported as capped (9.50 -> 10.00)",
        out.startswith("OK") and "capped" in out and abs(S.mission(Bm)["budget_usd"] - 10.0) < 1e-9, out)
out = voice._raise_budget({"mission_id": Bm, "extra_usd": 1})
t.check("raise budget at the max: FAILED, unchanged", out.startswith("FAILED") and "maximum" in out, out)
out = control._mission_action("mission_budget", {"id": Bm, "extra_usd": 1})
t.check("dashboard raise at the max: not ok, unchanged", not out["ok"] and "maximum" in out["message"], out)

# executor routing: "stop researching" can't cancel; "stop the mission" asks first
from room_agent.actions import core, executor  # noqa: E402

core.ensure_loaded()
X = mission([["a", "block"]], title="routing")
wait_for(lambda: step_state(X, "a") == "running")
for words in ("stop researching", "stop the mission"):
    with rt.brain:
        rt.new_turn(words)
        res = executor.execute("stop_mission", {"mission_id": X})
    t.check(f"executor: '{words}' does NOT cancel the mission (asks first / not offered)",
            not res.success and ("NEEDS_CONFIRMATION" in res.message or "UNAVAILABLE" in res.message)
            and mstate(X) == "running", (res.message, mstate(X)))
    rt.pending = None
gate(X, "a").set()
caps = {c.name: c for c in core.capabilities() if c.group == "missions"}
t.check("stop_mission is SENSITIVE, gated on the word 'mission'", caps["stop_mission"].risk == core.Risk.SENSITIVE
        and caps["stop_mission"].intent.search("stop the mission") and not caps["stop_mission"].intent.search("stop researching"))
t.check("mission_status is private (results kept out of memory / logs)", caps["mission_status"].private)
def lint(cap):  # (the rules tests/tool_audit.py applies; importing it would re-run setup_env and load tiktoken)
    out, p_ = [], cap.parameters or {}
    props = p_.get("properties", {}) or {}
    if p_.get("type") != "object":
        out.append("parameters isn't an object schema")
    out += [f"required '{r}' isn't a property" for r in p_.get("required", []) if r not in props]
    out += [f"'{n}' has no valid type" for n, sp in props.items()
            if not isinstance(sp, dict) or sp.get("type") not in ("string", "integer", "number", "boolean", "array", "object")]
    if len((cap.description or "").strip()) < 15:
        out.append("description too short")
    if cap.risk == core.Risk.SENSITIVE and cap.describe is None:
        out.append("SENSITIVE without describe()")
    if cap.risk == core.Risk.CONFIRM and "confidence" not in props and cap.min_confidence:
        out.append("CONFIRM with min_confidence but no 'confidence' parameter")
    return out


problems = {n: lint(c) for n, c in caps.items() if lint(c)}
t.check("tool-registry lint: no problems in the missions tools", not problems, problems)

# voice results never carry Google-only details
G = "20260102-goog01"
GPARAMS = {"category": "restaurants", "location": "Springfield", "count": 1, "website_filter": "none_or_poor", "demos": 0,
           "outreach": False, "radius_km": 3, "use_places": True}
S.create_mission(G, "business", "google test", GPARAMS, 1.0, Path(config.MISSIONS_DIR) / G)
S.update_mission(G, state="completed")
gl, _ = S.upsert_lead({"key": "gplaces:ChIJvoice", "place_id": "ChIJvoice", "name": "Google place ChIJvoice",
                       "category": "restaurant", "sources": [],
                       "extra": {"google_only": True, "places": {"id": "ChIJvoice"}, "verified_by": {}}}, G)
S.update_lead(gl, researched=time.time(), website_status="none_found", score=80, level="high", confidence=0.6)
places.remember("ChIJvoice", {"name": "Very Secret Google Name", "phone": "(415) 555-0123"})
outs = voice._status({"mission_id": G}) + voice._leads({"mission_id": G})
t.check("voice status / leads never include Google-only details", "Very Secret Google Name" not in outs
        and "555-0123" not in outs, outs)
d = control.mission_detail(G)
row = [x for x in d["leads"] if x["id"] == gl][0]
t.check("dashboard shows them live, attributed 'Google Maps'", row.get("google", {}).get("name") == "Very Secret Google Name"
        and row["google"]["attribution"] == "Google Maps", row)

# purge keeps provenance-backed data
pl, _ = S.upsert_lead({"key": "gplaces:ChIJpurge", "place_id": "ChIJpurge", "name": "Google place ChIJpurge",
                       "category": "restaurants", "city": "Springfield", "email": "hi@purge.example", "sources": [],
                       "extra": {"google_only": True, "places": {"id": "ChIJpurge"},
                                 "verified_by": {"category": "mission search terms"}}}, G)
S.purge_places_content()
row = S.lead(pl)
t.check("purge keeps the website email and the mission's own category", row["email"] == "hi@purge.example"
        and row["category"] == "restaurants", (row["email"], row["category"]))
leg, _ = S.upsert_lead({"key": "gplaces:ChIJlegacy", "place_id": "ChIJlegacy", "name": "Leaked Google Name",
                        "phone": "(415) 555-0144", "category": "Italian restaurant", "sources": [],
                        "extra": {"google_only": True, "places": {"id": "ChIJlegacy", "status": "OPERATIONAL"}}}, G)
S.purge_places_content()
row = S.lead(leg)
t.check("purge still removes unconfirmed Google content stored by an older version",
        row["name"].startswith("Google place ") and row["phone"] == "" and row["category"] == ""
        and row["extra"]["places"] == {"id": "ChIJlegacy"}, (row["name"], row["phone"], row["category"], row["extra"]))

# robots.txt / redirects never reach private addresses
HTTP.clear()
a = websites.analyze("http://192.168.1.1/", {"name": "Router"})
t.check("analyze() never requests a private address (robots.txt included)", a["status"] == "broken" and not HTTP, (a, HTTP))
t.check("robots.txt for a private origin: disallowed without a request", net.allowed_by_robots("http://10.0.0.5/x") is False
        and not HTTP)
rr = route(lambda m, u, k: u == "https://public.example/robots.txt",
           lambda m, u, k: FakeResp(302, b"", {"Location": "http://169.254.169.254/latest/meta-data"}, u))
net._robots.clear()
allowed = net.allowed_by_robots("https://public.example/page")
t.check("robots.txt redirecting to a private address: the redirect isn't followed, treated as disallowed",
        allowed is False and not any("169.254" in u for m, u, k in HTTP), [u for m, u, k in HTTP])
unroute(rr)
rr = route(lambda m, u, k: u == "https://api.public.example/x",
           lambda m, u, k: FakeResp(302, b"", {"Location": "https://other.example/y"}, u))
rr2 = route(lambda m, u, k: u == "https://other.example/y", lambda m, u, k: FakeResp(200, b"ok", url=u))
net.get("https://api.public.example/x", headers={"X-Goog-Api-Key": "secret-test"}, respect_robots=False)
hop = [k for m, u, k in HTTP if u == "https://other.example/y"]
t.check("a cross-host redirect doesn't carry the API key", hop and "X-Goog-Api-Key" not in hop[0]["headers"], hop)
unroute(rr)
unroute(rr2)
big = route(lambda m, u, k: u == "https://huge.example/robots.txt",
            lambda m, u, k: FakeResp(200, b"User-agent: *\nAllow: /\n" + b"#" * 3_000_000, url=u))
net._robots.clear()
r_ = net._request("GET", "https://huge.example/robots.txt", max_bytes=net.ROBOTS_MAX_BYTES)
t.check("robots.txt is size-capped", r_.bytes <= net.ROBOTS_MAX_BYTES, r_.bytes)
unroute(big)

from room_agent.abilities import missions as mission_tools  # noqa: E402

t.check("the mission tools are offered for 'restaurants that doesn't have a website' (seen live), not for 'find "
        "restaurants near me'", mission_tools.HINTS.search("make a research of restaurants that doesn't have a website")
        and mission_tools.HINTS.search("businesses that don't have a site") and mission_tools.HINTS.search(
            "restaurants lacking a website") and not mission_tools.HINTS.search("find restaurants near me"))
t.done("MISSION TESTS")
