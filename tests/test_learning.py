"""Learning & adaptation: Jarvis improves without becoming unpredictable. Offline: volume and the app/window world are
simulated; the real executor, hooks, learner, UserModel and SQLite store run (in a temp file).

Run:  .venv\\Scripts\\python -m tests.test_learning
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
                  SETTINGS_FILE=os.path.join(TMP, "set.json"), LEARNING_DB=os.path.join(TMP, "learning.db"),
                  USER_PROFILE="adam", HA_URL="", HA_TOKEN="", TRACE="0")
sys.path.insert(0, ROOT)

from room_agent import learning  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core  # noqa: E402
from room_agent.actions.context import env  # noqa: E402
from room_agent.actions.executor import Plan, execute  # noqa: E402
from room_agent.learning import model as um  # noqa: E402
from room_agent.tools import apps, media  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"   ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


# ---------------------------------------------------------------- a simulated PC
class Endpoint:
    level, muted = 0.50, 0

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, v, _):
        self.level = v

    def GetMute(self):
        return self.muted

    def SetMute(self, m, _):
        self.muted = m


EP = Endpoint()
media._endpoint = lambda: EP
INSTALLED = ["Spotify", "Google Chrome", "Microsoft Edge", "Visual Studio Code", "Discord"]
apps.apps = lambda refresh=False: {"entries": [{"name": n, "id": n, "procs": []} for n in INSTALLED], "exes": {}}
WORLD = {"running": set(), "monitor": {}}


def _app(args):
    said = args.get("app_name") or args.get("app") or ""
    if said.lower() in ("it", "that"):
        return env.active_app
    entry, _ = apps.find(said)
    return entry["name"] if entry else None


def fake_open(args):
    name = _app(args)
    if not name:
        return f"FAILED: couldn't find an app called '{args['app_name']}' on this PC."
    WORLD["running"].add(name)
    WORLD["monitor"].setdefault(name, "1")
    rt.last_active_app = {"name": name, "action": "opened", "at": time.time()}
    return f"OK: {name} is open; its window is up."


def fake_move(args):
    name = _app(args)
    if name not in WORLD["running"]:
        return f"FAILED: {name} isn't open."
    WORLD["monitor"][name] = str(args["monitor"]).strip()
    rt.last_active_app = {"name": name, "action": "moved", "at": time.time()}
    return f"OK: {name} is on monitor {WORLD['monitor'][name]} now."


core.ensure_loaded()
core.REGISTRY["open_app"].execute = fake_open
core.REGISTRY["open_app"].observe = lambda a, b=None: {"app": _app(a), "running": _app(a) in WORLD["running"]}
core.REGISTRY["move_window_to_monitor"].execute = fake_move
core.REGISTRY["move_window_to_monitor"].expect = lambda a, b: {"monitor.num": str(a.get("monitor")).strip()}  # (the fake's
# monitors, not this PC's: the real expectation reads the real screens)
core.REGISTRY["move_window_to_monitor"].observe = lambda a, b=None: {
    "app": _app(a), "monitor": {"num": WORLD["monitor"].get(_app(a))} if _app(a) in WORLD["running"] else None, "state": "normal"}
L = learning.learner()


def turn(text, steps, gap=0.0):
    """One user turn: the model (simulated) asks for `steps`; the learner sees the turn end."""
    rt.turn_text = text
    rt.turn_no += 1
    plan = Plan()
    for name, args in steps:
        plan.run(name, args)
    L.on_turn_end(text, plan, 0.4)
    return plan.steps


def volume():
    return round(EP.level * 100)


def model(user=None):
    return learning.user_model(user)


def tool(name, **args):
    return execute(name, args).message


# ---------------------------------------------------------------- before learning
print("a new Jarvis works immediately (defaults):")
EP.level = 0.50
turn("a little louder", [("volume_up", {})])
check("'louder' with nothing learned -> the default +10", volume() == 60, volume())
check("nothing learned from a single action", model().resolve("volume_step") is None)

# ---------------------------------------------------------------- explicit preference
print("explicit preferences are followed:")
rt.turn_text = "Always put Spotify on monitor 2."
out = tool("learn_preference", kind="app_monitor", subject="Spotify", value="2")
check("'Always put Spotify on monitor 2' -> saved as a rule", out.startswith("OK: saved: spotify goes on monitor 2"), out)
steps = turn("open spotify", [("open_app", {"app_name": "Spotify"})])
check("'Open Spotify' -> lands on monitor 2 by itself", WORLD["monitor"]["Spotify"] == "2" and
      "Also put it on monitor 2" in steps[0].message, steps[0].message)
out = tool("explain_last_action")
check("'Why did you do that?' -> the rule, in their words", "Always put Spotify on monitor 2" in out, out)
rt.turn_text = "When I say a little louder, increase it by 5%."
tool("learn_preference", kind="volume_step", value="5")
EP.level = 0.50
turn("a little louder", [("volume_up", {})])
check("'a little louder' after being taught -> +5", volume() == 55, volume())

print("a temporary instruction doesn't become permanent:")
rt.turn_text = "Put Spotify on monitor 1 today."
out = tool("learn_preference", kind="app_monitor", subject="Spotify", value="1")
check("saving 'today' as a preference is refused", out.startswith("FAILED") and "one-time" in out, out)
WORLD["running"].clear()
turn("Open Spotify and put it on monitor 1 today", [("open_app", {"app_name": "Spotify"}),
                                                    ("move_window_to_monitor", {"app": "it", "monitor": "1"})])
check("...it's done this time", WORLD["monitor"]["Spotify"] == "1")
p = model().store.preference("adam", "app_monitor:spotify")
check("...the lasting rule is still monitor 2", p["value"] == "2" and p["source"] == "explicit", p)
check("...and the one-time move left no evidence", not [s for s in model().store.signals("adam", "app_monitor:spotify")
                                                         if s["value"] == "1"])

print("the current request beats the saved preference:")
WORLD["running"].clear()
steps = turn("open Spotify on monitor 3", [("open_app", {"app_name": "Spotify"}),
                                           ("move_window_to_monitor", {"app": "Spotify", "monitor": "3"})])
check("no automatic move to 2 when they say where it goes", "Also put it" not in steps[0].message
      and WORLD["monitor"]["Spotify"] == "3", (steps[0].message, WORLD["monitor"]))
EP.level = 0.50
turn("turn it up by 20", [("volume_up", {"amount": 20})])
check("'up by 20' uses 20, not the taught 5", volume() == 70, volume())

# ---------------------------------------------------------------- inferred learning (separate user: no explicit rules)
print("repeated corrections build confidence (user 'sam'):")
learning.set_user("sam")
confidences, steps_taken = [], []
for i in range(8):
    EP.level = 0.40
    turn("make it a little louder", [("volume_up", {})])
    steps_taken.append(volume() - 40)
    if steps_taken[-1] != 5:
        turn("too much, down 5", [("volume_down", {"amount": 5})])  # Jarvis did +10: corrected
    else:
        turn("what's the weather like?", [])  # Jarvis did +5: they just moved on (weak evidence it was right)
    confidences.append(model().store.preference("sam", "volume_step")["confidence"])
print(f"     steps Jarvis took: {steps_taken}\n     confidence after each: {confidences}")
check("one correction is NOT enough to change behavior", steps_taken[1] == 10 and confidences[0] < um.APPLY_CONFIDENCE)
check("each correction / accepted step raises the confidence", all(b > a for a, b in zip(confidences, confidences[1:])))
check("behavior changes only once it's confident (+10 until then, +5 after)",
      steps_taken[:4] == [10] * 4 and steps_taken[4:] == [5] * 4, steps_taken)
p = model().store.preference("sam", "volume_step")
check(f"learned: volume_step = 5, confidence {p['confidence']}, evidence {p['evidence']}", p["value"] == 5 and
      p["confidence"] >= 0.8 and p["evidence"] >= 6, p)
check("'what have you learned?' shows it", "'louder/quieter' means 5%" in tool("list_learned"))

print("contradicting evidence lowers confidence:")
before = model().store.preference("sam", "volume_step")["confidence"]
for _ in range(3):
    EP.level = 0.40
    turn("a little louder", [("volume_up", {})])
    turn("not enough, up 5 more", [("volume_up", {"amount": 5})])
after = model().store.preference("sam", "volume_step")
check(f"confidence {before} -> {after['confidence']}", after["confidence"] < before, after)

print("explicit beats inferred:")
rt.turn_text = "From now on louder means 8 percent."
tool("learn_preference", kind="volume_step", value="8")
EP.level = 0.40
turn("louder", [("volume_up", {})])
check("taught 8 wins over the learned value", volume() == 48 and model().resolve("volume_step")["source"] == "explicit")
for _ in range(3):
    EP.level = 0.40
    turn("louder", [("volume_up", {})])
    turn("too much, down 6", [("volume_down", {"amount": 6})])
check("...and later evidence doesn't override the explicit rule", model().resolve("volume_step")["value"] == 8)

print("app corrections ('open my browser' -> Edge -> 'No, I mean Chrome'):")
for i in range(4):
    turn("open my browser", [("open_app", {"app_name": "Microsoft Edge"})])
    turn("no, I mean Chrome", [("open_app", {"app_name": "Chrome"})])
d = model().resolve("app_alias", "browser")
check("learned: 'browser' means Google Chrome", d and d["value"] == "Google Chrome", model().store.preference("sam", "app_alias:browser"))
steps = turn("open my browser", [("open_app", {"app_name": "browser"})])
check("'open my browser' now opens Chrome", steps[0].subject == "Google Chrome" and steps[0].success, steps[0].message)
rec = model().store.interactions("sam", 30)
corr = next(r for r in rec if r["correction"])
check("the correction is a structured record (original -> corrected)", corr["correction"]["original_request"] == "open my browser"
      and corr["correction"]["original_actions"][0]["params"]["app_name"] == "Microsoft Edge"
      and corr["correction"]["corrected_actions"][0]["params"]["app_name"] == "Chrome", corr["correction"])

print("failed actions don't become preferences:")
for _ in range(4):
    turn("open my editor", [("open_app", {"app_name": "Notepad Plus Ultra"})])
    turn("no, I mean FakeEditor", [("open_app", {"app_name": "FakeEditor"})])
check("no preference from failed opens", model().store.preference("sam", "app_alias:editor") is None)
for _ in range(4):
    turn("Open FakeApp and move it to monitor 2", [("open_app", {"app_name": "FakeApp"}),
                                                    ("move_window_to_monitor", {"app": "it", "monitor": "2"})])
check("no monitor preference from a plan that failed", not [p for p in model().all(True) if "fakeapp" in p["key"]])
fails = [r for r in model().store.interactions("sam", 5) if r["outcome"] == "failed"]
check("...but the failures are recorded as such (PLAN_FAILED)", fails and "PLAN_FAILED" in fails[0]["signals"])

print("preferences don't leak between users:")
learning.set_user("adam")
check("adam: volume 5 (his rule), browser unknown", model().resolve("volume_step")["value"] == 5
      and model().resolve("app_alias", "browser") is None)
learning.set_user("sam")
check("sam: volume 8, browser = Chrome, no Spotify rule", model().resolve("volume_step")["value"] == 8
      and model().resolve("app_monitor", "spotify") is None)
learning.set_user("guest")
check("a new person starts clean", not model().all(True))
learning.set_user("adam")

print("inspect, stop automating, forget:")
out = tool("list_learned")
check("'What have you learned about me?'", "spotify goes on monitor 2" in out and "5%" in out, out)
WORLD["running"].clear()
turn("open spotify", [("open_app", {"app_name": "Spotify"})])
out = tool("set_automation", what="that", enabled=False)
check("'Don't do that automatically anymore'", out.startswith("OK: won't do that automatically"), out)
WORLD["running"].clear()
WORLD["monitor"]["Spotify"] = "1"
steps = turn("open spotify", [("open_app", {"app_name": "Spotify"})])
check("...next time it isn't moved, but the preference is kept", WORLD["monitor"]["Spotify"] == "1"
      and "not applied automatically" in tool("list_learned"), steps[0].message)
out = tool("forget_preference", what="my Spotify monitor preference", confidence=0.95)
check("'Forget my Spotify monitor preference'", out.startswith("OK: forgot spotify goes on monitor 2"), out)
check("...it's gone, evidence too", model().store.preference("adam", "app_monitor:spotify") is None
      and not model().store.signals("adam", "app_monitor:spotify"))
out = tool("forget_preference", what="the volume thing", confidence=0.95)
check("'Reset that preference' (the volume one)", out.startswith("OK: forgot") and model().resolve("volume_step") is None, out)

print("irrelevant preferences stay out of unrelated conversations:")
rt.turn_text = "Always put Discord on monitor 3."
tool("learn_preference", kind="app_monitor", subject="Discord", value="3")
rt.turn_text = "Keep your answers short."
tool("learn_preference", kind="response_style", value="short")
check("'what's the weather tomorrow' sees only the behavior preference",
      learning.context_lines("what's the weather tomorrow") == ["answers: short (you told me)"],
      learning.context_lines("what's the weather tomorrow"))
check("'open discord' sees the Discord rule", any("discord goes on monitor 3" in x for x in learning.context_lines("open discord")))

print("routines ('When I say I'm working, open my development setup'):")
rt.turn_text = "When I say I'm working, open VS Code and Chrome."
out = tool("learn_preference", kind="routine", subject="I'm working",
           value='[{"action": "open_app", "args": {"app_name": "VS Code"}}, {"action": "open_app", "args": {"app_name": "Chrome"}}]')
check("taught as a structured routine", out.startswith("OK: saved: when they say 'i'm working': open_app VS Code, open_app Chrome"), out)
from room_agent.llm import openai_backend  # noqa: E402
from room_agent.tools.registry import active_tools  # noqa: E402

offered = {t["name"] for t in openai_backend.relevant_tools(active_tools(), [{"role": "user", "content": "I'm working"}])}
check("saying the trigger offers run_routine", "run_routine" in offered)
WORLD["running"].clear()
out = tool("run_routine", trigger="I'm working")
check("...and runs both steps", out.startswith("OK") and {"Visual Studio Code", "Google Chrome"} <= WORLD["running"], out)
rt.turn_text = "When I say movie time, set up my movie stuff."
out = tool("learn_preference", kind="routine", subject="movie time", value="set up my movie stuff")
check("a routine without concrete steps asks what it should do", tool("run_routine", trigger="movie time").startswith("NEEDS"))

print("privacy:")
rt.turn_text = "Remember my password is hunter2, always use it."
out = tool("learn_preference", kind="other", subject="login", value="hunter2")
check("a password is never learned", out.startswith("FAILED") and not model().find("hunter2"), out)
rt.turn_text = "always use sk-proj-abcdefghijklmnopqrstuvwxyz123456 for it"
out = tool("learn_preference", kind="other", subject="api", value="sk-proj-abcdefghijklmnopqrstuvwxyz123456")
check("an API key is never learned", out.startswith("FAILED"), out)
turn("set a timer, my card is 4111 1111 1111 1111", [("volume_up", {"amount": 1})])
rec = model().store.interactions("adam", 1)[0]
check("secrets in a stored request are redacted", "4111" not in rec["request"] and "[redacted]" in rec["request"], rec["request"])
check("learning lives in its own file, not memory.db", os.path.basename(learning.store().path) == "learning.db")

print("\nALL LEARNING TESTS PASSED" if not FAILS else f"\nFAILED: {FAILS}")
sys.stdout.flush()
os._exit(1 if FAILS else 0)
