"""LIVE window management on this PC's monitors:  .venv\\Scripts\\python -m tests.windows_live
Part 1 is a conversation ("Open Spotify" / "put it on monitor 2" / "maximize it") where a fake model makes the tool calls
(no API cost) and the real Windows actions run. Part 2 calls each tool directly. Every result is checked by reading
Windows' state independently. Spotify's window is put back where it was; Chrome and Discord are closed again."""

import ctypes
import json
import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from tests.harness import Checker, Conversation, setup_env  # noqa: E402
TMP = setup_env()

from room_agent import runtime as rt  # noqa: E402
from room_agent.llm import openai_backend  # noqa: E402
from room_agent.tools import apps  # noqa: E402
from room_agent.tools import window_control as wc  # noqa: E402
from room_agent.tools.registry import run_tool  # noqa: E402

u = wc.user32
FAILS = []
MONS = wc.monitors()


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def win(app):
    w = apps.app_windows(apps.find(app)[0])
    return w[0][0] if w else None


def on(app):
    h = win(app)
    m = wc._monitor_of(h, MONS) if h else None
    return m["num"] if m else None


def zoomed(app):
    h = win(app)
    return bool(h and u.IsZoomed(h))


def iconic(app):
    h = win(app)
    return bool(h and u.IsIconic(h))


def front(app):
    return apps._front_pid() in {p.pid for p in apps.processes(apps.find(app)[0])}


# ---------------------------------------------------------------- fake model, real tools


from tests.harness import Checker, Conversation  # noqa: E402

convo = Conversation(engine=None)
fake, REQUESTS, SPOKEN = convo.model, convo.requests, convo.spoken
from room_agent.conversation.turn import take_turn  # noqa: E402

HISTORY = []


def say(text, tool, args, reply):
    fake.requests.clear()
    fake.scripts = [{"tool": (tool, args)}, {"text": reply}]
    rt.turn_text = text
    take_turn(HISTORY, text, final=True)
    req = fake.requests[0]
    result = next((b["content"] for m in reversed(HISTORY) if isinstance(m["content"], list) for b in m["content"]
                   if isinstance(b, dict) and b.get("type") == "tool_result"), "")
    system = "\n".join(m["content"] for m in req["messages"] if m["role"] == "system")
    return result, {t["function"]["name"] for t in req["tools"]}, system


print(wc.list_monitors())
sp = apps.find("Spotify")[0]
spotify_was = None
if win("Spotify"):
    h = win("Spotify")
    r = wc.wt.RECT()
    with wc._dpi_aware():
        u.GetWindowRect(h, ctypes.byref(r))
    spotify_was = (r.left, r.top, r.right - r.left, r.bottom - r.top, wc._state(h))
chrome_was, discord_was = bool(apps.processes(apps.find("Chrome")[0])), bool(apps.processes(apps.find("Discord")[0]))

print("\n1. conversation: 'Open Spotify' -> 'put it on monitor 2' -> 'maximize it'  (fake model, real Windows)")
rt.last_active_app = None
res, tools, _ = say("Open Spotify", "open_app", {"app_name": "Spotify"}, "Spotify's up.")
check("'Open Spotify' -> Spotify open and in front", res.startswith("OK") and front("Spotify"), res)
if zoomed("Spotify"):
    run_tool("restore_window", {"app": "Spotify"})  # (start from a normal window so 'maximize it' really does something)
if on("Spotify") != 1:
    run_tool("move_window_to_monitor", {"app": "Spotify", "monitor": "1"})  # (so 'put it on monitor 2' is a real move)
check("(setup: Spotify starts on monitor 1, normal size)", on("Spotify") == 1 and not zoomed("Spotify"))
res, tools, system = say("put it on monitor 2", "move_window_to_monitor", {"app": "it", "monitor": "2"}, "Moved it over.")
check("'put it on monitor 2': window tools offered", "move_window_to_monitor" in tools)
check("...the model was told 'it' = Spotify", "app_in_conversation: Spotify" in system)
check("...Spotify is really on monitor 2", res.startswith("OK: Spotify is on monitor 2") and on("Spotify") == 2, res)
res, tools, system = say("maximize it", "maximize_window", {"app": "it"}, "Full size now.")
check("'maximize it' -> Spotify maximized, still on monitor 2", res.startswith("OK: Spotify is maximized")
      and zoomed("Spotify") and on("Spotify") == 2, res)
check("...the replies were spoken only after verification", "Moved it over." in " ".join(SPOKEN)
      and "Full size now." in " ".join(SPOKEN), SPOKEN)

print("\n2. each tool (as the model would call it), checked against Windows")


def step(label, tool, args, expect_ok, verify):
    t0 = time.time()
    out = run_tool(tool, args)
    ok = out.startswith("OK") == expect_ok and verify()
    check(f"{label:42} {time.time() - t0:4.1f}s", ok, out)


step("Open Chrome", "open_app", {"app_name": "Chrome"}, True, lambda: front("Chrome"))
step("'Put Chrome on my second monitor'", "move_window_to_monitor", {"app": "Chrome", "monitor": "my second monitor"},
     True, lambda: on("Chrome") == 2)
step("'Minimize this' (Chrome is in front)", "minimize_window", {"app": "this"}, True, lambda: iconic("Chrome"))
step("restore_window Chrome (from minimized)", "restore_window", {"app": "Chrome"}, True,
     lambda: not iconic("Chrome") and front("Chrome"))
step("restore_window Chrome (from maximized)", "restore_window", {"app": "Chrome"}, True,
     lambda: not iconic("Chrome") and not zoomed("Chrome"))
step("'Maximize Chrome'", "maximize_window", {"app": "Chrome"}, True, lambda: zoomed("Chrome") and on("Chrome") == 2)
step("move maximized Chrome to the right monitor", "move_window_to_monitor", {"app": "Chrome", "monitor": "right"}, True,
     lambda: on("Chrome") == 3 and zoomed("Chrome"))
step("move it to 'the other one' (next monitor)", "move_window_to_monitor", {"app": "it", "monitor": "the other one"}, True,
     lambda: on("Chrome") == 1)
step("move it to the primary monitor (already there)", "move_window_to_monitor", {"app": "it", "monitor": "primary"},
     True, lambda: on("Chrome") == 1)
step("Open Discord", "open_app", {"app_name": "Discord"}, True, lambda: bool(win("Discord")))
step("focus_window Spotify (on monitor 2)", "focus_window", {"app": "Spotify"}, True, lambda: front("Spotify"))
step("'Bring Discord here' (here = monitor 2)", "move_window_to_monitor", {"app": "Discord", "monitor": "here"}, True,
     lambda: on("Discord") == 2 and front("Discord"))
step("get_active_window", "get_active_window", {}, True, lambda: "Discord" in run_tool("get_active_window", {}))
step("'Switch back to Chrome'", "focus_app", {"app_name": "Chrome"}, True, lambda: front("Chrome"))
step("get_active_window (now Chrome)", "get_active_window", {}, True, lambda: "Google Chrome" in run_tool("get_active_window", {}))
step("'What's currently open?'", "list_running_apps", {}, True, lambda: True)
step("list_monitors", "list_monitors", {}, True, lambda: True)
step("monitor 4 (doesn't exist) -> FAILED", "move_window_to_monitor", {"app": "Chrome", "monitor": "4"}, False,
     lambda: on("Chrome") == 1)
step("close Discord", "close_app", {"app_name": "Discord", "confidence": 0.95}, True, lambda: not win("Discord"))
step("minimize Discord (not open) -> FAILED", "minimize_window", {"app": "Discord"}, False, lambda: True)

print("\nputting things back:")
if not chrome_was:
    print("  ", run_tool("close_app", {"app_name": "Chrome", "confidence": 0.95}))
if discord_was:
    print("  ", run_tool("open_app", {"app_name": "Discord"}))
h = win("Spotify")
if h and spotify_was:
    x, y, w, hgt, state = spotify_was
    with wc._dpi_aware():
        u.ShowWindow(h, wc.SW_RESTORE)
        u.SetWindowPos(h, None, x, y, w, hgt, wc.SWP_NOZORDER | wc.SWP_NOACTIVATE)
        if state == "maximized":
            u.ShowWindow(h, wc.SW_MAXIMIZE)
        elif state == "minimized":
            u.ShowWindow(h, wc.SW_MINIMIZE)
    print(f"   Spotify back on monitor {on('Spotify')}, {wc._state(h)}")
print("\nALL LIVE WINDOW STEPS PASSED" if not FAILS else f"\nFAILED: {FAILS}")
sys.stdout.flush()
os._exit(1 if FAILS else 0)
