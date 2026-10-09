"""Goal-driven missions: interpretation, plan validation, contract verification, bounded replanning, parallel steps,
the end-to-end business goal on a fake world, explanations. Offline: fake world, fake model transport, no Gmail.

Run:  .venv\\Scripts\\python -m tests.test_intelligence
"""

import json
import tempfile
import threading
import time
from pathlib import Path

from tests.harness import setup_env

setup_env(MISSION_SENDER_NAME="Test Sender", MISSION_SENDER_EMAIL="sender@example.org",
          MISSION_SENDER_ADDRESS="1 Test Way, Testville", DAILY_BUDGET_USD="50")

import room_agent.integrations as integ  # noqa: E402
import room_agent.integrations.google.gmail as gmailmod  # noqa: E402
from room_agent import config, control  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.missions import business, engine, goals, llm, meter, sitegen  # noqa: E402
from room_agent.missions.engine import Contract, StepResult, StepSpec, Workflow  # noqa: E402
from room_agent.missions.store import store  # noqa: E402
from tests import fake_world  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
S = store()
assert config.TEST_MODE and tempfile.gettempdir() in str(config.MISSIONS_DB), config.MISSIONS_DB
engine._announce = lambda text, mid: None
engine.BACKOFF_S = (0.05, 0.05, 0.05)
GMAIL = {"created": 0}


class NoGmail:
    def __init__(self, provider, account=None):
        pass

    def create_draft(self, *a, **k):
        GMAIL["created"] += 1
        raise RuntimeError("never in this test")


integ.provider = lambda pid: type("P", (), {"can": lambda self, s_, lv: True})()
gmailmod.GmailService = NoGmail


def wait_end(mid, timeout=120):
    end = time.time() + timeout
    while time.time() < end and S.mission(mid)["state"] in ("running", "planned"):
        time.sleep(0.03)
    return S.mission(mid)["state"]


# =============================================================================================== 1. interpretation
print("\n1. Goal interpretation (no model call, nothing invented)")
GOAL = ("Find 20 local businesses that lack a verified website, research them, prioritize the best opportunities, build "
        "five demo websites, and prepare personalized outreach drafts.")
g = goals.interpret(GOAL)
t.check("the brief's goal: business mission; count 20, 'none' filter, 5 demos, outreach", g.capability == "business_mission"
        and g.params.get("count") == 20 and g.params.get("website_filter") == "none" and g.params.get("demos") == 5
        and g.params.get("outreach") is True, g.params)
t.check("category and place aren't in the words (and no home location is known): ASKED, not invented",
        sorted(q["field"] for q in g.questions) == ["category", "location"] and "category" not in g.params
        and "location" not in g.params, g.questions)
try:
    goals.start(g)
    started = True
except ValueError as e:
    started = "first:" not in str(e)
t.check("a goal with open questions can't start a mission", not started)
g = goals.interpret(GOAL, answers={"category": "restaurants", "location": "Testville"})
ids = [c["id"] for c in g.success_criteria]
t.check("answered: success criteria qualified 20 / demos 5 / drafts 5 / nothing sent", ids == ["qualified", "demos", "drafts",
        "nothing_sent"] and [c["target"] for c in g.success_criteria] == [20, 5, 5, 0], g.success_criteria)
t.check("permissions: Gmail drafts need an approval each, never sent; spending within the budget",
        any("approval" in p["how"] for p in g.permissions) and any(p["what"] == "spending" for p in g.permissions))
t.check("deliverables listed; nothing-is-sent constraint present", len(g.deliverables) == 3
        and "nothing is sent or published (drafts only)" in g.constraints)
g2 = goals.interpret("find florists in Seattle with no website, don't use Google, spend at most $2")
t.check("their limits become constraints (no Google, $2)", g2.params["use_places"] is False and g2.budget_usd == 2.0
        and "don't use Google data" in g2.constraints and any("$2.00" in c for c in g2.constraints), g2.constraints)
g3 = goals.interpret("find dentists in Austin, TX with no website")
t.check("values not stated are documented defaults, not preferences", any("default" in d for d in g3.defaults)
        and g3.params["demos"] == 3, g3.defaults)
for text, cap in (("research the latest news about solid-state batteries", "research"),
                  ("open Spotify and play my focus playlist", "desktop_task"),
                  ("make the demo site for Luigi's blue", "site_edit"), ("approve the email draft for number 3", "email_draft"),
                  ("send 500 cold emails to every business in town right now", "unsupported")):
    gg = goals.interpret(text)
    t.check(f"routing: {text[:40]!r} -> {cap}", gg.capability == cap, gg.capability)
from room_agent.actions import core  # noqa: E402

core.ensure_loaded()
t.check("every routed capability is an EXISTING registered tool (nothing invented)",
        all(core.get(tool) is not None for tool in goals.CAPABILITY_TOOLS.values() if tool),
        [tool for tool in goals.CAPABILITY_TOOLS.values() if tool and core.get(tool) is None])
t.check("a mass-sending request maps to no tool at all", goals.CAPABILITY_TOOLS["unsupported"] == "")

# =============================================================================================== 2. plan validation
print("\n2. Plans are validated before anything runs")
RAN = []


def quick(ctx, st):
    RAN.append((ctx.id, st["key"]))
    return StepResult(True, evidence="ran")


def wf(kind, plan, **kw):
    return engine.register(Workflow(kind=kind, title="t", plan=lambda p: plan, handlers={"quick": quick, **kw.pop("h", {})},
                                    describe=lambda p: kind, summarize=lambda ctx: "summary", **kw))


for name, plan, why in (("unknown", [StepSpec("a", "teleport", "a")], "no capability"),
                        ("dangling", [StepSpec("a", "quick", "a", depends=["ghost"])], "isn't part of the plan"),
                        ("cycle", [StepSpec("a", "quick", "a", depends=["b"]), StepSpec("b", "quick", "b", depends=["a"])],
                         "cycle"),
                        ("dup", [StepSpec("a", "quick", "a"), StepSpec("a", "quick", "a2")], "duplicate")):
    wf(f"iv_{name}", plan)
    n0 = len(S.missions(1000))
    try:
        engine.create(f"iv_{name}", {})
        msg = ""
    except ValueError as e:
        msg = str(e)
    t.check(f"invalid plan ({name}) refused before anything is created or run", why in msg
            and len(S.missions(1000)) == n0, msg)
wf("iv_contract", [StepSpec("a", "quick", "a")], contracts={"other": Contract("x", "y", "z")})
try:
    engine.create("iv_contract", {})
    msg = ""
except ValueError as e:
    msg = str(e)
t.check("a workflow with contracts: a step kind without a contract is refused", "no contract" in msg, msg)


def adds_bad(ctx, st):
    return StepResult(True, evidence="ok", add=[StepSpec("x", "quick", "x", depends=["nowhere"])])


wf("iv_badadd", [StepSpec("a", "addbad", "a")], h={"addbad": adds_bad})
mid = engine.create("iv_badadd", {})["id"]
wait_end(mid)
t.check("a step whose follow-up plan is invalid fails (nothing invalid is added)", S.step(mid, "a")["state"] == "failed"
        and "aren't a valid plan" in S.step(mid, "a")["error"] and S.step(mid, "x") is None, S.step(mid, "a"))
wf("iv_legacy", [StepSpec("a", "quick", "a")])
mid = engine.create("iv_legacy", {}, 1.0)["id"]
wait_end(mid)
engine.pause(mid)
S.add_step(mid, "c1", "quick", "c1", {}, ["c2"], True, 1)  # (an older database: a cycle stored before validation)
S.add_step(mid, "c2", "quick", "c2", {}, ["c1"], True, 1)
S.set_state(mid, "running", "test")
engine.start_runner()
state = wait_end(mid)
t.check("steps that can never run are reported blocked; the mission is NOT 'completed'",
        state == "finished_with_problems" and S.step(mid, "c1")["state"] == "blocked", (state, S.step(mid, "c1")["state"]))

# =============================================================================================== 3. verification
print("\n3. A step is done only when its contract's code check passes")
VFLAG = {"n": 0}


def flaky_output(ctx, st):
    VFLAG["n"] += 1
    f = ctx.workspace / "out.txt"
    if VFLAG["n"] >= 2:
        f.write_text("result", encoding="utf-8")
    return StepResult(True, evidence="handler says done")


def v_out(ctx, st, res):
    return [] if (ctx.workspace / "out.txt").exists() else ["out.txt wasn't written"]


wf("iv_verify", [StepSpec("w", "write", "write it", max_attempts=3)], h={"write": flaky_output},
   contracts={"write": Contract("write a file", "fs", "out.txt exists", v_out)})
mid = engine.create("iv_verify", {})["id"]
wait_end(mid)
st = S.step(mid, "w")
t.check("handler said done but the file was missing: verification failed, retried, then verified",
        st["state"] == "completed" and st["attempts"] == 2 and "verified: out.txt exists" in st["evidence"], dict(st))
VFLAG["n"] = -100
wf("iv_verify2", [StepSpec("w", "write", "write it", idempotent=False)], h={"write": flaky_output},
   contracts={"write": Contract("write a file", "fs", "out.txt exists", v_out)})
mid = engine.create("iv_verify2", {})["id"]
wait_end(mid)
t.check("non-idempotent: a failed verification is a failure, not retried, mission reports a problem",
        S.step(mid, "w")["state"] == "failed" and "verification failed" in S.step(mid, "w")["error"]
        and S.mission(mid)["state"] == "finished_with_problems")


def committed_wrong(ctx, st):
    return StepResult(True, evidence="applied", committed=True)


wf("iv_verify3", [StepSpec("w", "commit", "apply")], h={"commit": committed_wrong},
   contracts={"commit": Contract("apply", "x", "out.txt exists", v_out)})
mid = engine.create("iv_verify3", {})["id"]
wait_end(mid)
t.check("an applied change that fails verification is recorded as such (never silently 'done')",
        S.step(mid, "w")["state"] == "failed" and "applied, but verification failed" in S.step(mid, "w")["error"])
config.MISSION_VERIFY = False
VFLAG["n"] = -100
mid = engine.create("iv_verify2", {})["id"]
wait_end(mid)
t.check("MISSION_VERIFY=0 (feature flag): the old behaviour (the handler's word)", S.step(mid, "w")["state"] == "completed")
config.MISSION_VERIFY = True

# =============================================================================================== 4. replanning
print("\n4. Replanning: bounded, logged, only for goal-driven missions")
RP = {"calls": 0}


def always_more(ctx, crit):
    RP["calls"] += 1
    n = len(S.steps(ctx.id))
    return [StepSpec(f"more{n}", "quick", f"more {n}")], f"criterion unmet (try {n})"


def never_met(mid):
    return [{"id": "x", "what": "never", "metric": "x", "target": 1, "got": 0, "met": False}]


real_eval = goals.evaluate
goals.evaluate = lambda mid, save=True: never_met(mid) if S.mission(mid)["kind"] == "iv_replan" else real_eval(mid, save)
wf("iv_replan", [StepSpec("a", "quick", "a")], replan=always_more, contracts={"quick": Contract("q", "q", "q")})
mid = engine.create("iv_replan", {}, goal={"text": "x", "success_criteria": [{"id": "x", "metric": "x", "target": 1}]})["id"]
wait_end(mid)
row = S.goal(mid)
reps = [d for d in row["decisions"] if d["type"] == "replan"]
t.check(f"at most MISSION_MAX_REPLANS ({config.MISSION_MAX_REPLANS}) plan changes, each with its reason",
        row["replans"] == config.MISSION_MAX_REPLANS == len(reps) and all(d["why"] for d in reps), row["decisions"])
t.check("...then the limit is recorded and the mission ends", any(d["type"] == "replan_limit" for d in row["decisions"])
        and S.mission(mid)["state"] in ("completed", "finished_with_problems"))
calls0 = RP["calls"]
mid = engine.create("iv_replan", {})["id"]  # (no goal recorded: an ordinary mission)
wait_end(mid)
t.check("a mission without a goal never replans (old behaviour)", RP["calls"] == calls0 and S.goal(mid) is None)
goals.evaluate = real_eval

# =============================================================================================== 5. parallel steps
print("\n5. Parallel independent steps (MISSION_CONCURRENCY)")
LIVE = {"now": 0, "max": 0, "res": {}, "res_overlap": 0, "alone_overlap": 0}
lk = threading.Lock()


def slow(ctx, st):
    with lk:
        LIVE["now"] += 1
        LIVE["max"] = max(LIVE["max"], LIVE["now"])
        r = st["args"].get("r")
        if r and LIVE["res"].get(r):
            LIVE["res_overlap"] += 1
        if r:
            LIVE["res"][r] = True
    try:
        end = time.time() + 0.25
        while time.time() < end:
            ctx.check()
            time.sleep(0.01)
    finally:
        with lk:
            LIVE["now"] -= 1
            if st["args"].get("r"):
                LIVE["res"][st["args"]["r"]] = False
    return StepResult(True, evidence="slept")


def alone(ctx, st):
    with lk:
        if LIVE["now"]:
            LIVE["alone_overlap"] += 1
    return slow(ctx, st)


PC = {"slow": Contract("s", "s", "s", parallel_safe=True, resource=lambda a: a.get("r")),
      "alone": Contract("a", "a", "a"), "quick": Contract("q", "q", "q")}
plan = [StepSpec(f"p{i}", "slow", f"p{i}", {"r": f"r{i % 4}"}) for i in range(8)] + [
    StepSpec("solo", "alone", "solo", {}, ["p0"]), StepSpec("after", "quick", "after", {}, ["p:*", "solo"])]
wf("iv_par", plan, h={"slow": slow, "alone": alone}, contracts=PC)
config.MISSION_CONCURRENCY = 3
t0 = time.time()
mid = engine.create("iv_par", {})["id"]
state = wait_end(mid)
wall = time.time() - t0
t.check("up to 3 steps at once, never more", LIVE["max"] == 3, LIVE)
t.check("two steps on the same resource never overlap; a non-parallel-safe step runs alone",
        LIVE["res_overlap"] == 0 and LIVE["alone_overlap"] == 0, LIVE)
t.check("dependencies still hold ('after' ran last) and everything completed once",
        state == "completed" and RAN[-1] == (mid, "after") and all(st["attempts"] == 1 for st in S.steps(mid)),
        (state, RAN[-3:]))
t.check(f"faster than one at a time (8 x 0.25s + solo sequential ~2.25s; took {wall:.2f}s)", wall < 1.9, wall)
LIVE.update(now=0, max=0)
mid = engine.create("iv_par", {})["id"]
time.sleep(0.15)
engine.pause(mid, "test")
time.sleep(0.6)
t.check("pause cancels every run in progress (none left running; they go back to pending)",
        S.mission(mid)["state"] == "paused" and not [s_ for s_ in S.steps(mid) if s_["state"] == "running"]
        and LIVE["now"] == 0, [(s_["key"], s_["state"]) for s_ in S.steps(mid)])
engine.stop(mid, "test")


def paid_step(ctx, st):
    with meter.paid(0.4, "parallel paid", provider="test", model="x") as ch:
        time.sleep(0.15)
        ch.actual(0.4, 10, 10, basis="provider_reported")
    return StepResult(True, evidence="paid")


wf("iv_parpaid", [StepSpec(f"q{i}", "paid", f"q{i}") for i in range(4)], h={"paid": paid_step},
   contracts={"paid": Contract("p", "p", "p", parallel_safe=True)})
mid = engine.create("iv_parpaid", {}, 1.0)["id"]
end = time.time() + 20
while time.time() < end and (S.mission(mid)["state"] == "running" or [s_ for s_ in S.steps(mid) if s_["state"] == "running"]):
    time.sleep(0.03)
m = S.mission(mid)
settled = [c for c in S.charges(mid, 100) if c["state"] == "settled"]
t.check("a shared budget across parallel paid steps: never overspent ($1.00: two $0.40 calls, then paused)",
        len(settled) == 2 and m["spent_usd"] <= 1.0 + 1e-9 and m["state"] == "paused_budget", (len(settled), m["spent_usd"], m["state"]))
t.check("the calls in flight when the budget ran out finished normally (settled, not cut off as 'uncertain')",
        not [c for c in S.charges(mid, 100) if c["state"] == "uncertain"], [c["state"] for c in S.charges(mid, 100)])
PAID_MID = mid
engine.stop(mid, "test")
config.MISSION_CONCURRENCY = 1

# =============================================================================================== 6. model tiers
print("\n6. Cost-aware model tiers")
t.check("light tasks use the light tier, planning / coding the strong one",
        llm.tier_for("wording") == "light" and llm.tier_for("extraction") == "light" and llm.tier_for("coding") == "strong")
t.check("MISSION_ESCALATE_AFTER=0: never escalates (no surprise cost)", llm.tier_for("wording", failures=99) == "light")
config.MISSION_ESCALATE_AFTER = 2
t.check("with a threshold: escalates after repeated check failures only",
        llm.tier_for("wording", 1) == "light" and llm.tier_for("wording", 2) == "strong")
config.MISSION_ESCALATE_AFTER = 0

# =============================================================================================== 7. the business goal
print("\n7. The brief's business goal end to end (fake world; failures, a restart; nothing sent)")
world = fake_world.install(fake_world.World(n=90, spacing_km=0.125, latency_s=0.0, pace_scale=0.01))
world.fail_search_once = {b["i"] for b in world.biz if b["i"] % 4 == 1}
real_build = sitegen.build
FAILED_DEMO = {}


def build(lead, workspace, overrides=None):
    rank = (lead.get("extra") or {}).get("rank") or {}
    if 1 in rank.values() and not overrides and "lead" not in FAILED_DEMO:
        FAILED_DEMO["lead"] = lead["name"]
        return sitegen.sites_dir(workspace) / sitegen.slug(lead["name"], lead["id"]), ["simulated: contrast check failed"]
    return real_build(lead, workspace, overrides)


sitegen.build = build
config.MISSION_CONCURRENCY = 4
g = goals.interpret(GOAL, answers={"category": "restaurants", "location": "Testville"})
mid = goals.start(g, 1.0)["id"]
end = time.time() + 120
while time.time() < end and sum(1 for st in S.steps(mid) if st["kind"] == "research" and st["state"] == "completed") < 10:
    time.sleep(0.02)
engine.load()  # (a restart in the middle of parallel research)
t.check("restart during parallel research: the mission is interrupted, no step left running",
        S.mission(mid)["state"] == "interrupted" and not [st for st in S.steps(mid) if st["state"] == "running"])
engine.resume(mid)
state = wait_end(mid, 300)
crit = {c["id"]: c for c in goals.evaluate(mid)}
t.check("all success criteria met: 20 qualifying, 5 verified demos, 5 drafts, nothing sent",
        all(c["met"] for c in crit.values()), {k: (c["got"], c["target"]) for k, c in crit.items()})
chosen = [x for x in S.leads(mission_id=mid, limit=1000) if mid in ((x.get("extra") or {}).get("rank") or {})]
t.check("no fabricated business facts: every chosen business truly lacks a working site of its own (world truth)",
        len(chosen) == 20 and all(world.by_name(x["name"])["kind"] in fake_world.QUALIFYING for x in chosen),
        [(x["name"], world.by_name(x["name"])["kind"]) for x in chosen if world.by_name(x["name"])["kind"] not in fake_world.QUALIFYING])
dec = S.goal(mid)["decisions"]
t.check("plan change logged with its reason: a wider (free) search; the failed demo was replaced by the re-rank",
        any("searching 6 km" in d["why"] for d in dec if d["type"] == "replan") and crit["demos"]["met"]
        and [st["state"] for st in S.steps(mid) if st["kind"] == "demo"].count("completed") == 5, [d.get("why") for d in dec])
research = [st for st in S.steps(mid) if st["kind"] == "research"]
unknown = [x for x in S.leads(mission_id=mid, limit=1000) if x["website_status"] == "unknown"]
t.check("a web search that failed once was retried (verification), not silently counted as checked",
        any(st["attempts"] >= 2 and st["state"] == "completed" for st in research)
        and all(S.step(mid, f"research:{x['id']}")["state"] == "skipped" for x in unknown)
        and not [x for x in unknown if mid in ((x.get("extra") or {}).get("rank") or {})],
        [(x["name"], S.step(mid, f"research:{x['id']}")["state"]) for x in unknown][:3])
# (an "unknown" left only where its retry was skipped because enough businesses already qualified; never chosen)
t.check("nothing sent, no Gmail draft created (approvals wait for the user)", GMAIL["created"] == 0
        and all(a["status"] == "pending" for a in S.approvals(mid)))
t.check("the summary states the goal result from the records", "success criteria met" in (S.mission(mid)["summary"] or ""),
        S.mission(mid)["summary"][-200:])
FAILED_STEP = next(st for st in S.steps(mid) if st["kind"] == "demo" and st["state"] == "failed")
t.check("the failed demo is reported honestly (failed, with the check's reason); mission 'finished_with_problems'",
        "contrast" in FAILED_STEP["error"] and state == "finished_with_problems", (state, FAILED_STEP["error"]))
sitegen.build = real_build
config.MISSION_CONCURRENCY = 1

# =============================================================================================== 8. explanations
print("\n8. What Jarvis says it's doing (dashboard + voice), from the records")
e = goals.explain(mid)
t.check("explain: understood goal, criteria, plan by kind, changes with reasons, failures classified",
        e["understood"].startswith("20 restaurants in Testville") and len(e["criteria"]) == 4 and "research" in e["plan"]
        and e["changes"] and any(f["kind"] == "permanent" or f["kind"] == "verification" for f in e["failed"]), e["failed"][:2])
short = goals.explain(mid, short=True)
t.check("the spoken version is short and factual", len(short) < 400 and "4 of 4 success criteria met" in short, short)
det = control.mission_detail(mid)
t.check("dashboard: goal + plan with contracts (purpose, capability, expected, verification, cost)",
        det["goal"]["criteria"] and det["plan"] and {"purpose", "capability", "expected", "verified_by", "on_failure",
                                                      "est_usd"} <= set(det["plan"][0]), list(det["plan"][0]))
sp = goals.explain(PAID_MID)["spend"]
t.check("spend by model with its basis (provider-reported vs estimate)", sp.get("test:x", {}).get("calls") == 2
        and sp["test:x"]["by_basis"] == {"provider_reported": 2}, sp)
from room_agent.abilities import missions as voice  # noqa: E402

out = voice._explain({"mission_id": mid})
t.check("voice explain_mission: goal, criteria, plan, why it changed", out.startswith("OK:") and "Why the plan changed" in out
        and "criteria met" in out, out[:300])
dump = json.dumps(det, default=str)
t.check("no keys or tokens in what the dashboard gets", "sk-test" not in dump and "sk-ant" not in dump)

# =============================================================================================== 9. voice start + memory
print("\n9. Voice start: their words fill what the arguments left out; verified knowledge is reused")
world2 = fake_world.install(fake_world.World(n=40, spacing_km=0.05, pace_scale=0.01, seed_shift=7))
rt.turn_text = "find 6 bakeries in Testville that lack a website and build 2 demo sites"
business_start = business.start
CAP = {}
business.start = lambda p, b=None, goal=None: CAP.update(p=p, goal=goal) or business_start(p, b, goal=goal)
out = voice._start({"category": "bakeries", "location": "Testville", "confidence": 0.9})
business.start = business_start
t.check("the count / demos / filter they said were filled in from their words (the arguments had left them out)",
        out.startswith("OK") and CAP["p"]["count"] == 6 and CAP["p"]["demos"] == 2 and CAP["p"]["website_filter"] == "none",
        (out[:200], CAP.get("p")))
t.check("...and the goal (criteria) is stored with the mission", S.goal(out.split("(mission ")[1].split(")")[0])
        is not None and "Understood goal" in out)
vm = out.split("(mission ")[1].split(")")[0]
wait_end(vm, 120)
S.cache_put("goal-knowledge:bakeries|testville", {"radius_km_needed": 6.0, "when": "2026-10-01", "mission": vm})
g = goals.interpret("find 6 bakeries in Testville that lack a website")
t.check("a verified earlier outcome (radius needed) is reused as a labelled hint", g.params["radius_km"] == 6.0
        and g.learned and "verified" in g.learned[0], (g.params["radius_km"], g.learned))
rt.turn_text = "find 6 bakeries in Testville with no website, make 4 demo sites"
out = voice._start({"category": "bakeries", "location": "Testville", "demos": 1, "confidence": 0.9})
t.check("their words contradict the arguments (4 vs 1 demos): said back, not silently changed",
        "CHECK WITH THEM" in out and "demos" in out, out[:300])
engine.stop(out.split("(mission ")[1].split(")")[0], "test") if "(mission " in out else None
t.done("INTELLIGENCE TESTS")
