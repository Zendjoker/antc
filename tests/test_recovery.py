"""Crash recovery, with real process termination.

For each scenario a CHILD process (tests/recovery_child.py) runs a mission against this suite's temporary missions
database and is killed at a named crash point (os._exit: no finally, no flush, no rollback). This process then plays
"Jarvis starting again": engine.load() recovery, checks, then resumes the mission and checks the end state.

Verified for every scenario: no lost completed work, no duplicated paid call, no double charge, the ledger and the
operations agree, a step is never reported done unless its effect is in place, uncertain outcomes are labelled as such.
Providers are fakes (models on a mock transport, Gmail as a JSON file); nothing leaves the machine.

Run:  .venv\\Scripts\\python -m tests.test_recovery
"""

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from tests.harness import setup_env

setup_env(MISSION_SENDER_NAME="Test Sender", MISSION_SENDER_EMAIL="sender@example.org",
          MISSION_SENDER_ADDRESS="1 Test Way, Testville", DAILY_BUDGET_USD="50")

ROOT = Path(__file__).resolve().parents[1]
CHILD = ROOT / "tests" / "recovery_child.py"
spec = importlib.util.spec_from_file_location("recovery_child", CHILD)
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)
DRAFTS = rc.install_fakes()
rc.register()

from room_agent import config  # noqa: E402
from room_agent.missions import coder, engine, outreach, ownership, sitegen  # noqa: E402
from room_agent.missions.store import store  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
S = store()
print(f"isolated: MISSIONS_DB={config.MISSIONS_DB}")


def crash(scenario, point):
    """Run the scenario in a child process that dies at `point`. -> (exit code, mission id, approval id, output)"""
    ownership.release()  # (this process stops being "the running Jarvis" while the child plays it)
    env = dict(os.environ, JARVIS_CRASH_AT=point, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, str(CHILD), scenario], env=env, capture_output=True, text=True, timeout=180,
                       cwd=str(ROOT))
    mid = next((ln.split()[1] for ln in p.stdout.splitlines() if ln.startswith("MISSION ")), None)
    aid = next((int(ln.split()[1]) for ln in p.stdout.splitlines() if ln.startswith("APPROVAL ")), None)
    return p.returncode, mid, aid, (p.stdout + p.stderr)[-600:]


def restart():
    """What Jarvis does when it starts again."""
    return engine.load()


def resume_and_wait(mid, timeout=60):
    engine.resume(mid)
    end = time.time() + timeout
    while time.time() < end:
        if S.mission(mid)["state"] in ("completed", "finished_with_problems", "cancelled", "paused_budget"):
            break
        time.sleep(0.05)
    return S.mission(mid)["state"]


def ran(mid, key, word="ran"):
    return sum(1 for e in S.events(mid, 1000) if e["text"] == f"{word} {key}")


def step(mid, key):
    return S.step(mid, key)


def ledger_consistent(mid):
    """Mission totals == ledger; nothing left reserved; every charge has exactly one operation in the matching state."""
    m = S.mission(mid)
    ch = S.charges(mid, 1000)
    ops = {o["charge_id"]: o for o in S.ops(mid) if o["charge_id"]}
    want = {"settled": "completed", "released": "failed", "uncertain": "uncertain", "reserved": "running"}
    spent = sum(c["actual_usd"] or 0 for c in ch if c["state"] != "reserved")
    ok = (abs(m["spent_usd"] - spent) < 1e-9 and abs(m["reserved_usd"]) < 1e-9 and len(ops) == len(ch)
          and all(ops[c["id"]]["state"] == want[c["state"]] for c in ch))
    return ok, {"spent": m["spent_usd"], "ledger": spent, "reserved": m["reserved_usd"],
                "charges": [(c["state"], c["actual_usd"]) for c in ch], "ops": [(o["state"]) for o in ops.values()]}


def died(code):
    return code == 86


# =============================================================================================== before an operation
print("\n1. Crash BEFORE an operation (a step about to be claimed)")
code, mid, _, out = crash("quick2", "before_operation")
t.check("the child was killed at the crash point", died(code) and mid, (code, out))
restart()
t.check("after restart: mission 'interrupted', nothing ran, both steps pending",
        S.mission(mid)["state"] == "interrupted" and ran(mid, "a") == 0 and step(mid, "a")["state"] == "pending"
        and step(mid, "b")["state"] == "pending", (S.mission(mid)["state"], [(s["key"], s["state"]) for s in S.steps(mid)]))
t.check("resumed: completed, each step ran exactly once", resume_and_wait(mid) == "completed"
        and ran(mid, "a") == 1 and ran(mid, "b") == 1, (S.mission(mid)["state"], ran(mid, "a"), ran(mid, "b")))
tr = [(x["from_state"], x["to_state"]) for x in S.transitions(mid)]
t.check("every mission state change is in the transition log (planned->running->interrupted->running->completed)",
        tr == [("planned", "running"), ("running", "interrupted"), ("interrupted", "running"), ("running", "completed")], tr)
Q_MID = mid

# =============================================================================================== during an operation
print("\n2. Crash DURING an operation")
code, mid, _, out = crash("crash_mid", "during_operation")
restart()
t.check("idempotent step interrupted mid-run -> pending (safe to repeat)", died(code)
        and step(mid, "x")["state"] == "pending", (code, step(mid, "x")["state"] if mid else None, out))
t.check("resumed: completed; the dependent step ran once", resume_and_wait(mid) == "completed" and ran(mid, "y") == 1)
code, mid, _, out = crash("crash_mid_nonidem", "during_operation")
restart()
t.check("NON-idempotent step interrupted mid-run -> 'uncertain' (not failed, not repeated)", died(code)
        and step(mid, "x")["state"] == "uncertain", (code, step(mid, "x")["state"] if mid else None, out))
state = resume_and_wait(mid)
t.check("resumed: it is never run again; its dependent is blocked; the mission reports problems",
        state == "finished_with_problems" and ran(mid, "x", "started") == 1 and step(mid, "z")["state"] == "blocked",
        (state, ran(mid, "x", "started"), step(mid, "z")["state"]))

# =============================================================================================== DB commit
print("\n3. Crash BEFORE / AFTER a database commit (a step that adds follow-up steps)")
code, mid, _, out = crash("fanout", "before_db_commit")
restart()
t.check("before the commit: the step is pending again and its follow-ups don't exist yet", died(code)
        and step(mid, "f")["state"] == "pending" and step(mid, "f:c1") is None, out)
t.check("resumed: it runs again (idempotent), its follow-ups are created once and each runs once",
        resume_and_wait(mid) == "completed" and ran(mid, "f:c1") == 1 and ran(mid, "f:c2") == 1
        and len([s for s in S.steps(mid) if s["key"].startswith("f:c")]) == 2)
code, mid, _, out = crash("fanout", "after_db_commit")
restart()
t.check("after the commit: the step is completed AND its follow-up steps exist (one transaction)", died(code)
        and step(mid, "f")["state"] == "completed" and step(mid, "f:c1") and step(mid, "f:c2"),
        [(s["key"], s["state"]) for s in S.steps(mid)] if mid else out)
t.check("resumed: the completed step isn't run again; the follow-ups run once", resume_and_wait(mid) == "completed"
        and ran(mid, "f") == 1 and ran(mid, "f:c1") == 1 and ran(mid, "f:c2") == 1)

# =============================================================================================== paid calls
print("\n4. Paid calls: after the provider answered / during settlement / before the step's commit")
code, mid, _, out = crash("paid", "after_external_response")
restart()
ch = S.charges(mid, 10)
t.check("crash after the provider answered (nothing persisted): the charge is counted at its full estimate, 'uncertain'",
        died(code) and len(ch) == 1 and ch[0]["state"] == "uncertain" and abs(ch[0]["actual_usd"] - ch[0]["estimate_usd"]) < 1e-12,
        [dict(c) for c in ch])
calls0 = rc.SDK["calls"]
state = resume_and_wait(mid)
t.check("resumed: the paid call is NOT repeated (it may have been billed, its result was lost); the step fails final",
        rc.SDK["calls"] == calls0 and step(mid, "p")["state"] == "failed" and "isn't repeated" in step(mid, "p")["error"],
        (rc.SDK["calls"] - calls0, step(mid, "p")["state"], step(mid, "p")["error"][:100]))
ok, info = ledger_consistent(mid)
t.check("ledger and operations agree (one charge, one 'uncertain' operation, nothing reserved)", ok, info)

code, mid, _, out = crash("paid", "during_settlement")
restart()
ok, info = ledger_consistent(mid)
t.check("crash INSIDE the settlement transaction: rolled back, then settled once at startup (uncertain, full estimate)",
        died(code) and ok and info["charges"] and info["charges"][0][0] == "uncertain", info)

code, mid, _, out = crash("paid", "before_db_commit")
restart()
ch = S.charges(mid, 10)
t.check("crash after the paid call was settled (result stored) but before the step's commit: one settled charge",
        died(code) and len(ch) == 1 and ch[0]["state"] == "settled" and step(mid, "p")["state"] == "pending",
        ([dict(c) for c in ch], step(mid, "p")["state"] if mid else out))
calls0 = rc.SDK["calls"]
state = resume_and_wait(mid)
t.check("resumed: the stored result is REUSED - no second provider call, no second charge, step completed",
        state == "completed" and rc.SDK["calls"] == calls0 and len(S.charges(mid, 10)) == 1
        and step(mid, "p")["result"].get("text") == "ok", (state, rc.SDK["calls"] - calls0, len(S.charges(mid, 10))))
ok, info = ledger_consistent(mid)
t.check("...ledger and operations agree", ok, info)

# =============================================================================================== website editing
print("\n5. Website editing: crash before / during / after the site swap, and after the model answered")


def site_of(mid):
    p = S.projects(mid)[0]
    return Path(p["path"])


def edited(mid):
    return "edited by the recovery test" in (site_of(mid) / "styles.css").read_text(encoding="utf-8")


code, mid, _, out = crash("edit", "before_site_swap")
restart()
t.check("crash with the paid edit result stored, nothing applied: the step is pending ('applied without paying again')",
        died(code) and step(mid, "edit")["state"] == "pending" and "without paying again" in step(mid, "edit")["error"]
        and not edited(mid), (step(mid, "edit")["state"] if mid else out, step(mid, "edit")["error"] if mid else ""))
calls0 = rc.SDK["calls"]
state = resume_and_wait(mid)
t.check("resumed: the edit is applied from the stored result - no new model call, exactly one charge",
        state == "completed" and edited(mid) and rc.SDK["calls"] == calls0 and len(S.charges(mid, 10)) == 1,
        (state, edited(mid), rc.SDK["calls"] - calls0, len(S.charges(mid, 10))))
t.check("...one previous version kept (the edit happened once)", len(sitegen.versions(S.mission(mid)["workspace"],
                                                                                        site_of(mid).name)) == 1)

code, mid, _, out = crash("edit", "during_site_swap")
ws = Path(S.mission(mid)["workspace"]) if mid else None
t.check("crash between the two renames: the live site folder is missing at that moment",
        died(code) and not site_of(mid).exists(), out)
notes = restart()
t.check("restart: the half-done swap is finished, and the edit step is reconciled as COMPLETED (live site = new version)",
        site_of(mid).exists() and edited(mid) and step(mid, "edit")["state"] == "completed", (notes[-3:], step(mid, "edit")))
calls0 = rc.SDK["calls"]
t.check("resumed: nothing is re-done (no model call, no second charge)", resume_and_wait(mid) == "completed"
        and rc.SDK["calls"] == calls0 and len(S.charges(mid, 10)) == 1)

code, mid, _, out = crash("edit", "after_site_swap")
restart()
t.check("crash after the swap, before it was recorded: reconciled COMPLETED from the live site's content (not 'failed')",
        died(code) and edited(mid) and step(mid, "edit")["state"] == "completed", step(mid, "edit") if mid else out)
calls0 = rc.SDK["calls"]
t.check("resumed: not applied twice (one version, no model call)", resume_and_wait(mid) == "completed"
        and rc.SDK["calls"] == calls0 and len(sitegen.versions(ws if False else S.mission(mid)["workspace"],
                                                                site_of(mid).name)) == 1)

code, mid, _, out = crash("edit", "after_external_response")
restart()
t.check("crash after the model answered, before its result was stored: step 'uncertain', charge counted in full",
        died(code) and step(mid, "edit")["state"] == "uncertain" and S.charges(mid, 10)[0]["state"] == "uncertain"
        and not edited(mid), (step(mid, "edit")["state"] if mid else out, [dict(c) for c in S.charges(mid, 10)] if mid else ""))
calls0 = rc.SDK["calls"]
state = resume_and_wait(mid)
t.check("resumed: the edit isn't repeated automatically (that would pay again); the mission reports a problem",
        state == "finished_with_problems" and rc.SDK["calls"] == calls0 and not edited(mid), (state, rc.SDK["calls"] - calls0))
ok, info = ledger_consistent(mid)
t.check("...ledger and operations agree", ok, info)

# =============================================================================================== Gmail
print("\n6. External action: crash after Gmail created the draft, before it was recorded")
before = len(json.loads(DRAFTS.read_text(encoding="utf-8"))) if DRAFTS.exists() else 0
code, mid, aid, out = crash("gmail", "after_gmail_draft")
restart()
drafts = json.loads(DRAFTS.read_text(encoding="utf-8"))
t.check("restart: the approval is 'unknown' (never assumed done or not done); the draft exists once in Gmail",
        died(code) and S.approval(aid)["status"] == "unknown" and len(drafts) == before + 1, (code, S.approval(aid), out))
ok, msg = outreach.retry(aid, "test")
drafts = json.loads(DRAFTS.read_text(encoding="utf-8"))
t.check("retry reconciles with Gmail by the recorded Message-ID: recorded as done, NO second draft",
        ok and S.approval(aid)["status"] == "done" and len(drafts) == before + 1 and "no second draft" in msg, (msg, len(drafts)))
code, mid, aid, out = crash("gmail", "after_gmail_draft")
restart()
drafts = json.loads(DRAFTS.read_text(encoding="utf-8"))
DRAFTS.write_text(json.dumps(drafts[:-1]), encoding="utf-8")  # (as if Gmail never got it)
ok, msg = outreach.retry(aid, "test")
t.check("Gmail has no such draft, but the attempt is recent: 'wait' - nothing created, still unknown",
        not ok and S.approval(aid)["status"] == "unknown" and len(json.loads(DRAFTS.read_text(encoding="utf-8"))) == len(drafts) - 1,
        msg)
outreach.RECONCILE_MIN_AGE_S = 0  # (as if the attempt were old enough to be listed)
ok, msg = outreach.retry(aid, "test")
t.check("old enough + a complete scan finds nothing: 'absent' -> waiting for approval again; still NOTHING created",
        ok and S.approval(aid)["status"] == "pending" and len(json.loads(DRAFTS.read_text(encoding="utf-8"))) == len(drafts) - 1,
        msg)
ok, msg = outreach.approve(aid, "test")
drafts = json.loads(DRAFTS.read_text(encoding="utf-8"))
t.check("only a new explicit approve creates it - exactly once", ok and S.approval(aid)["status"] == "done"
        and sum(1 for d in drafts if f"-{aid}-" in d["message_id"]) == 1, (msg, drafts[-2:]))
outreach.RECONCILE_MIN_AGE_S = 600

# =============================================================================================== cancellation
print("\n7. Cancellation after a crash")
code, mid, _, out = crash("quick2", "before_operation")
restart()
t.check("an interrupted mission can be stopped", engine.stop(mid, "test") and S.mission(mid)["state"] == "cancelled")
try:
    engine.add_steps(mid, [engine.StepSpec("late", "quick", "late")])
    closed = False
except engine.MissionClosed:
    closed = True
time.sleep(0.5)
t.check("...then no queued or new work restarts it (resume refused, add refused, nothing ran)",
        closed and not engine.resume(mid) and ran(mid, "a") == 0 and S.mission(mid)["state"] == "cancelled")
code, mid, _, out = crash("fanout", "after_db_commit")
restart()
engine.stop(mid, "test")
t.check("stopping after a crash keeps committed work: the completed step stays completed",
        step(mid, "f")["state"] == "completed" and step(mid, "f:c1")["state"] == "cancelled")

# =============================================================================================== audit
print("\n8. Operation records and audit")
ops = S.ops()
t.check("every operation has a unique stable id", len({o["id"] for o in ops}) == len(ops) and all(len(o["id"]) == 32 for o in ops))
t.check("no operation is left 'running' after recovery", not [o for o in S.ops(states=("running",))],
        [(o["kind"], o["what"]) for o in S.ops(states=("running",))])
dump = json.dumps([dict(o) for o in ops] + [dict(x) for m in S.missions(500) for x in S.transitions(m["id"])], default=str)
t.check("operation and transition records contain no keys / tokens", "sk-test" not in dump and "sk-ant" not in dump
        and "Bearer" not in dump)
_ = coder
t.done("RECOVERY TESTS")
