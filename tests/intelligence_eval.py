"""Intelligence evaluation (offline, no model): goal interpretation, capability routing and plan validity.

The labelled cases below were written BEFORE the goal interpreter (missions/goals.py) existed, so they aren't fitted to
it. Each case: what was said -> the capability it needs, the structured fields that must come out, and which facts are
missing and must be ASKED (never invented).

    goal interpretation     fields exactly right (category / location / count / website filter / demos / outreach /
                            budget / Google data) and the right clarification questions (no more, no fewer)
    capability routing      the request goes to the right existing capability family
    plan validity           invalid plans (unknown step kind, dangling dependency, dependency cycle, duplicate key) are
                            rejected BEFORE anything runs

    .venv\\Scripts\\python -m tests.intelligence_eval --label baseline     -> tests/benchmarks/intel-<label>.json
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "benchmarks"

# (text, capability, expected fields (only those listed are scored), questions that must be asked)
CASES = [
    ("Find 20 local businesses that lack a verified website, research them, prioritize the best opportunities, build five "
     "demo websites, and prepare personalized outreach drafts.", "business_mission",
     {"count": 20, "website_filter": "none", "demos": 5, "outreach": True}, {"category", "location"}),
    ("Find 20 restaurants in San Francisco with no website and build demos for the best 3", "business_mission",
     {"category": "restaurants", "location": "San Francisco", "count": 20, "website_filter": "none", "demos": 3}, set()),
    ("find dentists in Austin, TX that have a bad website", "business_mission",
     {"category": "dentists", "location": "Austin, TX", "website_filter": "poor"}, set()),
    ("look for 10 plumbers near Denver without websites, no demos, just the list", "business_mission",
     {"category": "plumbers", "location": "Denver", "count": 10, "website_filter": "none", "demos": 0}, set()),
    ("Find hair salons in Brooklyn with no or a poor website, make 2 demo sites, don't write any emails",
     "business_mission", {"category": "hair salons", "location": "Brooklyn", "website_filter": "none_or_poor",
                          "demos": 2, "outreach": False}, set()),
    ("find 15 bakeries in Portland lacking a website and spend at most $3", "business_mission",
     {"category": "bakeries", "location": "Portland", "count": 15, "website_filter": "none", "budget_usd": 3.0}, set()),
    ("find cafes in Lyon without a website using Google data", "business_mission",
     {"category": "cafes", "location": "Lyon", "website_filter": "none", "use_places": True}, set()),
    ("find businesses without websites and make demos", "business_mission", {"website_filter": "none"},
     {"category", "location"}),
    ("find 30 gyms that need a new website", "business_mission", {"category": "gyms", "count": 30}, {"location"}),
    ("build demo websites for 4 pizza places in Chicago that have no site", "business_mission",
     {"category": "pizza places", "location": "Chicago", "demos": 4, "website_filter": "none"}, set()),
    ("prospect 25 auto repair shops in Phoenix with poor websites, build three demos and draft outreach",
     "business_mission", {"category": "auto repair shops", "location": "Phoenix", "count": 25, "website_filter": "poor",
                          "demos": 3, "outreach": True}, set()),
    ("find florists in Seattle with no website, don't use Google", "business_mission",
     {"category": "florists", "location": "Seattle", "website_filter": "none", "use_places": False}, set()),
    ("research the latest news about solid-state batteries", "research", {}, set()),
    ("look up what the opening hours of the Louvre are", "research", {}, set()),
    ("compare the prices of the top three robot vacuums", "research", {}, set()),
    ("open Spotify and play my focus playlist", "desktop_task", {}, set()),
    ("move Chrome to my second monitor and maximize it", "desktop_task", {}, set()),
    ("make the demo site for Luigi's blue", "site_edit", {}, set()),
    ("change the footer of the Golden Wok demo to say open late", "site_edit", {}, set()),
    ("approve the email draft for number 3", "email_draft", {}, set()),
    ("put the outreach email for Luigi's in my Gmail drafts", "email_draft", {}, set()),
    ("hack into my neighbour's wifi", "unsupported", {}, set()),
    ("send 500 cold emails to every business in town right now", "unsupported", {}, set()),
    ("find 12 tattoo shops in Berlin with only a Facebook page and build 2 demos", "business_mission",
     {"category": "tattoo shops", "location": "Berlin", "count": 12, "website_filter": "none", "demos": 2}, set()),
]


# HELD OUT: written after the interpreter, and never used to tune it (reported separately, as the honest estimate).
HELDOUT = [
    ("Can you get me a list of 40 barbers around Leeds that don't have a website? Build demos for the top 6.",
     "business_mission", {"category": "barbers", "location": "Leeds", "count": 40, "website_filter": "none", "demos": 6},
     set()),
    ("I want to pitch websites to electricians in Tampa whose sites look outdated", "business_mission",
     {"category": "electricians", "location": "Tampa", "website_filter": "poor"}, set()),
    ("search for 8 yoga studios in Madrid without a site, three demos, budget 2 dollars", "business_mission",
     {"category": "yoga studios", "location": "Madrid", "count": 8, "website_filter": "none", "demos": 3,
      "budget_usd": 2.0}, set()),
    ("find some local shops that need a new website", "business_mission", {"website_filter": "poor"},
     {"category", "location"}),
    ("locate accountants near Boston with poor websites and skip the outreach", "business_mission",
     {"category": "accountants", "location": "Boston", "website_filter": "poor", "outreach": False}, set()),
    ("what's the population of Lisbon", "research", {}, set()),
    ("close all the Chrome windows", "desktop_task", {}, set()),
    ("reject approval 4", "email_draft", {}, set()),
    ("turn Golden Wok's demo green", "site_edit", {}, set()),
    ("find twelve photographers in Nice with no website and prepare outreach drafts", "business_mission",
     {"category": "photographers", "location": "Nice", "count": 12, "website_filter": "none", "outreach": True}, set()),
]


def eval_goals(cases=None):
    cases = CASES if cases is None else cases
    try:
        from room_agent.missions import goals
    except ImportError:
        return {"available": False, "note": "no goal interpreter in this version (the LLM fills tool arguments; not "
                                            "measurable offline)"}
    fields_ok = fields_total = q_ok = route_ok = 0
    misses = []
    for text, cap, want, questions in cases:
        g = goals.interpret(text)
        if g.capability == cap:
            route_ok += 1
        else:
            misses.append(("route", text[:60], g.capability))
        for k, v in want.items():
            fields_total += 1
            got = g.params.get(k)
            if (isinstance(v, str) and str(got or "").lower() == v.lower()) or got == v:
                fields_ok += 1
            else:
                misses.append(("field", text[:60], k, got, v))
        if {q["field"] for q in g.questions} == questions:
            q_ok += 1
        else:
            misses.append(("questions", text[:60], sorted(q["field"] for q in g.questions), sorted(questions)))
    n = len(cases)
    return {"available": True, "cases": n, "routing_accuracy": round(route_ok / n, 3),
            "field_accuracy": round(fields_ok / fields_total, 3), "clarification_accuracy": round(q_ok / n, 3),
            "misses": misses}


def eval_plans():
    """Invalid plans must be refused before anything runs. -> {rejected_before_run, executed_steps}"""
    from tests.harness import setup_env

    setup_env()
    from room_agent.missions import engine
    from room_agent.missions.engine import StepResult, StepSpec, Workflow
    from room_agent.missions.store import store

    ran = []
    engine._announce = lambda text, mid: None

    def quick(ctx, st):
        ran.append(st["key"])
        return StepResult(True, evidence="ran")

    bad = {
        "unknown_kind": [StepSpec("a", "quick", "a"), StepSpec("b", "teleport", "b", depends=["a"])],
        "dangling_dependency": [StepSpec("a", "quick", "a", depends=["nonexistent"])],
        "cycle": [StepSpec("a", "quick", "a", depends=["b"]), StepSpec("b", "quick", "b", depends=["a"])],
        "duplicate_key": [StepSpec("a", "quick", "a"), StepSpec("a", "quick", "a again")],
    }
    good = [StepSpec("a", "quick", "a"), StepSpec("b", "quick", "b", depends=["a"])]
    out = {}
    for name, specs in list(bad.items()) + [("valid", good)]:
        engine.register(Workflow(kind=f"pv_{name}", title="t", plan=lambda p, s=specs: s, handlers={"quick": quick},
                                 describe=lambda p: "plan validity", summarize=lambda ctx: "done"))
        before = len(ran)
        try:
            m = engine.create(f"pv_{name}", {})
            refused = False
        except ValueError:
            m, refused = None, True
        end = time.time() + 5
        while m and time.time() < end and store().mission(m["id"])["state"] == "running":
            time.sleep(0.05)
        out[name] = {"refused": refused, "steps_run": len(ran) - before}
    invalid = [k for k in bad]
    return {"invalid_plans": len(invalid), "rejected_before_run": sum(out[k]["refused"] and out[k]["steps_run"] == 0
                                                                       for k in invalid),
            "valid_plan_accepted": not out["valid"]["refused"] and out["valid"]["steps_run"] == 2, "detail": out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label")
    a = ap.parse_args()
    res = {"plans": eval_plans(), "goals": eval_goals(), "goals_heldout": eval_goals(HELDOUT)}
    res["commit"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                   cwd=str(ROOT)).stdout.strip()
    res["when"] = time.strftime("%Y-%m-%d %H:%M")
    print(json.dumps({k: v for k, v in res.items()}, indent=1, default=str)[:6000])
    if a.label:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"intel-{a.label}.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    sys.stdout.flush()
    import os

    os._exit(0)


if __name__ == "__main__":
    main()
