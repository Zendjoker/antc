"""Mission benchmark: how well Jarvis completes a real business goal end to end, offline and repeatable.

The goal (from the brief):
    "Find 20 local businesses that lack a verified website, research them, prioritize the best opportunities, build five
     demo websites, and prepare personalized outreach drafts."

Each scenario runs in its own process against a fresh temporary missions database and a fake world (tests/fake_world.py:
fake OpenStreetMap, websites, web search; the model that polishes outreach wording is a fake SDK transport whose usage
numbers are priced by the real price table). Nothing is sent, published or paid for.

    dense      enough qualifying businesses within the default 3 km
    sparse     too few within 3 km, enough within 6 km
    failures   dense + the first web search fails for 30% of businesses + one demo build fails its checks
    crash      dense + a simulated restart part-way through research (Jarvis restarts, the mission is resumed)

Scored from the database, the files and the fake world's ground truth, never from what Jarvis says:
    success                 20 truly qualifying businesses chosen, 5 demos that pass the site checks, 5 outreach
                            drafts written, 0 emails sent / drafts created in Gmail
    completion              the average of the three deliverable ratios (each capped at 1)
    false_qualified         chosen businesses that actually HAVE a working site of their own (unsupported facts)
    unsupported_claims      numbers in the final summary larger than what verifiably exists
    duplicate_fetches       the same URL fetched more than once (robots.txt excluded)
    paid calls, cost        from the mission ledger
    interventions           clarification answers + resumes / decisions the user had to give
    wall_s                  wall-clock time of the mission

    .venv\\Scripts\\python -m tests.mission_benchmark --label baseline        all scenarios -> tests/benchmarks/<label>.json
    .venv\\Scripts\\python -m tests.mission_benchmark --compare baseline upgraded
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "benchmarks"
GOAL_TEXT = ("Find 20 local businesses that lack a verified website, research them, prioritize the best opportunities, "
             "build five demo websites, and prepare personalized outreach drafts.")
ANSWERS = {"category": "restaurants", "location": "Testville"}  # what the user answers if asked (and only then)
GOLD = {"category": "restaurants", "location": "Testville", "count": 20, "website_filter": "none", "demos": 5,
        "outreach": True}
TARGET = {"qualified": 20, "demos": 5, "drafts": 5}
SCENARIOS = {
    "dense": {"world": {"n": 90, "spacing_km": 0.05}},
    "sparse": {"world": {"n": 90, "spacing_km": 0.125}},
    "failures": {"world": {"n": 90, "spacing_km": 0.05}, "fail_search_every": 3, "fail_demo_rank": 2},
    "crash": {"world": {"n": 90, "spacing_km": 0.05}, "restart_after_research": 12},
}
LATENCY_S = 0.03
CONCURRENCY = int(os.environ.get("BENCH_CONCURRENCY", "4"))  # (upgraded mode: MISSION_CONCURRENCY)
PACE_SCALE = 0.02


def run_scenario(name, mode):
    from tests.harness import setup_env

    setup_env(MISSION_SENDER_NAME="Bench Sender", MISSION_SENDER_EMAIL="sender@example.org",
              MISSION_SENDER_ADDRESS="1 Bench Way, Testville", DAILY_BUDGET_USD="50")
    import httpx2
    import openai

    import room_agent.integrations as integ
    import room_agent.integrations.google.gmail as gmailmod
    from room_agent import config
    from room_agent.llm import openai_backend
    from room_agent.missions import business, engine, sitegen
    from room_agent.missions.store import store
    from tests import fake_world

    sc = SCENARIOS[name]
    import tempfile

    assert config.TEST_MODE and tempfile.gettempdir() in str(config.MISSIONS_DB), config.MISSIONS_DB
    world = fake_world.install(fake_world.World(latency_s=LATENCY_S, pace_scale=PACE_SCALE, **sc["world"]))
    if sc.get("fail_search_every"):
        world.fail_search_once = {b["i"] for b in world.biz if b["i"] % sc["fail_search_every"] == 0}
    engine._announce = lambda text, mid: None
    engine.BACKOFF_S = (0.05, 0.05, 0.05)

    # the light model (outreach wording): a fake transport, priced from its usage numbers by the real price table
    calls = {"n": 0}

    def transport(req):
        calls["n"] += 1
        body = json.loads(req.content or b"{}")
        prompt = body["messages"][-1]["content"]
        text = prompt.split("\n\n", 1)[1] if "\n\n" in prompt else prompt
        return httpx2.Response(200, json={"id": "c", "object": "chat.completion", "created": 1, "model": "x",
                                          "choices": [{"index": 0, "finish_reason": "stop",
                                                       "message": {"role": "assistant", "content": text}}],
                                          "usage": {"prompt_tokens": 900, "completion_tokens": 250, "total_tokens": 1150}})

    oai = openai.OpenAI(api_key="sk-test-not-real", base_url="https://api.openai.test/v1",
                        http_client=httpx2.Client(transport=httpx2.MockTransport(transport)), max_retries=0)
    openai_backend.openai_client = lambda: oai
    config.MISSION_LLM_COPY = True
    gmail = {"created": 0, "sent": 0}

    class NoGmail:
        def __init__(self, provider, account=None):
            pass

        def create_draft(self, *a, **k):
            gmail["created"] += 1
            raise RuntimeError("the benchmark never approves a Gmail draft")

    integ.provider = lambda pid: type("P", (), {"can": lambda self, s, lv: True})()
    gmailmod.GmailService = NoGmail
    fail_demo = {}
    if sc.get("fail_demo_rank"):
        real_build = sitegen.build

        def build(lead, workspace, overrides=None):
            rank = ((lead.get("extra") or {}).get("rank") or {})
            if any(r == sc["fail_demo_rank"] for r in rank.values()) and not overrides:
                fail_demo["lead"] = lead["name"]
                folder = sitegen.sites_dir(workspace) / sitegen.slug(lead["name"], lead["id"])
                return folder, ["the generated site failed its contrast check (simulated)"]
            return real_build(lead, workspace, overrides)

        sitegen.build = build

    S = store()
    interventions = 0
    t0 = time.time()
    if mode == "baseline":
        mid = business.start(dict(GOLD), 1.0)["id"]
    else:
        from room_agent.missions import goals

        config.MISSION_CONCURRENCY = CONCURRENCY
        g = goals.interpret(GOAL_TEXT)
        if g.questions:
            interventions += 1  # (the user answers the clarification)
            g = goals.interpret(GOAL_TEXT, answers=ANSWERS)
        mid = goals.start(g, 1.0)["id"]

    def done():
        return S.mission(mid)["state"] in ("completed", "finished_with_problems", "cancelled", "paused_budget",
                                           "paused_daily", "paused", "interrupted")

    if sc.get("restart_after_research"):
        end = time.time() + 300
        while time.time() < end and sum(1 for st in S.steps(mid) if st["kind"] == "research"
                                        and st["state"] == "completed") < sc["restart_after_research"]:
            time.sleep(0.02)
        engine.load()  # (Jarvis restarts: the running mission comes back 'interrupted')
        interventions += 1
        engine.resume(mid)
    end = time.time() + 600
    while time.time() < end and not done():
        time.sleep(0.05)
    while time.time() < end and S.mission(mid)["state"] == "running":
        time.sleep(0.05)
    wall = time.time() - t0
    m = S.mission(mid)

    # ---- scoring, from the database + files + the fake world's truth
    chosen = [x for x in S.leads(mission_id=mid, limit=1000) if mid in ((x.get("extra") or {}).get("rank") or {})]
    truly = [x for x in chosen if (world.by_name(x["name"]) or {}).get("kind") in fake_world.QUALIFYING]
    demos_ok = 0
    for p in S.projects(mid):
        lead = S.lead(p["lead_id"])
        owner, _, _ = sitegen.load_owner(Path(m["workspace"]), Path(p["path"]).name)
        if (Path(p["path"]) / "index.html").exists() and not sitegen.check(Path(p["path"]), lead, owner):
            demos_ok += 1
    drafts = sum(1 for o in S.outreach(mid) if o["kind"] == "email" and (
        Path(m["workspace"]) / "outreach" / sitegen.slug(S.lead(o["lead_id"])["name"], o["lead_id"]) / "email.txt").exists())
    got = {"qualified": len(truly), "demos": demos_ok, "drafts": drafts}
    summary = m.get("summary") or ""
    claims = {}
    mm = re.search(r"(\d+) of \d+ wanted qualify", summary)
    if mm:
        claims["qualified"] = int(mm.group(1))
    mm = re.search(r"(\d+) demo sites?", summary)
    if mm:
        claims["demos"] = int(mm.group(1))
    mm = re.search(r"(\d+) outreach drafts?", summary)
    if mm:
        claims["drafts"] = int(mm.group(1))
    unsupported = sum(1 for k, v in claims.items() if v > got[k])
    gets = [u for meth, u in world.http if meth == "GET" and not u.endswith("/robots.txt")]
    dup = len(gets) - len(set(gets))
    dup_urls = sorted({u for u in gets if gets.count(u) > 1})
    charges = S.charges(mid, 10000)
    completion = sum(min(1.0, got[k] / TARGET[k]) for k in TARGET) / len(TARGET)
    success = all(got[k] >= TARGET[k] for k in TARGET) and gmail["created"] == 0 and gmail["sent"] == 0
    return {"scenario": name, "mode": mode, "state": m["state"], "success": success, "completion": round(completion, 3),
            "got": got, "chosen": len(chosen), "false_qualified": len(chosen) - len(truly), "claims": claims,
            "unsupported_claims": unsupported, "http_requests": len(world.http), "duplicate_fetches": dup,
            "searches": len(world.searches), "paid_calls": len(charges), "model_calls": calls["n"],
            "cost_usd": round(m["spent_usd"], 6), "interventions": interventions, "wall_s": round(wall, 2),
            "steps": len(S.steps(mid)), "failed_steps": sum(1 for s in S.steps(mid) if s["state"] in ("failed", "blocked")),
            "gmail_created": gmail["created"], "demo_failure_injected": fail_demo.get("lead", ""),
            "dup_urls": dup_urls[:10], "research_steps_run": sum(1 for s_ in S.steps(mid) if s_["kind"] == "research"
                                                                 and s_["state"] in ("completed", "failed"))}


def aggregate(rows):
    n = len(rows)
    succ = sum(r["success"] for r in rows)
    cost = sum(r["cost_usd"] for r in rows)
    return {"scenarios": n, "success_rate": round(succ / n, 3), "mean_completion": round(sum(r["completion"] for r in rows) / n, 3),
            "false_qualified": sum(r["false_qualified"] for r in rows), "unsupported_claims": sum(r["unsupported_claims"] for r in rows),
            "duplicate_fetches": sum(r["duplicate_fetches"] for r in rows), "http_requests": sum(r["http_requests"] for r in rows),
            "paid_calls": sum(r["paid_calls"] for r in rows), "total_cost_usd": round(cost, 6),
            "cost_per_success_usd": round(cost / succ, 6) if succ else None,
            "interventions": sum(r["interventions"] for r in rows), "total_wall_s": round(sum(r["wall_s"] for r in rows), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label")
    ap.add_argument("--mode", default="auto")
    ap.add_argument("--scenario")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--compare", nargs=2)
    a = ap.parse_args()
    if a.scenario:  # (child process)
        res = run_scenario(a.scenario, a.mode)
        print("RESULT " + json.dumps(res), flush=True)
        os._exit(0)
    if a.compare:
        b, u = (json.loads((OUT / f"{x}.json").read_text(encoding="utf-8")) for x in a.compare)
        print(f"{'metric':28}{a.compare[0]:>14}{a.compare[1]:>14}")
        for k in b["aggregate"]:
            print(f"{k:28}{str(b['aggregate'][k]):>14}{str(u['aggregate'][k]):>14}")
        for rb, ru in zip(b["rows"], u["rows"]):
            print(f"  {rb['scenario']:10} success {rb['success']!s:5} -> {ru['success']!s:5}  got {rb['got']} -> {ru['got']}  "
                  f"wall {rb['wall_s']}s -> {ru['wall_s']}s")
        return 0
    mode = a.mode
    if mode == "auto":
        mode = "upgraded" if (ROOT / "room_agent" / "missions" / "goals.py").exists() else "baseline"
    rows = []
    for name in a.only or SCENARIOS:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, "-m", "tests.mission_benchmark", "--scenario", name, "--mode", mode],
                           capture_output=True, text=True, timeout=900, cwd=str(ROOT), env=env)
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")), None)
        if line is None:
            print(f"{name}: no result\n{(p.stdout + p.stderr)[-1500:]}")
            rows.append({"scenario": name, "mode": mode, "success": False, "completion": 0.0, "error": True,
                         "got": {}, "false_qualified": 0, "unsupported_claims": 0, "duplicate_fetches": 0,
                         "http_requests": 0, "paid_calls": 0, "cost_usd": 0.0, "interventions": 0, "wall_s": 0.0})
            continue
        r = json.loads(line[7:])
        rows.append(r)
        print(f"{name:10} {mode}: success={r['success']} got={r['got']} false_q={r['false_qualified']} "
              f"unsupported={r['unsupported_claims']} dup={r['duplicate_fetches']} cost=${r['cost_usd']} "
              f"interventions={r['interventions']} wall={r['wall_s']}s state={r['state']}")
    out = {"label": a.label or mode, "mode": mode, "when": time.strftime("%Y-%m-%d %H:%M"),
           "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                    cwd=str(ROOT)).stdout.strip(),
           "settings": {"latency_s": LATENCY_S, "pace_scale": PACE_SCALE,
                        "concurrency": CONCURRENCY if mode == "upgraded" else 1}, "rows": rows, "aggregate": aggregate(rows)}
    if a.label:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{a.label}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out["aggregate"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
