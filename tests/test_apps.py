"""App control, offline: Windows is simulated (apps start, show windows, hide to the tray, ask "save changes?"), and OpenAI
is a fake. Checks that nothing is reported done unless the window really opened / closed / came to the front.

Run:  .venv\\Scripts\\python -m tests.test_apps        (the live run on this PC is tests/apps_live.py)
"""

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
from room_agent.tools.registry import run_tool  # noqa: E402

# ---------------------------------------------------------------- a pretend Windows
ENTRIES = [{"name": "Spotify", "id": "SpotifyAB.SpotifyMusic_x!Spotify", "exe": "", "link": "", "procs": []},
           {"name": "Google Chrome", "id": "Chrome", "exe": "", "link": "", "procs": ["chrome.exe"]},
           {"name": "Discord", "id": "com.squirrel.Discord.Discord", "exe": "", "link": "", "procs": ["discord.exe"]},
           {"name": "Visual Studio Code", "id": "Microsoft.VisualStudioCode", "exe": "", "link": "", "procs": ["code.exe"]},
           {"name": "Notepad", "id": "Microsoft.WindowsNotepad_x!App", "exe": "", "link": "", "procs": []},
           {"name": "Broken App", "id": "Broken.App", "exe": "", "link": "", "procs": ["broken.exe"]}]
FAKE_CACHE = {"built": time.time(), "entries": ENTRIES, "exes": {}}
apps.apps = lambda refresh=False: FAKE_CACHE
apps.OPEN_WAIT_S, apps.CLOSE_WAIT_S = 1.5, 0.5

PC = {}  # name -> {"pid", "window" (bool), "tray_on_close", "asks_save"}
FRONT = {"name": None}
BEHAVIOR = {"Discord": {"tray_on_close": True}, "Notepad": {"asks_save": True}}
_pid = [1000]


class Proc:
    def __init__(self, name):
        self.name, self.pid = name, PC[name]["pid"]

    def is_running(self):
        return self.name in PC and PC[self.name]["pid"] == self.pid

    def terminate(self):
        PC.pop(self.name, None)


def start(name):
    _pid[0] += 1
    PC[name] = {"pid": _pid[0], "window": True, **BEHAVIOR.get(name, {})}


def fake_launch(entry):
    if entry["name"] == "Broken App":
        return  # starts nothing
    if entry["name"] in PC:
        PC[entry["name"]]["window"] = True  # a tray app shows its window again
        return
    threading.Timer(0.3, start, args=(entry["name"],)).start()


def fake_processes(entry):
    return [Proc(entry["name"])] if entry["name"] in PC else []


def fake_windows(entry, pids=None):
    s = PC.get(entry["name"])
    return [(hash(entry["name"]), s["pid"], entry["name"])] if s and s["window"] else []


def fake_front(hwnd):
    FRONT["name"] = next((n for n in PC if hash(n) == hwnd), None)
    return True


def post_message(hwnd, msg, *_):
    name = next((n for n in PC if hash(n) == hwnd), None)
    if not name:
        return
    if PC[name].get("asks_save"):
        return  # "save changes?": the window stays
    if PC[name].get("tray_on_close"):
        PC[name]["window"] = False  # hides to the tray, keeps running
    else:
        PC.pop(name)


def wait_procs(procs, timeout=None):
    time.sleep(min(timeout or 0, 0.05))
    alive = [p for p in procs if p.is_running()]
    return [p for p in procs if p not in alive], alive


apps.IS_WINDOWS = True
apps._launch, apps.processes, apps.app_windows, apps.bring_to_front = fake_launch, fake_processes, fake_windows, fake_front
apps.user32 = NS(PostMessageW=post_message,  # a window with a "save changes?" dialog open is disabled
                 IsWindowEnabled=lambda h: not any(hash(n) == h and s.get("asks_save") for n, s in PC.items()))
apps._psutil = lambda: NS(wait_procs=wait_procs, Error=Exception)
PROTECTED = set()
apps._protected_pids = lambda: PROTECTED
apps._running_by_name = lambda said: None
apps.list_running_apps = lambda: "OK: open apps: " + ", ".join(PC) + "."

# ---------------------------------------------------------------- fake OpenAI (for "close it")



from tests.harness import Checker, Conversation  # noqa: E402

convo = Conversation(engine=None)
fake, REQUESTS, SPOKEN = convo.model, convo.requests, convo.spoken
from room_agent.conversation.turn import take_turn  # noqa: E402

t = Checker()
check, FAILS = t.check, t.fails


def tool(name, **args):
    return run_tool(name, args)


def turn(history, said, scripts):
    REQUESTS.clear()
    SPOKEN.clear()
    fake.scripts = list(scripts)
    rt.turn_text = said
    take_turn(history, said, final=True)
    return [b["content"] for m in history[-4:] if isinstance(m["content"], list) for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result"]


# ---------------------------------------------------------------- the requested tests
print("tool level (what the model's call actually does):")
r = tool("open_app", app_name="Spotify")
check("Open Spotify -> OK only once its window is up", r.startswith("OK") and "window is up" in r and "Spotify" in PC, r)
r = tool("open_app", app_name="spotify")
check("App already running -> brought to front, not a 2nd copy", r.startswith("OK: Spotify was already open")
      and FRONT["name"] == "Spotify", r)
r = tool("close_app", app_name="Spotify", confidence=0.95)
check("Close Spotify -> OK only once it's gone", r == "OK: Spotify is closed." and "Spotify" not in PC, r)
r = tool("open_app", app_name="Chrome")
check("Open Chrome ('Chrome' -> Google Chrome)", r.startswith("OK: Google Chrome is open"), r)
tool("open_app", app_name="Spotify")
r = tool("focus_app", app_name="Chrome")
check("Focus Chrome -> in front", r == "OK: Google Chrome is in front now." and FRONT["name"] == "Google Chrome", r)
calls = {"n": 0}
real_front = apps.bring_to_front


def flaky_front(hwnd):
    calls["n"] += 1
    return real_front(hwnd) if calls["n"] > 1 else False  # 1st try: window found, but Windows won't foreground it


apps.bring_to_front = flaky_front
r = tool("focus_app", app_name="Spotify")
apps.bring_to_front = real_front
check("a window that's found but won't come to front is retried (asked to show itself again), not given up on at "
      "once - the exact 2026-10-09 live failure (Spotify running, found, but never focused)",
      r == "OK: Spotify is in front now." and calls["n"] == 2, (r, calls["n"]))
r = tool("open_app", app_name="Discord")
check("Open Discord", r.startswith("OK: Discord is open"), r)
r = tool("close_app", app_name="it", confidence=0.9)
check("'close it' -> closes Discord (the last app)", r.startswith("OK: Discord is closed") and "Discord" not in PC, r)
check("...Discord hid in the tray, so it was ended and that's said", "kept running in the background" in r, r)
r = tool("open_app", app_name="Fakeapp Ultra 9000")
check("Unknown app -> 'I couldn't find ... on this PC', nothing done",
      r.startswith("FAILED") and "I couldn't find Fakeapp Ultra 9000 on this PC" in r, r)

print("never claims what didn't happen:")
r = tool("open_app", app_name="Broken App")
check("an app that never starts -> FAILED, not OK", r.startswith("FAILED: tried to open Broken App"), r)
tool("open_app", app_name="Notepad")
r = tool("close_app", app_name="Notepad", confidence=0.95)
check("'save changes?' dialog -> FAILED, nothing forced closed", r.startswith("FAILED") and "Notepad" in PC, r)
PC.pop("Notepad")
r = tool("focus_app", app_name="Discord")
check("switch to an app that isn't open -> FAILED + offer to open", r.startswith("FAILED") and "Offer to open" in r, r)
r = tool("close_app", app_name="Discord", confidence=0.95)
check("close an app that isn't open -> says so", r == "OK: Discord wasn't open, so there was nothing to close.", r)
tool("open_app", app_name="VS Code")
PROTECTED.add(PC["Visual Studio Code"]["pid"])
r = tool("close_app", app_name="VS Code", confidence=0.95)
check("won't close the app Jarvis runs inside", r.startswith("FAILED: I'm running inside") and "Visual Studio Code" in PC, r)
rt.last_active_app = None
r = tool("close_app", app_name="it", confidence=0.9)
check("'close it' with no app in the conversation -> asks which", r.startswith("NEEDS"), r)
r = tool("close_app", app_name="Chrome", confidence=0.4)
check("unclear 'close' (low confidence) -> asks first", r.startswith("NEEDS_CONFIRMATION") and "Google Chrome" in PC, r)

print("through the conversation (fake model):")
from room_agent import config  # noqa: E402

config.REFLEX = False  # (this part tests the model's path: tool offering and the claim check. Reflexes: test_cognition)
PC.clear()
history = []
res = turn(history, "Open Discord", [{"tool": ("open_app", {"app_name": "Discord"})}, {"text": "Discord's open."}])
check("'Open Discord' -> open_app offered and run", "open_app" in {t["function"]["name"] for t in REQUESTS[0]["tools"]}
      and any(x.startswith("OK: Discord is open") for x in res), res)
check("...and the confirmation is spoken after the OK", "Discord's open." in " ".join(SPOKEN), SPOKEN)
res = turn(history, "close it", [{"tool": ("close_app", {"app_name": "it", "confidence": 0.9})}, {"text": "Closed Discord."}])
sent = "\n".join(m["content"] for m in REQUESTS[0]["messages"] if m["role"] == "system")
check("'close it' -> app tools still offered", "close_app" in {t["function"]["name"] for t in REQUESTS[0]["tools"]})
check("...the model is told which app 'it' is", "app_in_conversation: Discord" in sent)
check("...Discord is closed", any(x.startswith("OK: Discord is closed") for x in res) and "Discord" not in PC, res)
turn(history, "open chrome", [{"tool": ("open_app", {"app_name": "Chrome"})}, {"text": "Chrome's up."}])
turn(history, "open spotify", [{"tool": ("open_app", {"app_name": "Spotify"})}, {"text": "Spotify's open."}])
res = turn(history, "switch back to chrome", [{"tool": ("focus_app", {"app_name": "Chrome"})}, {"text": "Back on Chrome."}])
check("'switch back to Chrome' -> focuses the existing window", FRONT["name"] == "Google Chrome"
      and any(x.startswith("OK: Google Chrome is in front") for x in res), res)
PC.clear()
SPOKEN.clear()
res = turn([], "open broken app", [{"tool": ("open_app", {"app_name": "Broken App"})}, {"text": "Opened Broken App for you."},
                                   {"text": "Hmm, Broken App didn't start."}])
check("a false 'Opened ... for you' after FAILED is never spoken", not any("Opened Broken App" in s for s in SPOKEN), SPOKEN)
res = turn([], "open spotify", [{"text": "Opened Spotify for you."}, {"tool": ("open_app", {"app_name": "Spotify"})},
                                {"text": "Spotify's open."}])
check("'Opened Spotify' said before any tool ran is held, then the tool really runs", "Spotify" in PC
      and not any("Opened Spotify for you" in s for s in SPOKEN), SPOKEN)

print("every phrasing gets the app tools (the model maps the words to the tool):")
rt.last_active_app = None
for said in ["Open Spotify", "Launch Discord", "Start Chrome", "Close Spotify", "Quit Discord", "Switch to Chrome",
             "Bring Spotify back", "What apps are running?"]:
    offered = {t["name"] for t in openai_backend.relevant_tools(
        __import__("room_agent.tools.registry", fromlist=["x"]).active_tools(), [{"role": "user", "content": said}])}
    check(f"{said!r}", {"open_app", "close_app", "focus_app", "list_running_apps"} <= offered)
rt.last_active_app = {"name": "Spotify", "action": "opened", "at": time.time()}
r = tool("close_app", app_name="it", confidence=0.9)
check("last_active_app: 'close it' after 'Open Spotify' means Spotify", "Spotify" in r, r)

from types import SimpleNamespace  # noqa: E402

from room_agent.abilities import computer as computer_tools  # noqa: E402


def opened(title):
    return computer_tools._spoken(SimpleNamespace(capability="open_url", parameters={}, message=(
        f'OK: opened sf.eater.com in a new Opera tab (it\'s in front); it\'s showing "{title}".')))


check("a page still loading is named by its site ('Opened sf.eater.com'), never 'Opened Loading...'; a real title is used",
      opened("Loading\u2026") == "Opened sf.eater.com in Opera." and opened("") == "Opened sf.eater.com in Opera."
      and opened("The 38 Essential Restaurants - Eater SF") == "Opened The 38 Essential Restaurants in Opera.",
      [opened("Loading\u2026"), opened("The 38 Essential Restaurants - Eater SF")])

print("\nALL APP TESTS PASSED" if not FAILS else f"\nFAILED: {FAILS}")
sys.stdout.flush()
os._exit(1 if FAILS else 0)
