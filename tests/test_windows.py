"""Window management, offline parts: monitor wording on other monitor layouts, which tools each phrase gets, and the claim
check. (The real Windows actions are tested live: tests/windows_live.py)

Run:  .venv\\Scripts\\python -m tests.test_windows
"""

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update(OPENAI_API_KEY="sk-test-not-real", LLM_DEFAULT="openai", MEMORY_DB=os.path.join(TMP, "m.db"),
                  MEMORY_FILE=os.path.join(TMP, "x.json"), RECENT_FILE=os.path.join(TMP, "y.json"),
                  REMINDERS_FILE=os.path.join(TMP, "r.json"), SPEND_FILE=os.path.join(TMP, "s.json"),
                  SETTINGS_FILE=os.path.join(TMP, "set.json"), HA_URL="", HA_TOKEN="", TRACE="0")
sys.path.insert(0, ROOT)

from room_agent import runtime as rt  # noqa: E402
from room_agent.llm import openai_backend  # noqa: E402
from room_agent.tools import window_control as wc  # noqa: E402
from room_agent.tools.registry import active_tools  # noqa: E402
from room_agent.truth import ClaimGuard  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"   ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def layout(*specs):
    """specs: (windows number, left, width, height, primary)"""
    return wc._label([{"num": n, "handle": n, "primary": p, "rect": (x, 0, x + w, h), "work": (x, 0, x + w, h - 40)}
                      for n, x, w, h, p in specs])


print("monitor wording:")
two = layout((1, 0, 1920, 1080, True), (2, 1920, 2560, 1440, False))
for said, want in [("2", 2), ("second", 2), ("my second monitor", 2), ("the right one", 2), ("left", 1), ("primary", 1),
                   ("the other one", 2), ("next", 2)]:
    m, why = wc.pick_monitor(said, two, current=two[0])
    check(f"2 monitors: {said!r} -> {want}", m and m["num"] == want, why)
m, why = wc.pick_monitor("other", two, current=two[1])
check("2 monitors: 'other' from monitor 2 wraps to 1", m and m["num"] == 1, why)
m, why = wc.pick_monitor("3", two)
check("2 monitors: 'monitor 3' -> FAILED, lists what exists", m is None and "there's no monitor 3" in why and "monitor 2" in why)
m, why = wc.pick_monitor("2", layout((1, 0, 1920, 1080, True)))
check("1 monitor: says there's nowhere to move it", m is None and "only has one monitor" in why)
three = layout((1, 0, 1920, 1080, True), (3, 1920, 1080, 1920, False), (2, -1440, 1440, 3440, False))
for said, want in [("middle", 1), ("center", 1), ("left", 2), ("right", 3), ("third", 3), ("main", 1)]:
    m, why = wc.pick_monitor(said, three, current=three[0])
    check(f"3 monitors: {said!r} -> {want}", m and m["num"] == want, why)
m, why = wc.pick_monitor("here", three, current=three[0], here=three[1])
check("'here' -> the monitor of the window in front", m and m["num"] == 2, why)
check("portrait monitors are described as portrait", "portrait" in wc._describe(three[1]))
dup = layout((0, 0, 1920, 1080, False), (0, 1920, 1920, 1080, True))
check("no usable Windows numbers -> numbered primary first, then left to right", [m["num"] for m in dup] == [1, 2]
      and dup[0]["primary"])

print("which tools each phrase gets:")
WIN = {"minimize_window", "maximize_window", "restore_window", "focus_window", "move_window_to_monitor",
       "get_active_window", "list_monitors"}
rt.last_active_app, rt.last_media_at = None, 0
for said in ["Minimize this", "Maximize Spotify", "Put Chrome on my second monitor", "Bring Discord here",
             "What's currently open?", "Switch back to Chrome", "make it full screen"]:
    offered = {t["name"] for t in openai_backend.relevant_tools(active_tools(), [{"role": "user", "content": said}])}
    check(f"{said!r}", WIN <= offered and "focus_app" in offered, WIN - offered)
rt.last_active_app = {"name": "Spotify", "action": "opened", "at": time.time()}
offered = {t["name"] for t in openai_backend.relevant_tools(active_tools(), [{"role": "user", "content": "maximize it"}])}
check("'maximize it' right after an app was used still gets them", WIN <= offered)

print("claims are spoken only after a window tool confirms them:")
g = ClaimGuard(lambda: 0, lambda: False)
for s in ["Minimized.", "Spotify is now on monitor 2.", "It's maximized.", "I moved it to your left screen."]:
    check(f"{s!r} held without a tool result", g.unverified(s) == ["app"], g.unverified(s))
g.tool_result("move_window_to_monitor", "OK: Spotify is on monitor 2 now.")
check("...and allowed once move_window_to_monitor returned OK", not g.unverified("Spotify is now on monitor 2."))
g2 = ClaimGuard(lambda: 0, lambda: False)
g2.tool_result("maximize_window", "FAILED: tried to maximize Spotify, but it isn't maximized.")
check("a FAILED result never verifies 'It's maximized'", g2.unverified("It's maximized.") == ["app"])
check("'I moved to Chicago' isn't a window claim", not ClaimGuard(lambda: 0, lambda: False).unverified("I moved to Chicago."))

print("\nALL WINDOW TESTS PASSED" if not FAILS else f"\nFAILED: {FAILS}")
sys.stdout.flush()
os._exit(1 if FAILS else 0)
