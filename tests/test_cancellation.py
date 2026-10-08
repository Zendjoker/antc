"""Emergency stop reaches long-running actions, offline. Each action is made to hang (a page that never loads, a click
that never navigates, a slow site, music that never starts, a slow folder walk, a file window that never appears), the
stop is pressed 0.3 s in, and the action must end within STOP_WITHIN_S saying it stopped. Also: no further model call
after a stop mid-tool; a stop said by voice is still answered; a request made after the stop isn't cancelled.

Run:  .venv\\Scripts\\python -m tests.test_cancellation
"""

import os
import threading
import time
import types

from tests.harness import setup_env

setup_env()

from room_agent import emergency  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

t = Checker()
core.ensure_loaded()
STOP_WITHIN_S = 1.5


def stopped_in_time(name, action):
    """Run action() in a thread, press stop at 0.3 s. -> (result text, seconds from stop to done)."""
    rt.new_turn(name)
    out, done = {}, threading.Event()

    def run():
        try:
            out["r"] = action()
        except Exception as e:  # noqa: BLE001
            out["r"] = f"EXCEPTION {e!r}"
        done.set()

    threading.Thread(target=run, daemon=True).start()
    time.sleep(0.3)
    t0 = time.time()
    emergency.stop_everything("hotkey")
    done.wait(10)
    return str(out.get("r")), time.time() - t0


def check(name, action, ok=lambda r: "stop" in r.lower()):
    r, dt = stopped_in_time(name, action)
    t.check(f"{name}: ended {dt:.2f}s after the stop, says so", dt <= STOP_WITHIN_S and not r.startswith("OK") and ok(r),
            (round(dt, 2), r[:140]))


print("Long actions stop:")
from room_agent.computer import browser_ops, browsers  # noqa: E402

browsers.installed = lambda refresh=False: {"opera": "C:\\fake\\opera.exe"}
browsers.default_key = lambda: "opera"
browsers.foreground_browser = lambda: ("opera", 1)
browsers.browser_windows = lambda key=None: [("opera", 1, "Old page - Opera")]
browsers.read_state = lambda hwnd: {"url": "https://old.example.com", "title": "Old page - Opera"}
browsers._launch = lambda exe, args: True  # (the page never shows up)
check("opening a page that never loads", lambda: browsers.open_url("https://www.youtube.com", "", "open youtube"))
check("waiting for a click's navigation", lambda: browser_ops.wait_change(1, {"url": "https://old.example.com", "title": "x"},
                                                                          timeout=8, check=lambda s: False),
      ok=lambda r: r == "None")  # (None = no change seen: the caller then reports "not confirmed")

from room_agent.computer import pages, research  # noqa: E402


def slow_fetch(url, cancel=None, **kw):
    while not (cancel and cancel()):
        time.sleep(0.05)
    return pages.Page(url=url, error="stopped")


check("research reading a slow site", lambda: research.for_model(research.research(
    "speech recognition", "quick", search=lambda q: [{"href": "https://slow.example.com/a", "title": "a"}], fetch=slow_fetch,
    cancel=__import__("room_agent.cancel", fromlist=["requested"]).requested)))

from room_agent.tools import music  # noqa: E402

music.WAIT_S = 8.0
music._spotify_window = lambda: 9
music._buttons = lambda hwnd: [("GYM", types.SimpleNamespace(name="Play GYM", raw=None))]
music._now = lambda: ("Old", "Someone", False)
music.uia.invoke = lambda el: "Invoke"  # (pressed, nothing starts)
check("waiting for music that never starts", lambda: music.spotify("GYM", "playlist"))

from room_agent.computer import files  # noqa: E402


def slow_walk(root):
    for i in range(400):
        time.sleep(0.02)
        yield (str(root), [], [f"file{i}.txt"])


files.os.walk = slow_walk
files.WALK_BUDGET_S = 8.0
files.roots = lambda: [files.HOME]
check("a slow folder search", lambda: str(files._walk_search("report")), ok=lambda r: r == "[]")
files.os.walk = os.walk

from room_agent.abilities import files as file_tools  # noqa: E402

file_tools._pick = lambda args: (str(files.HOME / "Documents" / "budget.xlsx"), "test")
files.open_with_default = lambda p: None
browsers._top_windows = lambda: []
check("waiting for a file's window", lambda: file_tools._open({}))

print("The model loop and the voice:")
convo = Conversation()
made = []


def stop_during_tool(args):
    emergency.stop_everything("hotkey")  # (the hotkey pressed while this tool runs)
    return "OK: done"


real_get_time = core.get("get_time").execute
core.get("get_time").execute = stop_during_tool
convo.say("what time is it and then tell me a story", scripts=[{"tools": [("get_time", {})]}, {"text": "Once upon a time..."}])
core.get("get_time").execute = real_get_time
t.check("stopped while a tool ran -> no second model call, nothing more said", len(convo.requests) == 1
        and "Once upon" not in " ".join(convo.said()), (len(convo.requests), convo.said()))


class Eng:
    def __init__(self):
        self.interrupted = threading.Event()

    def flush(self):
        pass


eng = Eng()
rt.engine = eng
rt.new_turn("stop everything")
emergency.stop_everything("voice")
t.check("a stop by voice doesn't cancel its own turn or set the interruption flag ('Stopped.' must be heard)",
        not rt.turn.cancel.is_set() and not eng.interrupted.is_set()
        and not __import__("room_agent.cancel", fromlist=["requested"]).requested())
rt.engine = None  # (the conversation helper runs without audio)
# (one conversation for the whole file: each one listens to the same speech queue; said() is per request)
convo.say("stop everything", reply="SHOULD NOT BE NEEDED")
t.check("...and 'Stopped.' is said", convo.said() == ["Stopped."], convo.said())
rt.engine = None
time.sleep(0.01)
convo.say("tell me a joke", scripts=[{"text": "Why did the chicken cross the road?"}])
t.check("a request made after the stop works normally (not cancelled by the old stop)",
        convo.said() == ["Why did the chicken cross the road?"], convo.said())
t.done("CANCELLATION")
