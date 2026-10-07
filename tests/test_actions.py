"""The capability/action architecture, offline: a made-up future module plugs in by registering capabilities only, and
gets everything else for free (offered to the model, listed as a capability, risk rules, plans, undo, events, claims).

Run:  .venv\\Scripts\\python -m tests.test_actions
"""

import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update(OPENAI_API_KEY="sk-test-not-real", LLM_DEFAULT="openai", MEMORY_DB=os.path.join(TMP, "m.db"),
                  MEMORY_FILE=os.path.join(TMP, "x.json"), RECENT_FILE=os.path.join(TMP, "y.json"),
                  REMINDERS_FILE=os.path.join(TMP, "r.json"), SPEND_FILE=os.path.join(TMP, "s.json"),
                  SETTINGS_FILE=os.path.join(TMP, "set.json"), HA_URL="", HA_TOKEN="", TRACE="0")
sys.path.insert(0, ROOT)

from room_agent import runtime as rt  # noqa: E402
from room_agent import truth  # noqa: E402
from room_agent.actions import core  # noqa: E402
from room_agent.actions.context import env  # noqa: E402
from room_agent.actions.core import Capability, Group, Risk, register, register_group  # noqa: E402
from room_agent.actions.events import events  # noqa: E402
from room_agent.actions.executor import Plan, execute  # noqa: E402
from room_agent.llm import openai_backend  # noqa: E402
from room_agent.tools.registry import active_tools, run_tool  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"   ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


# ---------------------------------------------------------------- a pretend future module: "lamp" + "notes"
LAMP = {"brightness": 40, "broken": False}
SENT = []


def set_brightness(args):
    if LAMP["broken"]:
        return "OK: done"  # a buggy implementation that claims success without doing it
    LAMP["brightness"] = int(args["level"])
    return f"OK: the desk lamp is at {LAMP['brightness']}%."


def put_brightness(args, before, after):
    LAMP["brightness"] = before["brightness"]
    return f"OK: the desk lamp is back to {LAMP['brightness']}%."


register_group(Group("desk", re.compile(r"lamp|light|bright|dim", re.I), title="desk lamp",
                     summary="set the desk lamp's brightness (undoable)"))
register(Capability(
    name="set_lamp_brightness", description="Set the desk lamp's brightness.", group="desk",
    parameters={"type": "object", "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}},
                "required": ["level"]},
    examples=["dim the lamp", "lamp to 70"], execute=set_brightness,
    observe=lambda args, before=None: {"brightness": LAMP["brightness"]},
    verify=lambda args, before, after: after["brightness"] == int(args["level"]),
    undo=put_brightness, undo_is_symmetric=True, event="lamp.changed", claim="home"))
register(Capability(
    name="send_note", description="Send a note to someone.", group="desk", risk=Risk.SENSITIVE,
    parameters={"type": "object", "properties": {"to": {"type": "string"}, "text": {"type": "string"}},
                "required": ["to", "text"]},
    execute=lambda args: (SENT.append(args), f"OK: sent to {args['to']}.")[1], claim="message"))
register(Capability(name="lamp_offline", description="x", parameters={"type": "object", "properties": {}},
                    execute=lambda a: "OK", available=lambda: False))

print("plugging in a new module = registering capabilities:")
names = {t["name"] for t in active_tools()}
check("its capabilities are offered to the model", {"set_lamp_brightness", "send_note"} <= names)
check("an unavailable one isn't", "lamp_offline" not in names)
check("examples become part of what the model reads", "'dim the lamp'" in core.get("set_lamp_brightness").schema()["description"])
caps = truth.capabilities()
check("it shows up in the model's 'what I can do' list", any(c.name == "desk lamp" for c in caps))
offered = {t["name"] for t in openai_backend.relevant_tools(active_tools(), [{"role": "user", "content": "dim the lamp a bit"}])}
check("its area's tools are offered when the request is about it", "set_lamp_brightness" in offered)
offered = {t["name"] for t in openai_backend.relevant_tools(active_tools(), [{"role": "user", "content": "how's it going?"}])}
check("...and not for plain chat", "set_lamp_brightness" not in offered)
check("its claims are checked like built-in ones", "set_lamp_brightness" in truth.VERIFIED_BY["home"]
      and "set_lamp_brightness" in truth.ACTION_TOOLS)

print("executor:")
seen = []
events.on("lamp.*", lambda e: seen.append(e))
r = execute("set_lamp_brightness", {"level": 70})
check("structured result", r.success and r.verified and r.state_before == {"brightness": 40}
      and r.state_after == {"brightness": 70} and r.message.startswith("OK"), r.as_dict())
check("event delivered to a subscriber that the module doesn't know about", seen and seen[-1]["after"] == {"brightness": 70})
r = execute("set_lamp_brightness", {"level": 300})
check("bad parameters are refused before anything runs", not r.success and LAMP["brightness"] == 70, r.message)
LAMP["broken"] = True
r = execute("set_lamp_brightness", {"level": 10})
check("an implementation that says OK without doing it is caught by verification",
      not r.success and not r.verified and "didn't actually happen" in r.message, r.message)
LAMP["broken"] = False
r = execute("make_coffee", {})
check("an invented capability is refused", not r.success and r.error_code == "unavailable" and r.recoverable is False)
check("run_tool still returns the same plain text", run_tool("make_coffee", {}).startswith("UNAVAILABLE"))

print("risk levels:")
rt.turn_no, rt.pending = 10, None
r = execute("send_note", {"to": "Sam", "text": "hi"})
check("SENSITIVE: asks first, even for a clear request", r.message.startswith("NEEDS_CONFIRMATION") and not SENT, r.message)
r = execute("send_note", {"to": "Sam", "text": "hi"})
check("...asking again in the same turn doesn't count as a yes", r.message.startswith("NEEDS_CONFIRMATION") and not SENT)
rt.turn_no = 11
rt.turn_text = "what time is it?"  # (not a yes)
r = execute("send_note", {"to": "Sam", "text": "hi"})
check("...a next turn that isn't a yes doesn't count", r.message.startswith("NEEDS_CONFIRMATION") and not SENT)
rt.turn_no, rt.turn_text = 12, "yes, send it"  # they said yes
r = execute("send_note", {"to": "Sam", "text": "hi"})
check("...after they confirm (next turn), it runs", r.success and SENT, r.message)
check("CONFIRM: close_app with an unclear request asks first", core.get("close_app").risk == Risk.CONFIRM
      and execute("close_app", {"app_name": "Spotify", "confidence": 0.3}).message.startswith("NEEDS_CONFIRMATION"))

print("undo:")
env.undo_stack.clear()
execute("set_lamp_brightness", {"level": 20})
check("context lists what can be undone", "set_lamp_brightness" in env.describe())
out = run_tool("undo_last_action", {})
check("'undo that' -> back to 70", out.startswith("OK") and LAMP["brightness"] == 70, out)
out = run_tool("undo_last_action", {})
check("'undo that' again -> redo, 20", out.startswith("OK") and LAMP["brightness"] == 20, out)
env.undo_stack.clear()
env.last_successful_action = None
check("nothing to undo -> says so", run_tool("undo_last_action", {}).startswith("FAILED: there's nothing recent"))

print("plans:")
fails = Plan()
r1 = fails.run("set_lamp_brightness", {"level": 999})
r2 = fails.run("undo_last_action", {})
check("steps without a shared subject still run after a failure", not r1.success and r2.message.startswith("FAILED: there's nothing"))
p = Plan()
a = p.run("open_app", {"app_name": "Definitely Not An App 123"})
b = p.run("move_window_to_monitor", {"app": "it", "monitor": "2"})
c = p.run("maximize_window", {"app": "Definitely Not An App 123"})
check("open fails -> 'move it' is not run", not a.success and b.error_code == "dependency_failed", b.message)
check("open fails -> a later step on the same app by name is not run", c.error_code == "dependency_failed", c.message)
check("plan summary", p.summary().startswith("0 of 3 done"), p.summary())

print("\nALL ACTION ARCHITECTURE TESTS PASSED" if not FAILS else f"\nFAILED: {FAILS}")
sys.stdout.flush()
os._exit(1 if FAILS else 0)
