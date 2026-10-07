"""LIVE app-control run on this PC (really opens and closes apps; no model, no API cost):
    .venv\\Scripts\\python -m tests.apps_live
Each step goes through run_tool, exactly as when the model asks for it, and is then checked independently."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TRACE", "0")

from room_agent import runtime as rt  # noqa: E402
from room_agent.tools import apps  # noqa: E402
from room_agent.tools.registry import run_tool  # noqa: E402

FAILS = []


def front_is(name):
    entry, _ = apps.find(name)
    return apps._front_pid() in {p.pid for p in apps.processes(entry)}


def running(name):
    return bool(apps.processes(apps.find(name)[0]))


def step(label, tool, args, expect_ok, verify):
    t0 = time.time()
    out = run_tool(tool, args)
    took = time.time() - t0
    ok = out.startswith("OK") == expect_ok and verify()
    print(f"  {'ok  ' if ok else 'FAIL'} {label:34} {took:4.1f}s  {out}")
    if not ok:
        FAILS.append(label)


print("app list:", len(apps.apps()["entries"]), "apps")
was_spotify = running("Spotify")
print("live run:")
step("Open Spotify (already running)" if was_spotify else "Open Spotify", "open_app", {"app_name": "Spotify"}, True,
     lambda: running("Spotify"))
step("Close Spotify", "close_app", {"app_name": "Spotify", "confidence": 0.95}, True, lambda: not running("Spotify"))
step("Open Spotify (fresh launch)", "open_app", {"app_name": "Spotify"}, True,
     lambda: running("Spotify") and bool(apps.app_windows(apps.find("Spotify")[0])))
step("Open Chrome", "open_app", {"app_name": "Chrome"}, True, lambda: running("Chrome"))
step("Focus Spotify", "focus_app", {"app_name": "Spotify"}, True, lambda: front_is("Spotify"))
step("Focus Chrome", "focus_app", {"app_name": "Chrome"}, True, lambda: front_is("Chrome"))
step("Open Discord", "open_app", {"app_name": "Discord"}, True, lambda: running("Discord"))
step("'close it' (-> Discord)", "close_app", {"app_name": "it", "confidence": 0.9}, True, lambda: not running("Discord"))
step("Unknown app", "open_app", {"app_name": "Zorblax Studio"}, False, lambda: True)
step("App already running (Chrome)", "open_app", {"app_name": "Chrome"}, True, lambda: front_is("Chrome"))
step("List running apps", "list_running_apps", {}, True, lambda: True)
step("Close VS Code (Jarvis's host?)", "close_app", {"app_name": "VS Code", "confidence": 0.95}, False,
     lambda: running("VS Code")) if apps._protected_pids() & {p.pid for p in apps.processes(apps.find("VS Code")[0])} else None
print("cleanup:")
step("Close Chrome (wasn't open before)", "close_app", {"app_name": "Chrome", "confidence": 0.95}, True,
     lambda: not running("Chrome"))
print("\nALL LIVE APP STEPS PASSED" if not FAILS else f"\nFAILED: {FAILS}")
