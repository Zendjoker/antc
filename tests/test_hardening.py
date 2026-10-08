"""Final hardening of mission crash recovery: ownership, Gmail reconciliation, Claude Code edits, hand-edit conflicts,
runtime reconciliation, observability, database rollback.

Real process termination (tests/recovery_child.py, killed at named crash points with os._exit) and real concurrency
(threads, two processes racing for the missions lock). Every provider is a fake:
    models       real SDK clients on a mock transport
    Claude Code  the process layer (sandbox.run) faked: it edits the sandbox copy and reports a cost in the CLI's JSON
    Gmail        (a) a JSON-file fake shared with the child process, (b) the REAL GmailService code on a fake HTTP
                 session that behaves like the Gmail API (search index lag, a rewritten Message-ID, pagination)
Nothing leaves the machine; nothing is sent; no paid call. Real-Gmail behaviour is NOT verified here
(tests/gmail_live_check.py does that, only when explicitly authorized).

Run:  .venv\\Scripts\\python -m tests.test_hardening
"""

import base64
import email
import email.policy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from tests.harness import setup_env

setup_env(MISSION_SENDER_NAME="Test Sender", MISSION_SENDER_EMAIL="sender@example.org",
          MISSION_SENDER_ADDRESS="1 Test Way, Testville", DAILY_BUDGET_USD="50")

ROOT = Path(__file__).resolve().parents[1]
CHILD = ROOT / "tests" / "recovery_child.py"
import room_agent.integrations.google.gmail as gmailmod  # noqa: E402

REAL_GMAIL = gmailmod.GmailService  # (kept before the fakes replace it: section 3 runs the real code)
spec = importlib.util.spec_from_file_location("recovery_child", CHILD)
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)
DRAFTS = rc.install_fakes()
FILE_GMAIL = gmailmod.GmailService
rc.register()

from room_agent import config, control  # noqa: E402
from room_agent.missions import coder, engine, outreach, ownership, sandbox, sitegen  # noqa: E402
from room_agent.missions.store import store  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
S = store()
print(f"isolated: MISSIONS_DB={config.MISSIONS_DB}")
CLI_RUNS = Path(config.MISSIONS_DIR) / "fake_cli_runs.txt"


def crash(scenario, point, **env_extra):
    ownership.release()
    env = dict(os.environ, JARVIS_CRASH_AT=point, PYTHONIOENCODING="utf-8", **env_extra)
    p = subprocess.run([sys.executable, str(CHILD), scenario], env=env, capture_output=True, text=True, timeout=180,
                       cwd=str(ROOT))
    mid = next((ln.split()[1] for ln in p.stdout.splitlines() if ln.startswith("MISSION ")), None)
    aid = next((int(ln.split()[1]) for ln in p.stdout.splitlines() if ln.startswith("APPROVAL ")), None)
    return p.returncode, mid, aid, (p.stdout + p.stderr)[-600:]


def wait_state(mid, timeout=60, states=("completed", "finished_with_problems", "cancelled", "paused_budget")):
    end = time.time() + timeout
    while time.time() < end and S.mission(mid)["state"] not in states:
        time.sleep(0.05)
    return S.mission(mid)["state"]


def resume_and_wait(mid):
    engine.resume(mid)
    return wait_state(mid)


def step(mid, key):
    return S.step(mid, key)


def site_of(mid):
    return Path(S.projects(mid)[0]["path"])


def ws_of(mid):
    return Path(S.mission(mid)["workspace"])


def css(mid):
    return (site_of(mid) / "styles.css").read_text(encoding="utf-8")


def ledger_ok(mid):
    m = S.mission(mid)
    ch = S.charges(mid, 1000)
    ops = {o["charge_id"]: o for o in S.ops(mid) if o["charge_id"]}
    want = {"settled": "completed", "released": "failed", "uncertain": "uncertain", "reserved": "running"}
    spent = sum(c["actual_usd"] or 0 for c in ch if c["state"] != "reserved")
    return (abs(m["spent_usd"] - spent) < 1e-9 and abs(m["reserved_usd"]) < 1e-9 and len(ops) == len(ch)
            and all(ops[c["id"]]["state"] == want[c["state"]] for c in ch))


def cli_runs():
    return len(CLI_RUNS.read_text(encoding="utf-8").splitlines()) if CLI_RUNS.exists() else 0


def died(code):
    return code == 86


# =============================================================================================== 1. ownership
print("\n1. One process owns the missions: a second one neither recovers nor runs them")
mid_own = rc.make_mission([("a", "quick", True, [])], "owned elsewhere", start=False)
S.set_state(mid_own, "running", "test")  # (as if the other process were running it right now)
ownership.release()
holder = subprocess.Popen([sys.executable, str(CHILD), "own"], env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT))
line = ""
end = time.time() + 90
while time.time() < end and not line.startswith("OWNED"):
    line = holder.stdout.readline().strip() or line
    if holder.poll() is not None:
        break
t.check("a child process took the missions lock", line.startswith("OWNED"), (line, holder.poll()))
child_token = (S.owner() or {}).get("token")
try:
    ownership.acquire()
    refused = False
except ownership.NotOwner:
    refused = True
t.check("this process can't take it while the child lives (NotOwner)", refused and ownership.token() is None)
notes = engine.load()
t.check("startup recovery is skipped entirely: the other process's running mission is NOT marked interrupted",
        notes and "not recovered" in notes[0] and S.mission(mid_own)["state"] == "running"
        and step(mid_own, "a")["state"] == "pending", (notes, S.mission(mid_own)["state"]))
try:
    engine.create("rec", {"title": "should not start"})
    created = True
except ownership.NotOwner:
    created = False
engine.start_runner()
time.sleep(0.6)
t.check("creating a mission is refused, and no runner starts: the step is NOT executed twice-in-parallel here",
        not created and step(mid_own, "a")["state"] == "pending" and not any(
            e["text"] == "ran a" for e in S.events(mid_own, 100)))
holder.kill()
holder.wait(10)
tok = ownership.acquire(wait_s=15)  # (as at startup: the OS releases a killed owner's lock a moment later)
info = ownership.info()
t.check("after the owner is killed, its lock is NOT stale: this process takes over at once (the previous pid is logged)",
        tok and info["owner"] and line.split()[-1] in (info["previous"] or ""), (line, info))
t.check("...with a NEW fencing token", tok != child_token and S.owner()["token"] == tok)
sid = step(mid_own, "a")["id"]
t.check("fencing: a claim with the dead owner's token is refused, even with the mission running",
        not S.claim_step(sid, mid_own, "stale-run", 1, owner=child_token) and step(mid_own, "a")["state"] == "pending")
toks = []
ths = [threading.Thread(target=lambda: toks.append(ownership.acquire())) for _ in range(8)]
[x.start() for x in ths]
[x.join() for x in ths]
t.check("8 threads acquiring at once in the owner process all get the same token", set(toks) == {tok}, set(toks))
engine.load()
t.check("now recovery runs here: the orphaned 'running' mission is 'interrupted'", S.mission(mid_own)["state"] == "interrupted")
t.check("resumed: the step runs exactly once", resume_and_wait(mid_own) == "completed"
        and sum(e["text"] == "ran a" for e in S.events(mid_own, 100)) == 1)

ownership.release()
racers = [subprocess.Popen([sys.executable, str(CHILD), "own"], env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT)) for _ in range(2)]
end = time.time() + 90
while time.time() < end and all(r.poll() is None for r in racers):
    time.sleep(0.1)
loser = next((r for r in racers if r.poll() is not None), None)
winner = next((r for r in racers if r is not loser), None)
for r in racers:
    if r.poll() is None:
        r.kill()
outs = [r.communicate(timeout=30) for r in racers]
lo = outs[racers.index(loser)] if loser else ("", "")
wo = outs[racers.index(winner)] if winner else ("", "")
t.check("two processes racing for the lock: exactly one owns it, the other stops with NotOwner",
        loser is not None and loser.returncode != 0 and "NotOwner" in lo[1] and "OWNED" in wo[0] and "OWNED" not in lo[0],
        (loser.returncode if loser else None, lo[1][-200:], wo[0][-100:]))
ownership.acquire()

# =============================================================================================== 2. Gmail (file fake)
print("\n2. Gmail reconciliation across a real crash: indexing delay, a rewritten Message-ID, an incomplete scan")


def drafts_for(aid):
    return [d for d in json.loads(DRAFTS.read_text(encoding="utf-8")) if f"-{aid}-" in (d.get("message_id") or "")
            or d.get("marker") == (S.op_by_key(f"gmail:{aid}") or {}).get("id")]


code, mid, aid, out = crash("gmail", "after_gmail_draft", FAKE_GMAIL_INDEX_LAG_S="3600")
engine.load()
t.check("crash right after Gmail created the draft: 'unknown'", died(code) and S.approval(aid)["status"] == "unknown",
        (code, out))
ok, msg = outreach.approve(aid, "test")
t.check("while unknown, approving again is refused (no second draft)", not ok and len(drafts_for(aid)) == 1, msg)
os.environ["FAKE_GMAIL_INDEX_LAG_S"] = "3600"  # (Gmail's search hasn't indexed it yet)
ok, msg = outreach.retry(aid, "test")
del os.environ["FAKE_GMAIL_INDEX_LAG_S"]
op = S.op_by_key(f"gmail:{aid}")
t.check("the search can't see it yet, the drafts scan finds it by its marker: recorded done, still ONE draft",
        ok and S.approval(aid)["status"] == "done" and len(drafts_for(aid)) == 1
        and (op["detail"] or {}).get("reconciled") == "scan", (msg, op["detail"]))

code, mid, aid, out = crash("gmail", "after_gmail_draft", FAKE_GMAIL_REWRITE_MSGID="1")
engine.load()
ok, msg = outreach.retry(aid, "test")
t.check("Gmail rewrote the Message-ID: found by the X-Jarvis-Operation marker, recorded done, ONE draft",
        died(code) and ok and S.approval(aid)["status"] == "done" and len(drafts_for(aid)) == 1, msg)

code, mid, aid, out = crash("gmail", "after_gmail_draft")
engine.load()
saved = DRAFTS.read_text(encoding="utf-8")
mine = [d for d in json.loads(saved) if f"-{aid}-" in d["message_id"]]
others = [{"id": f"x{i}", "to": "a@b.c", "subject": "s", "message_id": f"<other-{i}@x>", "marker": "", "at": 0}
          for i in range(320)]
DRAFTS.write_text(json.dumps(others + mine), encoding="utf-8")  # (ours is beyond the scan limit)
outreach.RECONCILE_MIN_AGE_S = 0
os.environ["FAKE_GMAIL_INDEX_LAG_S"] = "3600"
ok, msg = outreach.retry(aid, "test")
t.check("more drafts than the scan reads and the search lags: 'wait', nothing concluded, nothing created",
        not ok and S.approval(aid)["status"] == "unknown" and "drafts could be checked" in msg, msg)
DRAFTS.write_text(json.dumps(others), encoding="utf-8")  # (now: really absent, but still an incomplete scan)
ok, msg = outreach.retry(aid, "test")
t.check("absent but the scan is incomplete: still 'wait' (never 'absent' from a partial look)",
        not ok and S.approval(aid)["status"] == "unknown", msg)
del os.environ["FAKE_GMAIL_INDEX_LAG_S"]
DRAFTS.write_text(saved, encoding="utf-8")
outreach.RECONCILE_MIN_AGE_S = 600
op = S.op_by_key(f"gmail:{aid}")
S.op_finish(op["id"], "completed", detail={"draft_id": "d-seen"})  # (another check found it a moment ago)
ok, msg = outreach.resolve(aid, "not_created", "test")
t.check("manual 'not created' is refused when Gmail's record shows it was created (evidence beats a guess)",
        not ok and "was created" in msg and S.approval(aid)["status"] == "unknown", msg)
ok, msg = engine.resolve(S.approval(aid)["mission_id"], "approval", str(aid), "mark_created", "test")
t.check("manual 'it's in Gmail' -> done; no draft created by the resolution", ok and S.approval(aid)["status"] == "done"
        and len(drafts_for(aid)) == 1, msg)

# =============================================================================================== 3. real GmailService
print("\n3. The REAL Gmail code (find_draft, create_draft, approve/retry) against a fake Gmail API")


class FakeGmailAPI:
    """The HTTP surface GmailService uses, with: search-index lag, an optional Message-ID rewrite, pagination, a
    lost answer (created, then the request fails), a request that never arrived. Any send call fails the test."""

    def __init__(self):
        self.drafts, self.calls, self.lock = [], [], threading.Lock()
        self.lag, self.rewrite, self.lose_answer, self.never_arrives = 0.0, False, False, False

    @staticmethod
    def _h(d, name):
        return next((x["value"] for x in d["headers"] if x["name"].lower() == name), "")

    def _msg(self, d):
        return {"id": d["mid"], "threadId": "t" + d["mid"], "labelIds": ["DRAFT"], "internalDate": "0",
                "payload": {"mimeType": "text/plain", "headers": d["headers"],
                            "body": {"data": base64.urlsafe_b64encode(d["body"].encode()).decode()}}}

    def add(self, mid_header, marker="", body="x", at=0.0, to="someone@example.org"):
        n = len(self.drafts) + 1
        hs = [{"name": "To", "value": to}, {"name": "Subject", "value": "s"}, {"name": "Message-ID", "value": mid_header}]
        if marker:
            hs.append({"name": "X-Jarvis-Operation", "value": marker})
        self.drafts.append({"id": f"r{n}", "mid": f"m{n}", "headers": hs, "body": body, "at": at})
        return self.drafts[-1]

    def call(self, method, url, params=None, json_body=None, scope_hint=None, **kw):
        path = url.split("/users/me/", 1)[1]
        with self.lock:
            self.calls.append((method, path))
            assert "send" not in path, f"a send call was made: {method} {path}"
            if method == "POST" and path == "drafts":
                if self.never_arrives:
                    raise TimeoutError("the request never reached Gmail")
                msg = email.message_from_bytes(base64.urlsafe_b64decode(json_body["message"]["raw"]),
                                               policy=email.policy.default)
                n = len(self.drafts) + 1
                hs = [{"name": k, "value": str(v)} for k, v in msg.items()]
                if self.rewrite:
                    hs = [h if h["name"].lower() != "message-id" else {"name": h["name"], "value": f"<CA{n}@mail.gmail.com>"}
                          for h in hs]
                d = {"id": f"r{n}", "mid": f"m{n}", "headers": hs, "body": msg.get_content(), "at": time.time()}
                self.drafts.append(d)
                if self.lose_answer:
                    raise TimeoutError("the answer was lost")
                return {"id": d["id"], "message": {"id": d["mid"]}}
            if method == "GET" and path == "drafts":
                p = params or {}
                if "q" in p:
                    want = p["q"].split(":", 1)[1].strip("<>").lower()
                    hits = [d for d in self.drafts if time.time() - d["at"] >= self.lag
                            and self._h(d, "message-id").strip("<>").lower() == want]
                    return {"drafts": [{"id": d["id"], "message": {"id": d["mid"]}} for d in hits]} if hits else {}
                start, size = int(p.get("pageToken") or 0), int(p.get("maxResults", 100))
                out = {"drafts": [{"id": d["id"], "message": {"id": d["mid"]}} for d in self.drafts[start:start + size]]}
                if start + size < len(self.drafts):
                    out["nextPageToken"] = str(start + size)
                return out
            if method == "GET" and path.startswith("drafts/"):
                d = next(x for x in self.drafts if x["id"] == path.split("/")[1])
                return {"id": d["id"], "message": self._msg(d)}
            if method == "GET" and path.startswith("messages/"):
                return self._msg(next(x for x in self.drafts if x["mid"] == path.split("/")[1]))
            raise AssertionError(f"unexpected Gmail call {method} {path}")


API = FakeGmailAPI()


class HttpGmail(REAL_GMAIL):
    def __init__(self, provider=None, account=None):
        self.session, self.account = API, "me"


g = HttpGmail()
d = g.create_draft("owner@cafe.example", "Hello", "Body text", message_id="<jarvis-1-abc@jarvis.invalid>", marker="op1")
made = API.drafts[-1]
t.check("create_draft writes both identifiers into the draft (Message-ID and X-Jarvis-Operation) and reads it back",
        d["id"] == made["id"] and API._h(made, "message-id") == "<jarvis-1-abc@jarvis.invalid>"
        and API._h(made, "x-jarvis-operation") == "op1")
r = g.find_draft(message_id="<jarvis-1-abc@jarvis.invalid>", marker="op1")
t.check("found by the Message-ID search", r["found"] == made["id"] and r["how"] == "search", r)
API.lag = 3600
r = g.find_draft(message_id="<jarvis-1-abc@jarvis.invalid>", marker="op1")
t.check("search index lagging: found by scanning the drafts' headers", r["found"] == made["id"] and r["how"] == "scan", r)
API.lag, API.rewrite = 0, True
g.create_draft("owner@cafe.example", "Hello", "Body two", message_id="<jarvis-2-def@jarvis.invalid>", marker="op2")
r = g.find_draft(message_id="<jarvis-2-def@jarvis.invalid>", marker="op2")
t.check("Message-ID rewritten by Gmail: the search misses, the marker scan finds it",
        r["found"] == API.drafts[-1]["id"] and r["how"] == "scan", r)
API.rewrite = False
for i in range(250):
    API.drafts.insert(0, {"id": f"z{i}", "mid": f"zm{i}", "headers": [{"name": "Message-ID", "value": f"<z{i}@x>"}],
                          "body": "", "at": 0})
r = g.find_draft(message_id="<nope@x>", marker="op2")
t.check("pagination: found on the 3rd page of drafts (252 scanned)", r["found"] and r["scanned"] == 252, r)
r = g.find_draft(message_id="<nope@x>", marker="op-absent")
t.check("absent with every page read: complete=True, found=None", r["found"] is None and r["complete"], r)
r = g.find_draft(message_id="<nope@x>", marker="op-absent", scan_limit=150)
t.check("absent but the scan stopped at its limit: complete=False (can't conclude 'absent')",
        r["found"] is None and not r["complete"], r)
del API.drafts[:250]

gmailmod.GmailService = HttpGmail
LEAD_N = [0]


def new_approval():
    from room_agent.missions.outreach import prepare

    LEAD_N[0] += 1
    m = rc.make_mission([], "gmail http", start=False)
    lid, _ = S.upsert_lead({"key": f"osm:node/h{m}{LEAD_N[0]}", "name": f"Http Diner {LEAD_N[0]}", "category": "restaurant",
                            "email": f"owner{LEAD_N[0]}@httpdiner.example",
                            "sources": ["https://www.openstreetmap.org/node/3"]}, m)
    o, _ = prepare(S.lead(lid), m, ws_of(m), has_demo=False)
    return m, o["approval_id"]


def http_drafts(aid):
    op_ids = {o["id"] for o in S.ops() if o["kind"] == "gmail_draft" and o["what"] == f"Gmail draft for approval #{aid}"}
    return [d for d in API.drafts if f"-{aid}-" in API._h(d, "message-id") or API._h(d, "x-jarvis-operation") in op_ids]


m1, a1 = new_approval()
API.lose_answer = True
ok, msg = outreach.approve(a1, "test")
API.lose_answer = False
t.check("real code, the answer lost after Gmail created the draft: 'unknown'", not ok
        and S.approval(a1)["status"] == "unknown" and len(http_drafts(a1)) == 1, msg)
ok, msg = engine.resolve(m1, "approval", str(a1), "check", "test")
t.check("check (read-only): found -> done, still exactly ONE draft", ok and S.approval(a1)["status"] == "done"
        and len(http_drafts(a1)) == 1, msg)
m2, a2 = new_approval()
API.lose_answer, API.rewrite, API.lag = True, True, 3600
outreach.approve(a2, "test")
API.lose_answer = False
ok, msg = outreach.retry(a2, "test")
API.rewrite, API.lag = False, 0
t.check("real code, lost answer + rewritten Message-ID + lagging search: found by the marker, done, ONE draft",
        ok and S.approval(a2)["status"] == "done" and len(http_drafts(a2)) == 1, msg)
m3, a3 = new_approval()
API.never_arrives = True
outreach.approve(a3, "test")
API.never_arrives = False
ok, msg = outreach.retry(a3, "test")
t.check("the request never arrived; checked right away: 'wait' (too recent to call it absent), nothing created",
        not ok and S.approval(a3)["status"] == "unknown" and not http_drafts(a3), msg)
outreach.RECONCILE_MIN_AGE_S = 0
ok, msg = outreach.retry(a3, "test")
t.check("old enough + complete scan: absent -> waiting for approval; retry itself created nothing",
        ok and S.approval(a3)["status"] == "pending" and not http_drafts(a3), msg)
ok, msg = outreach.approve(a3, "test")
t.check("a new explicit approve creates it exactly once", ok and S.approval(a3)["status"] == "done"
        and len(http_drafts(a3)) == 1, msg)

print("   concurrency: retry / approve / manual resolution racing on the same unknown draft (5 rounds)")
worst, consistent = 0, True
for rnd in range(5):
    m, a = new_approval()
    API.never_arrives = True
    outreach.approve(a, "test")
    API.never_arrives = False
    barrier = threading.Barrier(10)

    def go(fn, *args):
        barrier.wait()
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001
            print("   race error:", e.__class__.__name__, e)

    jobs = ([(outreach.retry, a, "t")] * 3 + [(outreach.approve, a, "t")] * 4 + [(outreach.resolve, a, "not_created", "t")] * 2
            + [(engine.resolve, m, "approval", str(a), "check", "t")])
    ths = [threading.Thread(target=go, args=j) for j in jobs]
    [x.start() for x in ths]
    [x.join(30) for x in ths]
    n = len(http_drafts(a))
    worst = max(worst, n)
    st = S.approval(a)["status"]
    consistent &= (st == "done" and n == 1) or (st in ("pending", "unknown") and n == 0) or (st == "unknown" and n == 1)
    if st == "pending":
        outreach.approve(a, "t")
        worst = max(worst, len(http_drafts(a)))
t.check("at most ONE draft per approval in every round, and the recorded state matches Gmail", worst <= 1 and consistent,
        (worst, consistent))
t.check("no send call was ever made to Gmail", not [c for c in API.calls if "send" in c[1]])
outreach.RECONCILE_MIN_AGE_S = 600
gmailmod.GmailService = FILE_GMAIL

# =============================================================================================== 4. Claude Code edits
print("\n4. Claude Code edit, killed before generation / after generation / before, during, after the site swap")
saved_cfg = (config.CODER_BACKEND, config.CODER_MAX_USD, shutil.which, sandbox.run, sandbox.git_bash)
rc.install_cli_fakes()  # (this process resumes the missions: same fake worker)


def cli_edited(mid):
    return "edited by the CLI recovery test" in css(mid)


def fingerprint_ok(mid):
    return sitegen.known_hash(ws_of(mid), site_of(mid).name) == sitegen.live_hash(site_of(mid))


CLI_RUNS.unlink(missing_ok=True)
code, mid, _, out = crash("cli_edit", "before_generation")
engine.load()
t.check("killed before generation: nothing ran, nothing charged, the step is pending", died(code) and cli_runs() == 0
        and not S.charges(mid, 10) and step(mid, "edit")["state"] == "pending", (code, out, step(mid, "edit") if mid else ""))
t.check("resumed: Claude Code runs once, one charge (the cost the CLI reported), one version, fingerprint recorded",
        resume_and_wait(mid) == "completed" and cli_runs() == 1 and len(S.charges(mid, 10)) == 1
        and S.charges(mid, 10)[0]["basis"] == "provider_reported" and abs(S.charges(mid, 10)[0]["actual_usd"] - 0.0425) < 1e-9
        and cli_edited(mid) and len(sitegen.versions(ws_of(mid), site_of(mid).name)) == 1 and fingerprint_ok(mid),
        (S.mission(mid)["state"], cli_runs(), [dict(c) for c in S.charges(mid, 10)]))
CLI_MID_OK = mid

CLI_RUNS.unlink(missing_ok=True)
code, mid, _, out = crash("cli_edit", "after_generation")
engine.load()
ch = S.charges(mid, 10)
t.check("killed after Claude Code ran (cost not recorded): uncertain, charged at the reservation, labelled ESTIMATE",
        died(code) and cli_runs() == 1 and step(mid, "edit")["state"] == "uncertain" and len(ch) == 1
        and ch[0]["state"] == "uncertain" and ch[0]["basis"] == "estimate" and abs(ch[0]["actual_usd"] - 0.25) < 1e-9
        and not cli_edited(mid), (code, out, [dict(c) for c in ch]))
t.check("resumed: Claude Code is NOT run again automatically; the mission reports a problem",
        resume_and_wait(mid) == "finished_with_problems" and cli_runs() == 1 and not cli_edited(mid))
ok, msg = engine.resolve(mid, "step", "edit", "retry", "test")
state = wait_state(mid)
ch = S.charges(mid, 10)
t.check("only when YOU choose retry: it runs once more (paid again, shown as a 2nd charge), applied once",
        ok and state == "completed" and cli_runs() == 2 and len(ch) == 2 and cli_edited(mid)
        and sorted(c["state"] for c in ch) == ["settled", "uncertain"]
        and len(sitegen.versions(ws_of(mid), site_of(mid).name)) == 1 and ledger_ok(mid),
        (msg, state, cli_runs(), [(c["state"], c["basis"]) for c in ch]))
CLI_MID_UNC = mid

for point, label in (("before_site_swap", "the checked result stored, not applied"),
                     ("during_site_swap", "between the two renames"),
                     ("after_site_swap", "applied, not recorded")):
    CLI_RUNS.unlink(missing_ok=True)
    code, mid, _, out = crash("cli_edit", point)
    notes = engine.load()
    t.check(f"killed {point} ({label}): Claude Code ran once, one settled charge", died(code) and cli_runs() == 1
            and [c["state"] for c in S.charges(mid, 10)] == ["settled"], (code, out[-200:], cli_runs()))
    state = resume_and_wait(mid)
    t.check(f"...after restart + resume: completed, applied ONCE, never re-generated, fingerprint = live site",
            state == "completed" and cli_runs() == 1 and len(S.charges(mid, 10)) == 1 and cli_edited(mid)
            and len(sitegen.versions(ws_of(mid), site_of(mid).name)) == 1 and fingerprint_ok(mid) and ledger_ok(mid),
            (state, cli_runs(), len(sitegen.versions(ws_of(mid), site_of(mid).name)), notes[-2:]))
config.CODER_BACKEND, config.CODER_MAX_USD, shutil.which, sandbox.run, sandbox.git_bash = saved_cfg
coder.shutil.which = shutil.which

# =============================================================================================== 5. hand edits
print("\n5. A person edits the site while an edit is being recovered")
HUMAN = "\n/* edited BY HAND during recovery */\n"


def hand_edit(mid, text=HUMAN):
    p = site_of(mid) / "styles.css"
    p.write_text(p.read_text(encoding="utf-8") + text, encoding="utf-8")


for choice in ("keep_mine", "use_jarvis"):
    code, mid, _, out = crash("edit", "before_site_swap")
    hand_edit(mid)
    engine.load()
    calls0 = rc.SDK["calls"]
    state = resume_and_wait(mid)
    acts = engine.pending_actions(mid)
    conflict = next((x for x in acts if x["type"] == "conflict"), None)
    t.check(f"[{choice}] resume finds the hand edit: NOT overwritten, Jarvis's version set aside, a conflict reported",
            died(code) and HUMAN.strip() in css(mid) and "edited by the recovery test" not in css(mid)
            and conflict and conflict["actions"] == ["keep_mine", "use_jarvis"] and rc.SDK["calls"] == calls0
            and step(mid, "edit")["state"] == "failed" and "by hand" in step(mid, "edit")["error"],
            (state, step(mid, "edit")["error"][:160] if mid else out, acts))
    ok, msg = engine.resolve(mid, "conflict", conflict["id"] if conflict else "", choice, "test")
    left = sitegen.pending_conflicts(ws_of(mid))
    if choice == "keep_mine":
        t.check("keep my edits: the hand-edited site stays and becomes the baseline; the set-aside version is gone",
                ok and HUMAN.strip() in css(mid) and not left and fingerprint_ok(mid), msg)
        KEEP_MID = mid
    else:
        old = [v for v in sitegen.versions(ws_of(mid), site_of(mid).name)
               if HUMAN.strip() in (v / "styles.css").read_text(encoding="utf-8")]
        t.check("use Jarvis's version: it goes live, the hand-edited one is kept as a version (nothing lost)",
                ok and "edited by the recovery test" in css(mid) and HUMAN.strip() not in css(mid) and old and not left
                and fingerprint_ok(mid), msg)

code, mid, _, out = crash("edit", "after_site_swap")
hand_edit(mid)
engine.load()
st = step(mid, "edit")
t.check("killed after the swap, then edited by hand before restart: reconciled 'uncertain' (conflict), hand edit kept",
        died(code) and st["state"] == "uncertain" and "by hand" in st["error"] and HUMAN.strip() in css(mid), st)
calls0 = rc.SDK["calls"]
ok, msg = engine.resolve(mid, "step", "edit", "accept", "test")
t.check("accept as is: skipped, never re-applied over the hand edit", ok and step(mid, "edit")["state"] == "skipped"
        and resume_and_wait(mid) == "completed" and HUMAN.strip() in css(mid) and rc.SDK["calls"] == calls0, msg)

ws, sl = ws_of(KEEP_MID), site_of(KEEP_MID).name
hand_edit(KEEP_MID, "\n/* second hand edit */\n")
files = {p.name: p.read_bytes() for p in site_of(KEEP_MID).iterdir() if p.is_file() and not p.name.startswith(".")}
files["styles.css"] += b"\n/* jarvis v3 */\n"
staged = sitegen.stage(ws, sl, files)
half = ws / "sites" / ".versions" / sl / "20991231-235959-half-swap"
os.rename(site_of(KEEP_MID), half)  # (a crash between the two renames, over a hand-edited site)
notes = sitegen.recover(ws)
t.check("half-done swap over a hand-edited site: recovery puts the PERSON's version back and sets Jarvis's aside",
        "second hand edit" in css(KEEP_MID) and "jarvis v3" not in css(KEEP_MID)
        and any("conflict" in n for n in notes) and sitegen.pending_conflicts(ws), notes)
sitegen.resolve_conflict(ws, sl, "keep_mine", None)
staged = sitegen.stage(ws, sl, files)
os.rename(site_of(KEEP_MID), ws / "sites" / ".versions" / sl / "20991231-235960-half-swap")
notes = sitegen.recover(ws)
t.check("half-done swap with NO hand edit: recovery finishes it and records the new fingerprint",
        "jarvis v3" in css(KEEP_MID) and any("finished" in n for n in notes) and fingerprint_ok(KEEP_MID), notes)
hand_edit(KEEP_MID, "\n/* third hand edit */\n")
lead = S.lead(S.projects(KEEP_MID)[0]["lead_id"])
_, problems = sitegen.build(lead, ws)
t.check("a regenerate (redesign) over a hand-edited site is refused as a conflict, the hand edit kept",
        problems and "by hand" in problems[0] and "third hand edit" in css(KEEP_MID), problems)
sitegen.resolve_conflict(ws, sl, "keep_mine", None)

# =============================================================================================== 6. runtime reconcile
print("\n6. Reconciliation without restarting: read-only checks, nothing retried by itself")
code, mid, _, out = crash("crash_mid_nonidem", "during_operation")
engine.load()
calls0 = rc.SDK["calls"]
notes = engine.reconcile_now(mid)
t.check("reconcile_now on an uncertain step: still uncertain, nothing ran (no model call, not started again)",
        step(mid, "x")["state"] == "uncertain" and rc.SDK["calls"] == calls0
        and sum(e["text"] == "started x" for e in S.events(mid, 200)) == 1, notes)
acts = engine.pending_actions(mid)
t.check("it's listed as needing a decision, with the choices retry / accept",
        any(x["type"] == "step" and x["id"] == "x" and x["actions"] == ["retry", "accept"] for x in acts), acts)
ok, msg = engine.resolve(mid, "step", "x", "accept", "test")
t.check("accept -> skipped; resuming runs only the dependent step", ok and step(mid, "x")["state"] == "skipped"
        and resume_and_wait(mid) == "completed" and sum(e["text"] == "started x" for e in S.events(mid, 200)) == 1
        and sum(e["text"] == "ran z" for e in S.events(mid, 200)) == 1, msg)
ok, msg = engine.resolve(mid, "step", "x", "retry", "test")
t.check("a step that isn't waiting for a decision can't be retried", not ok, msg)

code, mid, _, out = crash("edit", "after_external_response")
engine.load()
calls0 = rc.SDK["calls"]
engine.reconcile_now()
t.check("reconcile_now (all missions) on a lost paid answer: still uncertain, no model call",
        step(mid, "edit")["state"] == "uncertain" and rc.SDK["calls"] == calls0)
rc.SDK["reply"] = json.dumps({"files": {"styles.css": css(mid) + "\n/* edited by the recovery test */\n"},
                              "summary": "edited"})  # (what the model answers this time)
ok, msg = engine.resolve(mid, "step", "edit", "retry", "test")
state = resume_and_wait(mid) if S.mission(mid)["state"] == "interrupted" else wait_state(mid)
ch = S.charges(mid, 10)
t.check("retry chosen: the model is called ONCE more, the edit applied once, two charges on record, ledger consistent",
        ok and state == "completed" and rc.SDK["calls"] == calls0 + 1 and len(ch) == 2
        and "edited by the recovery test" in css(mid) and ledger_ok(mid), (msg, state, rc.SDK["calls"] - calls0, len(ch)))
UNC_EDIT_MID = mid

code, mid, aid, out = crash("gmail", "after_gmail_draft")
engine.load()
n0 = len(json.loads(DRAFTS.read_text(encoding="utf-8")))
notes = engine.reconcile_now(mid)
t.check("reconcile_now on an unknown Gmail draft that exists: done by reading Gmail, nothing created",
        S.approval(aid)["status"] == "done" and len(json.loads(DRAFTS.read_text(encoding="utf-8"))) == n0, notes)
code, mid, aid, out = crash("gmail", "after_gmail_draft")
engine.load()
d = json.loads(DRAFTS.read_text(encoding="utf-8"))
DRAFTS.write_text(json.dumps(d[:-1]), encoding="utf-8")
notes = engine.reconcile_now(mid)
t.check("...one that is absent but recent: left 'unknown' (wait), nothing created", S.approval(aid)["status"] == "unknown"
        and len(json.loads(DRAFTS.read_text(encoding="utf-8"))) == len(d) - 1, notes)
ok, msg = engine.resolve(mid, "approval", str(aid), "mark_not_created", "test")
t.check("manual 'not in Gmail' -> waiting for approval again: the approval requirement is kept, nothing created",
        ok and S.approval(aid)["status"] == "pending" and len(json.loads(DRAFTS.read_text(encoding="utf-8"))) == len(d) - 1, msg)

# =============================================================================================== 7. observability
print("\n7. What the dashboard shows")
det = control.mission_detail(UNC_EDIT_MID)
op_keys = {k for o in det["operations"] for k in o}
dump = json.dumps(det, default=str)
t.check("operations: id, kind, what, state, reason, times only - no results, payloads or details",
        det["operations"] and op_keys == {"id", "kind", "what", "state", "error", "created", "updated"}, op_keys)
t.check("no keys / tokens / stored edit results in the detail", "sk-test" not in dump and "sk-ant" not in dump
        and "Bearer" not in dump and '"result"' not in json.dumps(det["operations"]))
bases = {c["state"]: c["basis"] for c in det["charges"]}
t.check("charges say what their amount is based on: the provider's usage vs a local estimate",
        bases.get("uncertain") == "estimate" and bases.get("settled") == "provider_usage", bases)
det = control.mission_detail(CLI_MID_UNC)
t.check("a Claude Code charge: the cost the provider REPORTED; the lost one: ESTIMATE",
        sorted(c["basis"] for c in det["charges"]) == ["estimate", "provider_reported"], det["charges"])
t.check("uncertain charges are listed as needing attention (no action: check the provider's usage page)",
        any(x["type"] == "charge" and not x["actions"] for x in det["attention"]), det["attention"])
t.check("ownership is shown (this process owns the missions)", det["owner"]["owner"] is True, det["owner"])

# =============================================================================================== 8. DB rollback
print("\n8. Database rollback: a failure inside a transaction leaves no partial state")
mid = rc.make_mission([("r", "quick", True, [])], "rollback", start=False)
S.db.execute("CREATE TRIGGER t_fail BEFORE INSERT ON transitions BEGIN SELECT RAISE(ABORT, 'test failure'); END")
S.db.commit()
try:
    S.set_state(mid, "running", "test")
    raised = False
except Exception:  # noqa: BLE001
    raised = True
t.check("a mission state change whose log entry fails: rolled back (state unchanged, no log entry)",
        raised and S.mission(mid)["state"] == "planned" and not S.transitions(mid))
S.db.execute("DROP TRIGGER t_fail")
S.db.commit()
S.set_state(mid, "running", "test")
sid = step(mid, "r")["id"]
t.check("(claimed by the owner)", S.claim_step(sid, mid, "run-1", 1, owner=ownership.token()))
S.db.execute("CREATE TRIGGER t_fail2 BEFORE INSERT ON steps BEGIN SELECT RAISE(ABORT, 'test failure'); END")
S.db.commit()
try:
    S.complete_step(sid, "run-1", {"state": "completed", "evidence": "x"},
                    adds=[engine.StepSpec("r:c1", "quick", "child 1"), engine.StepSpec("r:c2", "quick", "child 2")])
    raised = False
except Exception:  # noqa: BLE001
    raised = True
t.check("a step completion whose follow-up insert fails: rolled back (still running, no follow-ups, no partial child)",
        raised and step(mid, "r")["state"] == "running" and step(mid, "r:c1") is None and step(mid, "r:c2") is None)
S.db.execute("DROP TRIGGER t_fail2")
S.db.commit()
t.check("...and the same completion then commits whole", S.complete_step(sid, "run-1", {"state": "completed"},
        adds=[engine.StepSpec("r:c1", "quick", "child 1")]) and step(mid, "r:c1")["state"] == "pending")
engine.stop(mid, "test")

print("\n9. Audit")
t.check("no operation left 'running'", not S.ops(states=("running",)), [(o["kind"], o["what"]) for o in S.ops(states=("running",))])
t.done("HARDENING TESTS")
