"""Keeps Jarvis running: starts it, restarts it after a crash or a hang, keeps daily logs, and can start it at login.

    python -m room_agent.service              run the supervisor here (it starts Jarvis and watches it)
    python -m room_agent.service --install    start it automatically when you log in (a shortcut in your Startup
                                              folder, runs hidden; --remove takes it out)
    python -m room_agent.service --start      start the supervisor in the background now (no window)
    python -m room_agent.service --stop       stop Jarvis and the supervisor on purpose (no restart)
    python -m room_agent.service --status     is it running, since when, is it healthy

Restarts: any exit that wasn't asked for (--stop) is restarted after a short wait that grows if it keeps failing (5 s,
15 s, 1 min, up to 5 min), so a crash loop doesn't spin. A Jarvis that stops listening (no microphone frame for
HANG_S while it isn't busy answering) or whose dashboard link stops answering is restarted too.
Logs: logs/jarvis-YYYYMMDD.log (one per day); logs and diagnostic files older than KEEP_DAYS are deleted.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
STOP_FILE = ROOT / "jarvis.stop"
SUPERVISOR_PID = ROOT / "jarvis.supervisor"
KEEP_DAYS = 14
HANG_S = 600          # no mic frame for this long (and not mid-answer): it's stuck
UNREACHABLE_S = 180   # the live link didn't answer for this long: it's stuck
GRACE_S = 120         # after a start, time to load models before health checks count
BACKOFF = [5, 15, 60, 300]
CHECK_EVERY_S = 30
SHORTCUT = "Jarvis.lnk"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
COMMAND = None          # (tests run a stand-in instead of main.py)


def _port():
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("CONTROL_PORT="):
                return int(line.split("=", 1)[1].split("#")[0].strip())
    except (OSError, ValueError):
        pass
    return 8771


def _python(windowless=False):
    exe = Path(sys.executable)
    if windowless and exe.name.lower() == "python.exe" and (exe.parent / "pythonw.exe").exists():
        return str(exe.parent / "pythonw.exe")
    return str(exe)


def _log(msg):
    LOGS.mkdir(exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [service] {msg}\n"
    with open(LOGS / f"jarvis-{time.strftime('%Y%m%d')}.log", "a", encoding="utf-8") as f:
        f.write(line)
    try:
        print(line, end="", flush=True)
    except (OSError, ValueError, AttributeError):
        pass  # (pythonw: no console)


def clean_logs(now=None):
    """Delete logs and diagnostic files older than KEEP_DAYS. -> how many were deleted."""
    now = now or time.time()
    gone = 0
    for pattern in ("jarvis-*.log", "diagnostics-*.jsonl"):
        for p in LOGS.glob(pattern):
            try:
                if now - p.stat().st_mtime > KEEP_DAYS * 86400:
                    p.unlink()
                    gone += 1
            except OSError:
                pass
    return gone


def health(port=None, timeout=5):
    """The running Jarvis's health from its live link, or None if it doesn't answer."""
    try:
        from room_agent import localauth  # (the live link needs the local secret, or it answers 403)

        req = urllib.request.Request(f"http://127.0.0.1:{port or _port()}/live", headers={localauth.HEADER: localauth.token()})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")).get("health") or {}
    except Exception:
        return None


def already_running():
    """A Jarvis is answering on its live link before the supervisor started one (someone started it by hand)."""
    return health(timeout=2) is not None


def stuck(h, started, unreachable_since, now=None):
    """-> why this Jarvis should be restarted, or ''. Pure logic (tested)."""
    now = now or time.time()
    if now - started < GRACE_S:
        return ""
    if h is None:
        if unreachable_since and now - unreachable_since > UNREACHABLE_S:
            return f"its live link hasn't answered for {now - unreachable_since:.0f}s"
        return ""
    age = h.get("heartbeat_age_s")
    busy = h.get("state") in ("processing", "speaking")
    if age is not None and age > HANG_S and not busy:
        return f"it stopped listening ({age:.0f}s since the last microphone frame)"
    if age is not None and age > HANG_S * 3:
        return f"it's been busy for {age:.0f}s without listening"
    return ""


def next_wait(failures):
    return BACKOFF[min(failures, len(BACKOFF) - 1)]


def _start_jarvis():
    LOGS.mkdir(exist_ok=True)
    out = open(LOGS / f"jarvis-{time.strftime('%Y%m%d')}.log", "a", encoding="utf-8", buffering=1)
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "JARVIS_SUPERVISED": "1"}
    p = subprocess.Popen(COMMAND or [_python(), str(ROOT / "main.py")], cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, env=env, creationflags=NO_WINDOW if os.name == "nt" else 0)
    return p, out


def _kill(p):
    if p.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
        else:
            p.kill()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def supervise():
    """Run Jarvis and keep it running until --stop."""
    if STOP_FILE.exists():
        STOP_FILE.unlink()
    SUPERVISOR_PID.write_text(str(os.getpid()))
    if already_running():
        _log("Jarvis is already running (started by hand?): the supervisor won't start a second one. Stop it first.")
        return 1
    failures, port = 0, _port()
    _log(f"supervisor started (pid {os.getpid()})")
    try:
        while not STOP_FILE.exists():
            clean_logs()
            p, out = _start_jarvis()
            started, unreachable_since, reason = time.time(), None, ""
            _log(f"Jarvis started (pid {p.pid})")
            while p.poll() is None and not STOP_FILE.exists():
                wake = time.time() + (CHECK_EVERY_S if time.time() - started > GRACE_S else 5)
                while time.time() < wake and p.poll() is None and not STOP_FILE.exists():
                    time.sleep(0.2)  # (an exit is noticed at once, health is checked every CHECK_EVERY_S)
                if p.poll() is not None or STOP_FILE.exists():
                    break
                h = health(port)
                unreachable_since = None if h is not None else (unreachable_since or time.time())
                reason = stuck(h, started, unreachable_since)
                if reason:
                    _log(f"restarting Jarvis: {reason}")
                    _kill(p)
                    break
            if STOP_FILE.exists():
                _kill(p)
                break
            code = p.poll()
            out.close()
            ran = time.time() - started
            failures = 0 if ran > 600 else failures + 1  # (ran for a while: a one-off, not a crash loop)
            wait = next_wait(failures - 1) if failures else BACKOFF[0]
            _log(f"Jarvis exited (code {code}, after {ran:.0f}s{', ' + reason if reason else ''}); restarting in {wait}s")
            deadline = time.time() + wait
            while time.time() < deadline and not STOP_FILE.exists():
                time.sleep(min(1.0, wait / 5))
    finally:
        _log("supervisor stopped")
        for f in (SUPERVISOR_PID, STOP_FILE):
            try:
                f.unlink()
            except OSError:
                pass
    return 0


# ---------------------------------------------------------------- start at login (Startup folder shortcut)
def _startup_dir():
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def install():
    """A shortcut in your Startup folder that runs the supervisor hidden when you log in. Nothing system-wide."""
    import comtypes.client

    link = _startup_dir() / SHORTCUT
    shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
    sc = shell.CreateShortcut(str(link))
    sc.TargetPath = _python(windowless=True)
    sc.Arguments = "-m room_agent.service"
    sc.WorkingDirectory = str(ROOT)
    sc.Description = "Jarvis room agent (supervisor)"
    sc.WindowStyle = 7
    sc.Save()
    return link


def remove():
    link = _startup_dir() / SHORTCUT
    if link.exists():
        link.unlink()
        return True
    return False


def start_background():
    subprocess.Popen([_python(windowless=True), "-m", "room_agent.service"], cwd=str(ROOT),
                     creationflags=NO_WINDOW | 0x00000008 if os.name == "nt" else 0,  # (+ DETACHED_PROCESS)
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop(wait=40):
    """Stop on purpose: the supervisor sees the stop file, closes Jarvis and doesn't restart it."""
    if not SUPERVISOR_PID.exists():
        return False
    STOP_FILE.write_text("stop")
    deadline = time.time() + wait
    while time.time() < deadline and SUPERVISOR_PID.exists():
        time.sleep(1)
    return not SUPERVISOR_PID.exists()


def status():
    h = health(timeout=3)
    sup = SUPERVISOR_PID.read_text().strip() if SUPERVISOR_PID.exists() else None
    auto = (_startup_dir() / SHORTCUT).exists()
    if h is None:
        return f"Jarvis: not running. Supervisor: {'pid ' + sup if sup else 'not running'}. Starts at login: {'yes' if auto else 'no'}."
    supervisor = f"pid {sup}" if sup else "not running (started by hand: no automatic restart)"
    if not h:
        return (f"Jarvis: running (an older version without health info: restart it to get it). Supervisor: {supervisor}. "
                f"Starts at login: {'yes' if auto else 'no'}.")
    up = int(h.get("uptime_s") or 0)
    beat = h.get("heartbeat_age_s")
    return (f"Jarvis: running (pid {h.get('pid')}, up {up // 3600}h{(up % 3600) // 60:02d}m, {h.get('state')}, "
            f"{'last mic frame ' + str(beat) + 's ago' if beat is not None else 'not listening yet'}). "
            f"Supervisor: {supervisor}. Starts at login: {'yes' if auto else 'no'}.")


def main(argv=None):
    p = argparse.ArgumentParser(description="Keep Jarvis running")
    g = p.add_mutually_exclusive_group()
    for flag in ("install", "remove", "start", "stop", "status"):
        g.add_argument(f"--{flag}", action="store_true")
    a = p.parse_args(argv)
    if a.install:
        print(f"Installed: Jarvis starts when you log in ({install()}). Remove with --remove.")
    elif a.remove:
        print("Removed: Jarvis won't start at login." if remove() else "It wasn't set to start at login.")
    elif a.start:
        start_background()
        print("Starting Jarvis in the background (supervised). Check with --status in a minute.")
    elif a.stop:
        print("Stopped." if stop() else "No supervisor was running (if Jarvis runs in a window, close it there).")
    elif a.status:
        print(status())
    else:
        return supervise()
    return 0


if __name__ == "__main__":
    sys.exit(main())
