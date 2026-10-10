"""P0 safety and reliability fixes, offline: DNS-rebinding guard, emergency stop, audit log, memory hygiene and clean-up,
file saving / moving / deleting, research reports. Nothing on this PC is changed (temp folders, fake engine).

Run:  .venv\\Scripts\\python -m tests.test_safety
"""

import json
import queue
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from tests.harness import setup_env

tmp = setup_env()

from room_agent import audit, config, emergency  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor, journal  # noqa: E402
from room_agent.control import local_host  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

t = Checker()
core.ensure_loaded()

print("DNS rebinding:")
for host, ok in [("127.0.0.1:8765", True), ("localhost:8771", True), ("[::1]:8765", True), ("127.0.0.1", True),
                 ("evil.example.com:8765", False), ("127.0.0.1.evil.com", False), ("", False)]:
    t.check(f"Host {host!r} -> {'allowed' if ok else 'refused'}", local_host(host) == ok)
import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "UI"))
import server as ui  # noqa: E402

client = ui.app.test_client()
from room_agent import localauth  # noqa: E402

client.get(f"/?key={localauth.token()}", headers={"Host": "127.0.0.1:8765"})  # (signed in like the user's browser)
ui.ENV_FILE = Path(tmp) / ".env"  # (never the real .env)
ui.ENV_FILE.write_text("OPENAI_API_KEY=sk-test-not-real-0123456789abcdef\nUNITS=imperial\n", encoding="utf-8")
r = client.get("/api/env", headers={"Host": "attacker.example:8765"})
t.check("the dashboard refuses a page whose Host isn't this PC (can't read settings)", r.status_code == 403)
r = client.get("/api/env", headers={"Host": "127.0.0.1:8765"})
t.check("...and still serves this PC", r.status_code == 200)

print("Emergency stop:")


class FakeEngine:
    def __init__(self):
        self.interrupted, self.flushed = threading.Event(), 0

    def flush(self):
        self.flushed += 1


rt.engine = FakeEngine()
rt.speak_q = queue.Queue()
for s in ("one sentence", "another"):
    rt.speak_q.put(s)
rt.new_turn("research the history of everything")
jid = journal.requested("research_web", {"question": "x"})
journal.state(journal.EXECUTING, jid=jid)
from room_agent.actions import pending  # noqa: E402

pending.confirming("clear_list", {"list": "shopping"})
from room_agent.tools import pcsettings  # noqa: E402

pcsettings.scheduled_power[0] = True
cancelled_power = []
pcsettings.cancel_power = lambda: cancelled_power.append(1) or "OK"
done = emergency.stop_everything("hotkey")
t.check("speech stops at once (engine flushed and interrupted, queued sentences dropped)", rt.engine.flushed == 1
        and rt.engine.interrupted.is_set() and rt.speak_q.empty())
t.check("the current request is cancelled (research / page actions stop between steps)", rt.turn.cancel.is_set())
t.check("a request waiting for a yes is dropped", rt.pending is None)
t.check("a shutdown Jarvis scheduled is cancelled", cancelled_power == [1])
t.check("running actions are marked CANCELED", journal._entries[jid]["state"] == "CANCELED")
ev = [e for e in audit.recent() if e.get("event") == "emergency_stop"]
t.check("...and it's in the audit log, with how it was triggered", ev and ev[-1]["via"] == "hotkey", ev[-1:])
rt.engine = None
convo = Conversation()
convo.say("stop everything", reply="SHOULD NOT BE NEEDED")
t.check("'stop everything' by voice -> instant (no model call), 'Stopped.'", not convo.requests and convo.said() == ["Stopped."],
        convo.said())
from room_agent import control  # noqa: E402

out = control.do({"do": "emergency_stop"})
t.check("the dashboard's Stop button works too", out["ok"] and out["message"].startswith("Stopped"))
t.check("the hotkey parses (Ctrl+Alt+J)", emergency._parse("ctrl+alt+j") == (0x1 | 0x2 | 0x4000, ord("J"))
        and emergency._parse("ctrl+alt") is None)

print("Audit log:")
before = len(audit.recent(500))
rt.new_turn("add milk to my shopping list")
executor.execute("add_to_list", {"item": "milk", "list": "shopping"})
rows = audit.recent(500)[before:]
t.check("a change is audited with the user's words, the action, its args and outcome", rows and rows[-1]["action"] == "add_to_list"
        and rows[-1]["outcome"] == "OK" and rows[-1]["words"] == "add milk to my shopping list" and rows[-1]["args"]["item"] == "milk",
        rows[-1:])
n = len(audit.recent(500))
rt.new_turn("what's on my shopping list")
executor.execute("show_list", {"list": "shopping"})
t.check("a plain read isn't audited (only changes and refusals)", len(audit.recent(500)) == n)
rt.new_turn("set a timer")
executor.execute("read_file", {"file": "secret plans"})
row = audit.recent(1)[0]
t.check("a refused private action: audited by name only (no words, no file names)", row["action"] == "read_file"
        and "words" not in row and isinstance(row["args"], list), row)
rt.pending = None

print("Untrusted content can't act:")
rt.new_turn("summarize this page")
r = executor.execute("remember", {"content": "The user's bank PIN is 1234"})
t.check("a page / email can't plant a memory (their words didn't ask to remember) -> asks first", r.message.startswith("NEEDS_CONFIRMATION"),
        r.message)
rt.pending = None
rt.new_turn("summarize this page")
r = executor.execute("set_wifi", {"on": False, "confidence": 0.99})
t.check("a page can't switch Wi-Fi off", r.message.startswith("NEEDS_CONFIRMATION"), r.message)
rt.pending = None

print("Memory hygiene:")
from room_agent.memory.writer import is_skip, is_transient  # noqa: E402

t.check("'(Summary for memory): SKIP' is recognized as junk (the old filter missed it)", is_skip("(Summary for memory):\nSKIP"))
t.check("...a real summary that mentions skipping is kept", not is_skip("Adam asked to skip the intro, set a pasta timer and talked weather."))
t.check("'On the bed right now' is a moment, not a lasting fact", is_transient("On the bed right now"))
t.check("a quoted phrase isn't a moment ('...move your butt right now')", not is_transient('Preferred roast: "get up right now"', "preference"))
t.check("a plan with a date isn't dropped", not is_transient("Dentist today at 3", "plan"))
from room_agent.memory import cleanup  # noqa: E402

dbp = str(Path(tmp) / "clean.db")
db = sqlite3.connect(dbp)
db.executescript("""CREATE TABLE memories(id INTEGER PRIMARY KEY, category TEXT, key TEXT, content TEXT, value TEXT, source TEXT,
  confidence REAL, created_at TEXT, updated_at TEXT, active INTEGER, superseded_by INTEGER);
  CREATE TABLE summaries(id INTEGER PRIMARY KEY, created_at TEXT, summary TEXT);""")
rows = [(1, "preference", "Wake-up roasts should vary creative phrases each ring until Adam wakes up", "learned", "2026-10-06 18:36"),
        (2, "preference", "Adam wants rotating creative roasts on wake-up timers, varying each ring until he wakes up", "explicit", "2026-10-06 18:40"),
        (3, "person", "Has a pet named Maximus", "learned", "2026-10-05"),
        (4, "fact", "On the bed right now", "user_statement", "2026-10-08")]
for i, cat, content, src, at in rows:
    db.execute("INSERT INTO memories VALUES (?,?,NULL,?,NULL,?,0.8,?,?,1,NULL)", (i, cat, content, src, at, at))
db.execute("INSERT INTO summaries VALUES (1, '2026-10-05', '(Summary for memory): SKIP')")
db.execute("INSERT INTO summaries VALUES (2, '2026-10-05', 'Adam set a stove timer.')")
db.commit()
p = cleanup.plan(db)
db.close()
t.check("duplicates found by meaning; the explicit one is kept", len(p["duplicates"]) == 1 and p["duplicates"][0][0]["id"] == 2
        and [d["id"] for d in p["duplicates"][0][1]] == [1], cleanup.describe(p))
t.check("the moment and the junk summary are found; the pet isn't touched", [r["id"] for r in p["moments"]] == [4]
        and [s for s, _ in p["skip_summaries"]] == [1])
backup = cleanup.apply(dbp, p)
db = sqlite3.connect(dbp)
active = [r[0] for r in db.execute("SELECT id FROM memories WHERE active=1 ORDER BY id")]
t.check("applied: duplicates and the moment hidden (not deleted), backup made", active == [2, 3] and Path(backup).exists()
        and db.execute("SELECT count(*) FROM memories").fetchone()[0] == 4)
db.close()
cleanup.revert(dbp)
db = sqlite3.connect(dbp)
t.check("--revert brings them back", [r[0] for r in db.execute("SELECT id FROM memories WHERE active=1 ORDER BY id")] == [1, 2, 3, 4])
db.close()

print("Files: save, move, delete, report:")
from room_agent.computer import filewrite, files  # noqa: E402

home = Path(tempfile.mkdtemp())
files.HOME = home
for d in ("Desktop", "Documents"):
    (home / d).mkdir()
rt.new_turn("save that as a note on my desktop")
r = executor.execute("save_file", {"name": "groceries", "content": "milk\neggs"})
t.check("saved on the Desktop, checked on disk", r.success and (home / "Desktop" / "groceries.md").read_text() == "milk\neggs", r.message)
r = executor.execute("save_file", {"name": "groceries", "content": "other"})
t.check("an existing file is never overwritten without a yes", not r.success and r.message.startswith("NEEDS")
        and (home / "Desktop" / "groceries.md").read_text() == "milk\neggs", r.message)
r = filewrite.write("x", "y", where="C:\\Windows")
t.check("outside their folders -> refused", not r.startswith("OK"), r)
r = filewrite.write("evil", "y", where=str(home / "Documents" / ".." / ".." / ".."))
t.check("a path escaping their folders -> refused", not r.startswith("OK"), r)
rt.new_turn("move groceries to documents and rename it shopping")
r = executor.execute("move_file", {"file": str(home / "Desktop" / "groceries.md"), "to_folder": "documents",
                                   "new_name": "shopping", "confidence": 0.95})
t.check("move + rename, checked", r.success and (home / "Documents" / "shopping.md").exists()
        and not (home / "Desktop" / "groceries.md").exists(), r.message)
rt.new_turn("delete the shopping file")
r = executor.execute("delete_file", {"file": str(home / "Documents" / "shopping.md")})
t.check("delete -> asks first (always)", r.message.startswith("NEEDS_CONFIRMATION") and (home / "Documents" / "shopping.md").exists())
rt.pending = None
recycled = []
import ctypes  # noqa: E402

real_sh = ctypes.windll.shell32.SHFileOperationW


def fake_sh(op):
    recycled.append(1)
    Path(op._obj.pFrom.rstrip("\0")).unlink()
    return 0


ctypes.windll.shell32.SHFileOperationW = fake_sh
r = filewrite.recycle(home / "Documents" / "shopping.md")
ctypes.windll.shell32.SHFileOperationW = real_sh
t.check("when confirmed: to the Recycle Bin (recoverable), checked gone", r.startswith("OK") and recycled == [1], r)
t.check("folders are never deleted by voice", not filewrite.recycle(home / "Documents").startswith("OK"))
from room_agent.computer.research import Report, Source  # noqa: E402

rep = Report(question="Best local speech recognition models for Windows?", depth="deep",
             sources=[Source(1, "Whisper - docs", "https://example.org/whisper", "example.org", True, passages=["Whisper is a speech model."]),
                      Source(2, "Benchmark blog", "https://blog.example.com/asr", "blog.example.com", passages=["WER 7.4 percent."])],
             failed=[("https://gone.example.com/x", "the site answered 404")])
r = filewrite.research_report(rep, "Whisper leads on accuracy [1][2].")
saved = next((home / "Desktop").glob("Research - *.md"))
body = saved.read_text(encoding="utf-8")
t.check("research report saved on the Desktop, checked", r.startswith("OK") and saved.exists(), r)
t.check("...sources and quotes come from the research itself (real URLs, real passages)", "https://example.org/whisper" in body
        and "> Whisper is a speech model." in body and "https://blog.example.com/asr" in body)
t.check("...what couldn't be read is listed, and the summary is marked as Jarvis's", "gone.example.com" in body
        and "Written by Jarvis from the sources below" in body)
t.check("no research -> nothing saved", filewrite.research_report(None).startswith("FAILED"))
t.done("SAFETY")
