"""LIVE test of the capability/action architecture on this PC:  .venv\\Scripts\\python -m tests.actions_live
A fake model makes the tool calls (several in one reply, like a real model does for "open X and move it..."; no API
cost); the executor, plans, context, undo and events run for real against Windows. Every outcome is checked by reading
Windows' state directly. Volume and Spotify's window are put back at the end; Chrome is closed by the test itself."""

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
from room_agent.actions import core  # noqa: E402
from room_agent.actions.context import env  # noqa: E402
from room_agent.actions.events import events  # noqa: E402
from room_agent.actions.executor import execute  # noqa: E402
from room_agent.llm import openai_backend  # noqa: E402
from room_agent.tools import apps, media  # noqa: E402
from room_agent.tools import window_control as wc  # noqa: E402
from room_agent.tools.registry import run_tool  # noqa: E402

u = wc.user32
MONS = wc.monitors()
FAILS = []
SEEN = []
events.on("*", lambda e: SEEN.append(e["name"]))


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"   {detail}" if detail and not ok else ""))
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


def running(app):
    return bool(apps.processes(apps.find(app)[0]))


def volume():
    return media._level(media._endpoint())


# ---------------------------------------------------------------- fake model (multiple tool calls per reply)


from tests.harness import Checker, Conversation  # noqa: E402

convo = Conversation(engine=None)
fake, REQUESTS, SPOKEN = convo.model, convo.requests, convo.spoken
from room_agent.conversation.turn import take_turn  # noqa: E402

HISTORY = []


def say(text, calls, reply):
    """One user turn: the model asks for `calls` (all in one reply), then says `reply`. -> (tool results, system prompt)"""
    fake.requests.clear()
    SPOKEN.clear()
    fake.scripts = [{"tools": calls}, {"text": reply}]
    rt.turn_text = text
    seen = {id(m) for m in HISTORY}  # (the history gets trimmed, so positions shift: new messages are found by identity)
    take_turn(HISTORY, text, final=True)
    results = [b["content"] for m in HISTORY if id(m) not in seen and isinstance(m["content"], list) for b in m["content"]
               if isinstance(b, dict) and b.get("type") == "tool_result"]
    system = "\n".join(m["content"] for m in fake.requests[0]["messages"] if m["role"] == "system")
    return results, system


# ---------------------------------------------------------------- remember how things were
vol_was = volume()
sp_h = win("Spotify")
sp_was = None
if sp_h:
    r = wc.wt.RECT()
    with wc._dpi_aware():
        u.GetWindowRect(sp_h, ctypes.byref(r))
    sp_was = (r.left, r.top, r.right - r.left, r.bottom - r.top, wc._state(sp_h))
chrome_was = running("Chrome")
def put_back():
    print("\nputting things back:")
    media._set_level(vol_was, unmute=False)
    if not chrome_was and running("Chrome"):
        run_tool("close_app", {"app_name": "Chrome", "confidence": 0.95})
    h = win("Spotify")
    if h and sp_was:
        x, y, w, hh, state = sp_was
        with wc._dpi_aware():
            u.ShowWindow(h, wc.SW_RESTORE)
            u.SetWindowPos(h, None, x, y, w, hh, wc.SWP_NOZORDER | wc.SWP_NOACTIVATE)
            if state == "maximized":
                u.ShowWindow(h, wc.SW_MAXIMIZE)
    print(f"after: volume {volume()}%, Spotify on monitor {on('Spotify')}, {wc._state(win('Spotify')) if win('Spotify') else '-'}")


_hook = sys.excepthook
sys.excepthook = lambda *a: (put_back(), _hook(*a))  # (a crash halfway still restores volume and windows)

print(f"before: volume {vol_was}%, Spotify {'on monitor ' + str(on('Spotify')) if sp_h else 'closed'}, "
      f"{len(core.capabilities())} capabilities registered\n")

print("1. 'Open Spotify'")
res, _ = say("Open Spotify", [("open_app", {"app_name": "Spotify"})], "Spotify's up.")
check("opened and verified", res[0].startswith("OK") and running("Spotify") and env.last_action.verified, res)
check("context: active_app = Spotify", env.active_app == "Spotify")
check("event app.opened", "app.opened" in SEEN)
if zoomed("Spotify"):
    run_tool("restore_window", {"app": "Spotify"})
if on("Spotify") != 1:
    run_tool("move_window_to_monitor", {"app": "Spotify", "monitor": "1"})

print("\n2. 'Open Spotify and move it to monitor 2.'  (2 tool calls in one reply)")
res, _ = say("Open Spotify and move it to monitor 2.", [("open_app", {"app_name": "Spotify"}),
                                                        ("move_window_to_monitor", {"app": "it", "monitor": "2"})],
             "Done, Spotify's on monitor 2.")
check("both steps OK", len(res) == 2 and all(r.startswith("OK") for r in res), res)
check("Spotify really on monitor 2", on("Spotify") == 2)
check("one concise reply spoken", SPOKEN[-1:] == ["Done, Spotify's on monitor 2."], SPOKEN)
run_tool("move_window_to_monitor", {"app": "Spotify", "monitor": "1"})

print("\n3. 'Open Spotify, move it to monitor 2, maximize it and set volume to 30%.'  (4 calls in one reply)")
SEEN.clear()
res, _ = say("Open Spotify, move it to monitor 2, maximize it and set volume to 30%.",
             [("open_app", {"app_name": "Spotify"}), ("move_window_to_monitor", {"app": "it", "monitor": "2"}),
              ("maximize_window", {"app": "it"}), ("set_volume", {"percent": 30})],
             "All set: Spotify's maximized on monitor 2 and the volume's at 30.")
check("all 4 steps OK, in order", len(res) == 4 and all(r.startswith("OK") for r in res), res)
check("Spotify on monitor 2, maximized; volume 30%", on("Spotify") == 2 and zoomed("Spotify") and volume() == 30,
      (on("Spotify"), zoomed("Spotify"), volume()))
vol_step = next(r for r in [env.last_action] if r.capability == "set_volume")
check("structured result: set_volume before/after/verified", vol_step.state_before["volume"] == vol_was
      and vol_step.state_after["volume"] == 30 and vol_step.verified and vol_step.success, vol_step.as_dict())
check("events: window.moved, window.maximized, volume.changed", {"window.moved", "window.maximized", "volume.changed"} <= set(SEEN), SEEN)
check("one concise reply spoken", len([x for x in SPOKEN if not x.startswith("<")]) == 1, SPOKEN)

print("\n4. 'move it back'")
res, system = say("move it back", [("undo_last_action", {"action": "move", "app": "it"})], "Moved it back.")
check("model saw what can be undone", "can undo" in system and "move_window_to_monitor on Spotify" in system)
check("Spotify back on monitor 1, still maximized", res[0].startswith("OK") and on("Spotify") == 1 and zoomed("Spotify"), res)
check("volume untouched by 'move it back'", volume() == 30)

print("\n5. 'undo that'")
res, _ = say("undo that", [("undo_last_action", {})], "Okay, it's back on monitor 2.")
check("undoing the undo: Spotify on monitor 2 again", res[0].startswith("OK") and on("Spotify") == 2, res)
res, _ = say("and put the volume back", [("undo_last_action", {"action": "volume"})], "Volume's back.")
check(f"volume undo: back to {vol_was}%", res[0].startswith("OK") and volume() == vol_was, res)

print("\n6. failure: 'Open FakeApp and move it to monitor 2.'")
where_before = on("Spotify")
for label, move_args in [("'it'", {"app": "it", "monitor": "2"}), ("by name", {"app": "FakeApp", "monitor": "2"})]:
    res, _ = say("Open FakeApp and move it to monitor 2.", [("open_app", {"app_name": "FakeApp"}),
                                                           ("move_window_to_monitor", move_args)],
                 "I couldn't find FakeApp on this PC.")
    check(f"open fails honestly ({label})", res[0].startswith("FAILED") and "couldn't find" in res[0], res)
    check(f"move NOT executed ({label})", res[1].startswith("FAILED: not done, because open_app") and
          env.last_action.capability != "move_window_to_monitor", res[1])
check("nothing else moved (Spotify stayed put)", on("Spotify") == where_before)

print("\n7. context: 'Open Chrome.' / 'move it to the other monitor.' / 'maximize it.' / 'close it.'")
res, _ = say("Open Chrome.", [("open_app", {"app_name": "Chrome"})], "Chrome's open.")
start = on("Chrome")
check("Chrome open, active_app = Google Chrome", res[0].startswith("OK") and env.active_app == "Google Chrome", res)
res, _ = say("move it to the other monitor.", [("move_window_to_monitor", {"app": "it", "monitor": "the other one"})], "Moved.")
check("'it' = Chrome, moved to another monitor", res[0].startswith("OK: Google Chrome") and on("Chrome") != start, res)
check("previous_app remembered", env.previous_app == "Spotify", env.previous_app)
res, _ = say("maximize it.", [("maximize_window", {"app": "it"})], "Full screen.")
check("'it' = Chrome, maximized", res[0].startswith("OK: Google Chrome") and zoomed("Chrome"), res)
res, _ = say("close it.", [("close_app", {"app_name": "it", "confidence": 0.9})], "Closed Chrome.")
check("'it' = Chrome, closed", res[0].startswith("OK: Google Chrome is closed") and not running("Chrome"), res)
check("Spotify untouched by 'close it'", running("Spotify"))
res, _ = say("undo that", [("undo_last_action", {})], "I can't undo closing it, but I can open it again.")
check("'undo that' after closing: says it can't be undone (no older change undone instead)",
      res[0].startswith("FAILED") and "can't be undone" in res[0] and not running("Chrome"), res)

print("\n8. registry is the source of truth")
res = execute("delete_all_files", {})
check("an invented capability is refused", not res.success and res.error_code == "unavailable", res.message)
check("every offered tool is a registered capability", all(core.get(t["function"]["name"]) for t in fake.requests[0]["tools"]))

put_back()
print("\nALL ARCHITECTURE STEPS PASSED" if not FAILS else f"\nFAILED: {FAILS}")
sys.stdout.flush()
os._exit(1 if FAILS else 0)
