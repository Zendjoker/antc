"""V2 general intelligence: goal understanding, plan contracts + validation, bounded recovery, supervision, the sandboxed
coding capability, operational experience. Offline: a temporary home folder, fake web / browser, fake coding model.

Run:  .venv\\Scripts\\python -m tests.test_v2_intelligence
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from tests.harness import setup_env

setup_env(CODING_ALLOW_UNSANDBOXED="1")  # (its own temp test project; on Windows the Job sandbox is used anyway)

from tests import sim_pc  # noqa: E402,F401
from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.abilities import tasks as task_tools  # noqa: E402
from room_agent.actions import core, executor, planning, recovery, supervisor, tasks  # noqa: E402
from room_agent.cognition import experience_v2 as xp  # noqa: E402
from room_agent.cognition import understand  # noqa: E402
from room_agent.computer import coding, files  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
core.ensure_loaded()
tasks.BACKOFF_S = (0.01, 0.02)
home = Path(tempfile.mkdtemp(prefix="v2-home-"))
files.HOME = home
files._index_search = lambda q, kind=None, limit=8: None
for d in ("Desktop", "Documents", "Downloads"):
    (home / d).mkdir()


def run(said, steps, goal="test"):
    rt.new_turn(said)
    rt.turn_no += 1
    rt.current_plan = executor.Plan()
    out = task_tools._run({"goal": goal, "steps": steps})
    return out, tasks.get() if not out.startswith(("FAILED: the plan", "NEEDS")) else None


# =============================================================================================== 1. understanding
print("\n1. Goal understanding from their words (no model call)")
s = understand.from_words("Draft an email to alice@example.com about Friday's meeting, but don't send it")
t.check("'don't send it' -> a forbid:send constraint; email family; no send permission implied",
        "forbid:send" in s.constraint_keys() and s.families == ["email"] and "send_email" not in s.permissions, s)
s = understand.from_words("In my Downloads, put the PDFs in a PDFs folder and don't touch the text files")
rule = next(c for c in s.constraints if c.key() == "forbid:touch:.txt")
t.check("'don't touch the text files' blocks moving a .txt, not a .pdf", rule.blocks("move_file", {"file": "a/notes.txt"})
        and not rule.blocks("move_file", {"file": "a/report.pdf"}))
s = understand.from_words("Move only the PNG screenshots from my Desktop into Pictures")
only = next(c for c in s.constraints if c.kind == "only")
t.check("'only the PNG ...' blocks moving a .jpg, allows a .png", only.blocks("move_file", {"file": "x/holiday.jpg"})
        and not only.blocks("move_file", {"file": "x/shot.png"}))
t.check("'send it to him' -> referent + recipient must be asked", set(understand.from_words("send it to him").missing)
        == {"referent", "recipient"})
t.check("'open spotify and move it to my second monitor': 'it' has a referent (nothing to ask)",
        not understand.from_words("open spotify and move it to my second monitor").missing)
t.check("a negated capability doesn't count ('don't open Chrome, just search the web')",
        understand.from_words("don't open Chrome, just search the web for the httpx changelog").families == ["research"])
words = understand.from_words("find florists in Seattle with no website and spend at most $2")
m = understand.merge(words, {"budget_usd": 5, "constraints": ["don't use Google"], "capabilities": ["business"]})
t.check("merge: the model's ungrounded constraint ('don't use Google', never said) is NOT added; budget conflict listed",
        "forbid:google" not in m.constraint_keys() and m.conflicts and "budget" in m.conflicts[0], (m.constraint_keys(), m.conflicts))
config.GOAL_VERIFIER = False
calls = []
sp = understand.second_pass(understand.from_words("send it to him"), ask=lambda txt: calls.append(txt) or {})
t.check("second-pass verifier OFF by default: no model call", not calls)
config.GOAL_VERIFIER = True
sp = understand.second_pass(understand.from_words("send it to him, don't cc anyone"), ask=lambda txt: calls.append(txt) or {
    "constraints": ["don't use Google", "don't cc anyone"], "missing": ["subject"]})
t.check("verifier ON + ambiguous: called once; only constraints grounded in the words are kept; questions can only be "
        "added", len(calls) == 1 and "forbid:google" not in sp.constraint_keys() and "subject" in sp.missing, sp)
calls.clear()
understand.second_pass(understand.from_words("set a timer for 10 minutes"), ask=lambda txt: calls.append(txt) or {})
t.check("verifier ON but nothing ambiguous: no call (cost only when it can help)", not calls)
config.GOAL_VERIFIER = False

# =============================================================================================== 2. enforcement
print("\n2. Their explicit limits hold on every call, even without a tracked goal")
from room_agent import cognition  # noqa: E402

rt.new_turn("draft an email to bob@example.com but don't send it")
rt.turn.goal = None
cognition._state["goal"] = None
refusal = cognition.check_call(core.get("gmail_send"), {"draft_id": "current"})
t.check("gmail_send refused by 'don't send it' (no goal needed)", refusal and "they said" in refusal, refusal)
t.check("...while drafting is still allowed", cognition.check_call(core.get("gmail_create_draft"), {"to": "bob@example.com"}) is None)

# =============================================================================================== 3. plan review
print("\n3. run_task plans are validated before anything runs")
out, _ = run("do things", [{"tool": "teleport", "args": {}}])
t.check("unknown tool -> rejected, nothing run", out.startswith("FAILED") and "teleport" in out, out)
rv = planning.review([{"tool": "save_file", "args": {"name": "a", "content": "x"}, "depends_on": [2]},
                      {"tool": "save_file", "args": {"name": "b", "content": "y"}, "depends_on": [1]}],
                     understand.from_words("save a and b"), {"save_file"})
t.check("a dependency cycle -> rejected", any("cycle" in r for r in rv.rejected), rv.rejected)
out, _ = run("save a note", [{"tool": "save_file", "args": {"name": "n"}}])
t.check("a required input missing -> NEEDS, nothing run", out.startswith("NEEDS") and "content" in out
        and not (home / "Desktop" / "n.md").exists(), out)
out, _ = run("lock my pc then save a note", [{"tool": "lock_pc", "args": {}},
                                             {"tool": "save_file", "args": {"name": "n2", "content": "x"}}])
t.check("a state-changing step nothing can verify -> rejected (lock_pc has no verifiable outcome)",
        out.startswith("FAILED") and "verify" in out and not (home / "Desktop" / "n2.md").exists(), out)
(home / "Documents" / "notes.txt").write_text("call mum", encoding="utf-8")
out, task = run("summarize my notes.txt in the documents folder",
                [{"tool": "read_file", "args": {"file": str(home / "Documents" / "notes.txt")}},
                 {"tool": "delete_file", "args": {"file": str(home / "Documents" / "notes.txt")}, "depends_on": [1]}])
t.check("a step needing a permission their words never implied (delete) is left out, not asked, file kept",
        task and task["steps"][1]["state"] == "SKIPPED" and (home / "Documents" / "notes.txt").exists()
        and "Left out" in out, out[-300:])
out, task = run("make a Trip folder in my documents and save an itinerary in it",
                [{"tool": "save_file", "args": {"name": "itinerary", "content": "Day 1", "folder": "Documents/Trip"}},
                 {"tool": "make_folder", "args": {"name": "Trip", "folder": "documents"}}])
t.check("inferred dependency: saving into a folder made later -> reordered, both done",
        task and task["state"] == "COMPLETED" and (home / "Documents" / "Trip" / "itinerary.md").exists()
        and [s["tool"] for s in task["steps"]] == ["make_folder", "save_file"], out[-300:])
t.check("...every step got its family's default check (evidence from code, not the tool's word)",
        all("success check" in s["evidence"] for s in task["steps"]), [s["evidence"] for s in task["steps"]])
config.TASK_CONTRACTS = config.TASK_RECOVERY = False
out, task = run("make a Trip2 folder in my documents and save an itinerary in it",
                [{"tool": "save_file", "args": {"name": "itinerary", "content": "Day 1", "folder": "Documents/Trip2"}},
                 {"tool": "make_folder", "args": {"name": "Trip2", "folder": "documents"}}])
t.check("TASK_CONTRACTS=0 + TASK_RECOVERY=0 (flags): the old behaviour (in the given order: the save fails)",
        task and task["state"] == "PARTIAL", (task or {}).get("state"))
config.TASK_CONTRACTS = config.TASK_RECOVERY = True
tasks.desk = None
from room_agent.computer.context import desk  # noqa: E402
from room_agent.computer import research as R  # noqa: E402

desk.research = R.Report(question="q", sources=[R.Source(n=1, title="a", url="https://a.example", host="a.example")])
t.check("citations_valid: [2] with one source read -> False; [1] -> True",
        tasks.run_check({"citations_valid": "x [2]"})[0] is False and tasks.run_check({"citations_valid": "x [1]"})[0] is True)

# =============================================================================================== 4. recovery
print("\n4. Bounded recovery: safe alternatives only")
st = {"n": 1, "tool": "gmail_send", "args": {}, "state": "UNKNOWN", "check": None}
t.check("an UNKNOWN outcome is never recovered", recovery.classify(st, "UNKNOWN: not confirmed") == "unknown_outcome"
        and recovery.attempt({}, st, None, "UNKNOWN: x", False, None, None, 3) == (False, ""))
st = {"n": 1, "tool": "move_file", "args": {}, "state": "FAILED", "check": None}
t.check("a refusal by their words is never worked around",
        recovery.attempt({}, st, None, "FAILED: not done: they said \"x\"", False, None, None, 3) == (False, ""))
from room_agent.tools import web  # noqa: E402

web.search = lambda q, news=False: [{"href": "https://evil.example/docs", "title": "x"}]
url, why = recovery._same_site("https://docs.example/a", "streaming docs")
t.check("browser recovery only follows a result on THE SAME SITE (a search result elsewhere is ignored)",
        url is None and "docs.example" in why, why)
st = {"n": 1, "tool": "get_weather", "args": {}, "state": "FAILED", "check": None}
t.check("no recovery budget left -> nothing tried", recovery.attempt({}, st, None, "FAILED: couldn't connect", False,
                                                                     None, None, 0) == (False, ""))

# =============================================================================================== 5. supervision
print("\n5. Supervision (deterministic)")
out, task = run("save a note called dup with hello twice",
                [{"tool": "save_file", "args": {"name": "dup", "content": "hello"}},
                 {"tool": "save_file", "args": {"name": "dup", "content": "hello"}}])
t.check("an exact repeat of a completed step isn't run again", task and task["state"] == "COMPLETED"
        and "not repeated" in task["steps"][1]["result"] and any("repeats" in x for x in task.get("supervisor", [])),
        (task or {}).get("supervisor"))
old = config.TASK_MAX_USD
config.TASK_MAX_USD = -1.0
out, task = run("save notes a3 and b3", [{"tool": "save_file", "args": {"name": "a3", "content": "x"}},
                                          {"tool": "save_file", "args": {"name": "b3", "content": "y"}}])
t.check("over the cost limit: no step starts, the rest is cancelled and said", task and task["state"] == "CANCELED"
        and not (home / "Desktop" / "a3.md").exists(), (task or {}).get("supervisor"))
config.TASK_MAX_USD = old
tt = tasks.new("x", [{"tool": "save_file", "args": {"name": "gone", "content": "x"},
                      "check": {"file_exists": str(home / "Desktop" / "gone.md")}}])
tt["steps"][0]["state"] = "COMPLETED"
t.check("missing output at the end (a produced file is gone) -> not 'done'", supervisor.finish(tt) is False
        and any("missing output" in x for x in tt["supervisor"]))

# =============================================================================================== 6. coding
print("\n6. Coding: sandboxed, allowlisted, verified, approval before changing a project")
proj = home / "projects" / "calc"
proj.mkdir(parents=True)
(proj / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
(proj / "test_calc.py").write_text("import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
                                   "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n", encoding="utf-8")
r = coding.run_tests("projects/calc")
t.check("run_tests: failing test found, in a sandbox copy (the project unchanged)", not r["passed"] and "test_add" in
        " ".join(r["failing"]) and (proj / "calc.py").read_text().count("a - b") == 1, r)
for bad in (str(config.HERE), "C:/Windows", str(Path(tempfile.gettempdir()))):
    try:
        coding.resolve(bad)
        refused = False
    except coding.CodingError:
        refused = True
    t.check(f"workspace boundary: {bad[:30]} refused", refused)
try:
    coding._run(["cmd.exe", "/c", "dir"], Path(tempfile.mkdtemp()))
    allow = False
except coding.CodingError:
    allow = True
t.check("only the allowlisted test commands can run (no shell)", allow)
coding.FIXER = None
res = coding.propose_fix("projects/calc", "fix it")
t.check("no coding model configured (the default): fix_code is UNAVAILABLE, nothing paid", res.get("unavailable"), res)
coding.FIXER = lambda p, i, f, fail: {"test_calc.py": f["test_calc.py"].replace("5", "-1")}
res = coding.propose_fix("projects/calc", "make the tests pass")
t.check("a 'fix' that edits the tests is refused (test files are protected)", not res["ok"] and "protected" in res["message"])
coding.FIXER = lambda p, i, f, fail: {"calc.py": f["calc.py"].replace("a - b", "a + b")}
res = coding.propose_fix("projects/calc", "make the tests pass")
t.check("a real fix: verified in a sandbox, kept as pending, the project NOT changed yet",
        res["ok"] and "a - b" in (proj / "calc.py").read_text() and coding.pending("projects/calc"), res)
(proj / "calc.py").write_text("def add(a, b):\n    return a - b  # hand edit\n", encoding="utf-8")
res = coding.apply_fix("projects/calc")
t.check("the project changed since (hand edit): nothing applied", not res["ok"] and "changed since" in res["message"]
        and "hand edit" in (proj / "calc.py").read_text())
(proj / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
coding.propose_fix("projects/calc", "make the tests pass")
real = coding.run_tests
coding.run_tests = lambda project, overrides=None: {"passed": False, "line": "1 test(s) ran", "failing": ["x"],
                                                    "tail": "", "project": str(proj)} if overrides is None else real(project, overrides)
res = coding.apply_fix("projects/calc")
coding.run_tests = real
t.check("applied but the live tests fail -> the backup is put back (rollback)", not res["ok"] and "put back" in res["message"]
        and "a - b" in (proj / "calc.py").read_text(), res)
coding.propose_fix("projects/calc", "make the tests pass")
res = coding.apply_fix("projects/calc")
t.check("applied: tests pass on the live project, backup kept", res["ok"] and "a + b" in (proj / "calc.py").read_text()
        and Path(res["backup"]).exists(), res)
t.check("apply_code_fix always asks first (SENSITIVE)", core.get("apply_code_fix").risk.name == "SENSITIVE")
(proj / "test_slow.py").write_text("import time, unittest\n\n\nclass S(unittest.TestCase):\n    def test_s(self):\n"
                                   "        time.sleep(30)\n", encoding="utf-8")
coding.TEST_TIMEOUT_S = 2
t0 = time.time()
try:
    coding.run_tests("projects/calc")
    stopped = False
except coding.CodingError as e:
    stopped = "stopped" in str(e)
coding.TEST_TIMEOUT_S = 120
t.check("a test run over its time limit is stopped (process tree killed)", stopped and time.time() - t0 < 20)
(proj / "test_slow.py").unlink()

# =============================================================================================== 7. experience
print("\n7. Operational experience (experience.db)")
xp.forget("all")
done = {"id": "x1", "intent": "save a shopping list file on my desktop", "goal": "save a shopping list", "state": "COMPLETED",
        "steps": [{"tool": "save_file", "state": "COMPLETED", "evidence": "success check: ok", "args": {"content": "SECRET"}}]}
xp.record_task(done)
xp.record_task(dict(done, id="x2"))
hits = xp.relevant("save a shopping list file to my desktop please")
t.check("a verified task becomes a procedure; a similar request finds it (as a hint)", hits and hits[0]["successes"] == 2,
        hits)
procs, _ = xp.listing()
t.check("only tool names / outcomes / their words are stored (no file content)", "SECRET" not in json.dumps(procs))
for i in range(3):
    xp.record_task(dict(done, id=f"f{i}", state="FAILED", steps=[dict(done["steps"][0], state="FAILED")]))
t.check("conflict: a procedure that now fails more than it works isn't offered any more",
        not xp.relevant("save a shopping list file to my desktop please"))
xp.record_pattern("open_url", "wrong_result", "same-site search", True)
xp.record_pattern("open_url", "wrong_result", "other", False)
t.check("failure patterns: the alternative that worked is preferred",
        recovery._prefer_known("open_url", "wrong_result", [("other", []), ("same-site search", [])])[0][0] == "same-site search")
config.EXPERIENCE_TTL_DAYS = 0
t.check("expired knowledge isn't used", xp.pattern_score("open_url", "wrong_result", "same-site search") == 0)
config.EXPERIENCE_TTL_DAYS = 90
t.check("forget_task_knowledge removes it", xp.forget("all") >= 1 and xp.listing() == ([], []))
# =============================================================================================== 8. phase 8
print("\n8. Optional: router escalation (off by default), dashboard plan visibility")
from room_agent.llm import router  # noqa: E402

config.ROUTER_ESCALATION = False
t.check("ROUTER_ESCALATION off (default): a multi-domain request isn't escalated",
        router.escalation("research the radius, fix my geo project and email bob@example.com the result") == "")
config.ROUTER_ESCALATION = True
t.check("ROUTER_ESCALATION on: several capability families -> the smart model, with the reason",
        "several capabilities" in router.escalation("research the radius, fix my geo project and email bob@example.com"))
t.check("...a simple request stays on the default model", router.escalation("set a timer for ten minutes") == "")
config.ROUTER_ESCALATION = False
snap = tasks.snapshot(3)
t.check("dashboard snapshot: per-step verification + reasons, no arguments / results (no payloads)",
        snap and all({"tool", "state", "verified", "why"} == set(st) for x in snap for st in x["steps"])
        and "args" not in json.dumps(snap) and "SECRET" not in json.dumps(snap))
t.done("V2 INTELLIGENCE TESTS")
