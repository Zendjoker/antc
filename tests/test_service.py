"""Always on, offline: the supervisor restarts a crashed or hung Jarvis with growing waits, stops on purpose, cleans old
logs; only one Jarvis can run; Whisper preloads in the background exactly once. Uses a stand-in instead of main.py: no
audio, no real Jarvis started, nothing installed at login.

Run:  .venv\\Scripts\\python -m tests.test_service
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from tests.harness import setup_env

setup_env()

from room_agent import service  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
tmp = Path(tempfile.mkdtemp())
service.LOGS, service.STOP_FILE, service.SUPERVISOR_PID = tmp / "logs", tmp / "jarvis.stop", tmp / "jarvis.supervisor"

print("When to restart:")
now = 10_000.0
t.check("just started -> never judged during the warm-up", service.stuck(None, now - 30, now - 30, now) == "")
t.check("healthy (mic frame 0.1 s ago) -> keep it", service.stuck({"heartbeat_age_s": 0.1, "state": "wake_word_only"}, 0, None, now) == "")
t.check("no mic frame for 11 min while idle -> restart (hung)", "stopped listening" in service.stuck(
    {"heartbeat_age_s": 660, "state": "wake_word_only"}, 0, None, now))
t.check("11 min busy answering (e.g. long research) -> not killed", service.stuck(
    {"heartbeat_age_s": 660, "state": "processing"}, 0, None, now) == "")
t.check("...but busy for 31 min without listening -> restart", "busy" in service.stuck(
    {"heartbeat_age_s": 1860, "state": "processing"}, 0, None, now))
t.check("live link silent for 4 min -> restart", "hasn't answered" in service.stuck(None, 0, now - 240, now))
t.check("live link silent for 1 min -> wait", service.stuck(None, 0, now - 60, now) == "")
t.check("waits grow when it keeps crashing: 5 s, 15 s, 1 min, then 5 min max",
        [service.next_wait(i) for i in range(6)] == [5, 15, 60, 300, 300, 300])

print("Logs:")
service.LOGS.mkdir()
old, new = service.LOGS / "jarvis-20260901.log", service.LOGS / "jarvis-20261008.log"
diag = service.LOGS / "diagnostics-20260901-101010.jsonl"
for f in (old, new, diag):
    f.write_text("x")
past = time.time() - 20 * 86400
os.utime(old, (past, past))
os.utime(diag, (past, past))
t.check("logs and diagnostics older than 14 days deleted, recent kept", service.clean_logs() == 2 and new.exists()
        and not old.exists() and not diag.exists())

print("Supervising (a stand-in that crashes at once):")
service.BACKOFF, service.GRACE_S, service.CHECK_EVERY_S = [0.2, 0.2], 999, 0.1
service.health = lambda port=None, timeout=5: None  # (no real Jarvis answering; the stand-in has no live link)
service.already_running = lambda: False
service.COMMAND = [sys.executable, "-c", "import sys; print('stand-in ran'); sys.exit(3)"]
th = threading.Thread(target=service.supervise, daemon=True)
health_calls = []
th.start()
time.sleep(2.0)
log = "".join(p.read_text(encoding="utf-8") for p in service.LOGS.glob("jarvis-*.log"))
starts = log.count("Jarvis started")
t.check("a crashing Jarvis is restarted, again and again", starts >= 3, starts)
t.check("...each exit is logged with its code", "exited (code 3" in log and "stand-in ran" in log)
service.STOP_FILE.write_text("stop")
th.join(timeout=5)
t.check("--stop: the supervisor stops on purpose, no restart, its files cleaned up", not th.is_alive()
        and not service.SUPERVISOR_PID.exists() and not service.STOP_FILE.exists())

print("Hung Jarvis:")
service.COMMAND = [sys.executable, "-c", "import time; time.sleep(60)"]
service.GRACE_S = 0
service.health = lambda port=None, timeout=5: {"heartbeat_age_s": 9999, "state": "wake_word_only"}
th = threading.Thread(target=service.supervise, daemon=True)
th.start()
time.sleep(1.5)
service.STOP_FILE.write_text("stop")
th.join(timeout=15)
log = "".join(p.read_text(encoding="utf-8") for p in service.LOGS.glob("jarvis-*.log"))
t.check("a Jarvis that stopped listening is killed and restarted", "restarting Jarvis: it stopped listening" in log)

print("One Jarvis at a time:")
code = ("import sys; sys.path.insert(0, r'{root}'); from room_agent import cli, config; config.HERE = __import__('pathlib')"
        ".Path(r'{tmp}'); ok = cli._single_instance(); print(ok, flush=True); import time; time.sleep({s})")
first = subprocess.Popen([sys.executable, "-c", code.format(root=service.ROOT, tmp=tmp, s=4)], stdout=subprocess.PIPE,
                         text=True, env={**os.environ})
t.check("the first Jarvis gets the lock", first.stdout.readline().strip() == "True")
second = subprocess.run([sys.executable, "-c", code.format(root=service.ROOT, tmp=tmp, s=0)], capture_output=True, text=True)
t.check("a second one is refused while the first runs", second.stdout.strip() == "False", second.stdout + second.stderr[-300:])
first.kill()
first.wait()
third = subprocess.run([sys.executable, "-c", code.format(root=service.ROOT, tmp=tmp, s=0)], capture_output=True, text=True)
t.check("after the first one died (even killed), a new one can start: no stale lock", third.stdout.strip() == "True")

print("Background preload:")
from room_agent.audio import stt  # noqa: E402

loads = []


class FakeWhisper:
    def __init__(self, *a, **k):
        loads.append(1)
        time.sleep(0.3)


import types  # noqa: E402

sys.modules["faster_whisper"] = types.SimpleNamespace(WhisperModel=FakeWhisper)
stt._whisper = None
stt.preload()
time.sleep(0.05)
t0 = time.time()
model = stt.load_whisper()  # (the voice loop asks while the preload is still loading)
t.check("preload + the voice loop asking at the same time -> loaded once, and it waits for it", len(loads) == 1
        and isinstance(model, FakeWhisper), len(loads))
t.done("SERVICE")
