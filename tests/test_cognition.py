"""Cognitive architecture scenarios (room_agent/cognition/), offline: a simulated PC (apps, monitors, volume, ports,
a dev server registered like any future capability), a scripted model (no cost), and the real executor, permissions,
verification, beliefs, goals, limits, experience store, metrics and trace.

    .venv\\Scripts\\python -m tests.test_cognition

Each scenario checks behavior (what ran, what was observed, what was claimed), not just functions.
"""

import logging
import re
import time

from tests.harness import Checker, Conversation, setup_env

TMP = setup_env(TRACE="1")
LOGS = []


class Capture(logging.Handler):
    def emit(self, record):
        LOGS.append(record.getMessage())


logging.basicConfig(level=logging.INFO)
logging.getLogger("room-agent").addHandler(Capture())

from room_agent import cognition  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core  # noqa: E402
from room_agent.actions.context import env  # noqa: E402
from room_agent.actions.executor import ActionResult  # noqa: E402
from room_agent.cognition.goal import Goal  # noqa: E402
from room_agent.tools import apps  # noqa: E402

t = Checker()
check = t.check

from tests.sim_pc import (BUG, EP, OPENED, PORTS, RUNNING, STOPPED, WIN, fake_open)  # noqa: E402

convo = Conversation(engine=None)


def say(text, calls=None, reply="Okay.", scripts=None):
    LOGS.clear()
    t0 = time.time()
    res, system, tools = convo.say(text, calls, reply, scripts=scripts)
    rows = cognition.store().turns(1)
    return {"results": res, "system": system, "tools": tools, "requests": list(convo.requests), "said": convo.said(),
            "seconds": time.time() - t0, "metrics": rows[0] if rows else {}, "trace": "\n".join(LOGS)}


# ---------------------------------------------------------------- SCENARIO 1: a reflex
print("Scenario 1: 'Open Spotify.' (REFLEX)")
r = say("Open Spotify.")
check("REFLEX: no model call, no goal, Spotify open (verified), a short confirmation",
      not r["requests"] and r["metrics"].get("level") == "REFLEX" and "Spotify" in RUNNING and r["said"]
      and r["metrics"].get("goal") is None and not r["metrics"].get("model_calls"), (r["metrics"], r["said"]))
fast = say("what's the time", reply="It's noon.")
check("reflex latency: no slower than a FAST turn even with a free, instant fake model "
      f"({r['seconds'] * 1000:.0f} ms vs {fast['seconds'] * 1000:.0f} ms)", r["seconds"] <= fast["seconds"] + 0.05)
t0 = time.time()
for _ in range(50):
    rt.new_turn("what's the weather tomorrow")
    cognition.begin_turn("what's the weather tomorrow")
    cognition.end_turn("", None)
check("the cognitive routing itself costs ~nothing per turn", (time.time() - t0) / 50 < 0.01, round((time.time() - t0) * 20, 2))

# ---------------------------------------------------------------- SCENARIO 2: a reference, current state, verified
print("Scenario 2: 'Move it to the other monitor.'")
r = say("Move it to the other monitor.")
check("'it' resolved from context (Spotify), current monitor read (1), moved to 2, EXPECTED = OBSERVED",
      WIN["Spotify"] == 2 and not r["requests"] and "EXPECTED: {'monitor.num': 2}" in r["trace"]
      and "OBSERVED: {'monitor.num': 2}" in r["trace"], (WIN, r["trace"][-600:]))
check("...and it's now a VERIFIED belief", env.status_of("window.Spotify.monitor") == "VERIFIED"
      and env.belief("window.Spotify.monitor").value["num"] == 2)
BUG["move_lands_on"] = 1  # (from monitor 2, "the other one" means 3; the window ends up on 1 but the tool says OK)
r = say("Move it to the other monitor.", scripts=[{"text": "Moved it over."}, {"text": "It didn't land where it should."}])
check("the tool says OK but it landed on 1, not 3: ACTION DID NOT ACHIEVE EXPECTED STATE, never claimed as done",
      WIN["Spotify"] == 1 and r["metrics"].get("verification_failures") == 1 and not any("Moved" in s for s in r["said"])
      and "EXPECTED: {'monitor.num': 3}" in r["trace"] and "OBSERVED: {'monitor.num': 1}" in r["trace"],
      (r["said"], r["metrics"], r["trace"][-400:]))
BUG["move_lands_on"] = None

# ---------------------------------------------------------------- SCENARIO 3: unknown -> observe, never guess
print("Scenario 3: 'Is my backend running?'")
env.forget_beliefs()
PORTS.clear()
PORTS[8000] = "python.exe (uvicorn)"
r = say("Is my backend running?", [("inspect_ports", {})], "Yes, uvicorn's up on 8000.")
check("DELIBERATE, a goal, told to check with a read-only tool instead of guessing; inspect_ports offered",
      r["metrics"].get("level") == "DELIBERATE" and "never guess" in r["system"] and "inspect_ports" in r["tools"]
      and "read-only checks available for this" in r["system"], r["system"][-900:])
check("it observed (1 observation, 0 actions) and the answer came from the observation",
      r["metrics"].get("observations") == 1 and r["metrics"].get("actions") == 0 and "uvicorn" in r["results"][0], r["metrics"])
r = say("Is my backend running?", [("inspect_ports", {})], "Still up.")
check("asked again within its freshness: what's verified is shown as known (no need to check again)",
      "known now (verified" in r["system"], r["system"][-700:])

# ---------------------------------------------------------------- SCENARIO 4: a goal with a taught routine
print("Scenario 4: 'Get my development environment ready.'")
from room_agent import learning  # noqa: E402

learning.user_model().teach("routine", [{"action": "open_app", "args": {"app_name": "Visual Studio Code"}},
                                        {"action": "start_dev_server", "args": {}}], subject="I'm working",
                            because="you taught it")
PORTS.clear()
OPENED.clear()
r = say("Get my development environment ready.", [("run_routine", {"trigger": "I'm working"})],
        "VS Code is open and the dev server is up.")
check("goal-based: DELIBERATE, the taught routine offered as context (nothing invented)", r["metrics"].get("level") == "DELIBERATE"
      and "routines they taught you" in r["system"] and "i'm working" in r["system"].lower(), r["system"][-900:])
check("minimal plan: exactly what the routine needs, verified (VS Code open, server reachable), goal SATISFIED",
      OPENED == ["Visual Studio Code"] and PORTS.get(8000) == "devserver" and "STATUS: SATISFIED" in r["trace"], (OPENED, PORTS))

# ---------------------------------------------------------------- SCENARIO 5: failure -> update state -> replan -> honest
print("Scenario 5: port already occupied")
PORTS.clear()
PORTS[8000] = "python.exe (an old run)"
r = say("Start my dev server.", scripts=[{"tools": [("start_dev_server", {})]}, {"tools": [("inspect_ports", {"port": 8000})]},
                                         {"text": "The server's running!"},
                                         {"text": "Port 8000 is taken by an old python process. Want me to stop it?"}])
check("the failure is noticed, the world model updated (who owns 8000 observed), and it REPLANNED",
      r["metrics"].get("replans", 0) >= 1 and "REPLAN" in r["trace"] and r["metrics"].get("observations") == 1
      and env.relevant_beliefs(["inspect_ports"]), r["metrics"])
check("no false success: 'The server's running!' never spoken; goal not satisfied", not any("running!" in s for s in r["said"])
      and "STATUS: SATISFIED" not in r["trace"], (r["said"], r["trace"][-500:]))

# ---------------------------------------------------------------- SCENARIO 6: the user changes the plan
print("Scenario 6: 'Actually don't do that.'")
RUNNING.clear()
OPENED.clear()


def open_then_interrupted(name):
    out = fake_open(name)
    rt.turn.cancel.set()  # (they talk over Jarvis right after the first step)
    return out


apps.open_app = open_then_interrupted
r = say("Open Chrome and VS Code, then start the server.", [("open_app", {"app_name": "Chrome"}),
                                                              ("open_app", {"app_name": "VS Code"}), ("start_dev_server", {})])
apps.open_app = fake_open
check("interrupted mid-plan: the remaining steps were NOT run, the goal waits (not finished blindly)",
      OPENED == ["Google Chrome"] and any("not run, they interrupted" in x for x in r["results"])
      and "STATUS: BLOCKED" in r["trace"], (OPENED, r["results"]))
RUNNING.clear()
OPENED.clear()
r = say("Actually, don't open Chrome.", [("open_app", {"app_name": "Chrome"}), ("open_app", {"app_name": "VS Code"})],
        "Okay, just VS Code then.")
check("the constraint changes the rest of the plan: Chrome refused, VS Code done",
      OPENED == ["Visual Studio Code"] and any("they said" in x for x in r["results"]), (OPENED, r["results"]))
say("Open Chrome and start the server.", reply="Which server, the dev one?")  # (the goal waits for their answer)
r = say("Never mind.")
check("'Never mind' cancels the goal (counted as a cancellation)", cognition.active_goal() is None
      and r["metrics"].get("cancellations") == 1, r["metrics"])

# ---------------------------------------------------------------- SCENARIO 7: experience is a hypothesis, not the answer
print("Scenario 7: past experience")
env.forget_beliefs()
PORTS.clear()
old = Goal(user_request="start the dev server", objective="start the dev server")
old.status = "FAILED"
cognition.store().record(old, [
    ActionResult(False, "start_dev_server", {}, "FAILED: port 8000 is already in use, so the dev server couldn't start."),
    ActionResult(True, "inspect_ports", {"port": 8000}, "OK: port 8000 is in use by python.exe (a stale run).")],
    lambda n: False, 12.0)
r = say("The dev server won't start again, figure out why.", scripts=[{"tools": [("inspect_ports", {"port": 8000})]},
                                                {"tools": [("start_dev_server", {})]}, {"text": "It's up."}])
check("the past experience is offered as a hint to check first, explicitly 'not proof'",
      "past experience (a hint, not proof" in r["system"] and "stale run" in r["system"], r["system"][-900:])
check("the current state was observed before acting, and the cause wasn't assumed (port free this time -> started)",
      PORTS.get(8000) == "devserver" and r["metrics"].get("observations") == 1 and r["metrics"].get("actions") == 1
      and r["trace"].index("OBSERVE") < r["trace"].index("STEP"), (PORTS, r["metrics"]))

# ---------------------------------------------------------------- SCENARIO 8: capability unavailable
print("Scenario 8: capability unavailable")
r = say("Turn on the bedroom lights.", [("home_assistant", {"domain": "light", "service": "turn_on",
                                                             "entity_id": "light.bedroom"}), ("teleport", {})],
        scripts=None)
check("no Home Assistant -> UNAVAILABLE; an invented tool -> UNAVAILABLE; nothing claimed",
      all(x.startswith("UNAVAILABLE") for x in r["results"]) and "home_assistant" not in r["tools"], r["results"])

# ---------------------------------------------------------------- SCENARIO 9: a sensitive step in the plan
print("Scenario 9: permissions")
r = say("Kill the old python process.", [("stop_process", {"name": "python"}), ("stop_process", {"name": "python"})],
        "Done, it's stopped.")
check("the permission system intercepts it (asked first, nothing stopped), even when the model insists in one turn",
      not STOPPED and all(x.startswith("NEEDS_CONFIRMATION") for x in r["results"])
      and not any("stopped" in s for s in r["said"]), (r["results"], r["said"]))

# ---------------------------------------------------------------- SCENARIO 10: already satisfied
print("Scenario 10: goal already satisfied")
EP.level, EP.muted, EP.sets = 0.4, False, 0
r = say("Set the volume to 40.")
check("volume already 40: verified 'already so', no action performed (no Set call), honest short reply",
      EP.sets == 0 and r["metrics"].get("already_satisfied") == 1 and not r["requests"] and "40" in " ".join(r["said"]),
      (EP.sets, r["metrics"], r["said"]))
PORTS[8000] = "devserver"
r = say("Make sure the dev server is running.", [("start_dev_server", {})], "It's already running.")
check("a registered capability with `expect`: already satisfied -> not started again", r["results"][0].startswith(
      "OK: nothing needed") and PORTS[8000] == "devserver" and r["metrics"].get("actions") == 0, (r["results"], r["metrics"]))

# ---------------------------------------------------------------- replanning a different way; offers aren't claims
print("Replanning a different way:")
RUNNING.clear()
OPENED.clear()
r = say("Get VS Code ready for me.", scripts=[{"tools": [("focus_app", {"app_name": "VS Code"}), ("open_app", {"app_name": "VS Code"})]},
                                              {"tools": [("open_app", {"app_name": "VS Code"})]}, {"text": "VS Code is open."}])
check("in the same round a step on a failed subject is skipped; the next round (a replan) may try another way",
      "not done, because focus_app" in r["results"][1] and r["results"][2].startswith("OK") and "Visual Studio Code" in RUNNING
      and r["metrics"].get("replans") == 1, r["results"])
from room_agent import truth  # noqa: E402

g = truth.ClaimGuard(lambda: 0, lambda: False)
check("an offer ('if you tell me how long, I can set a timer') isn't held as a claim; a promise or a claim still is",
      not g.unverified("If you tell me roughly how long, I can set a timer to remind you.")
      and g.unverified("I'll remind you in an hour.") and g.unverified("Timer's set.") and g.unverified("I can see your calendar."))

# ---------------------------------------------------------------- limits, waiting goals, metrics, trace
print("Limits, waiting goals, metrics, trace:")
endless = [{"tools": [("inspect_ports", {"port": 8000 + i})]} for i in range(12)] + [{"text": "I'll stop there."}]
r = say("Figure out why my server keeps failing.", scripts=endless)
check("no infinite loop: the tool rounds stop at the limit and it reports honestly",
      r["metrics"].get("rounds", 0) <= 8 and len(r["results"]) <= 8 and "LIMIT" in r["trace"], r["metrics"])
r = say("Tell me when my download finishes.", reply="I can't watch downloads yet, want a reminder instead?")
check("a 'tell me when' goal: kept as a goal with a trigger, honest that nothing can watch yet",
      "no background watching yet" in r["system"] and cognition._state["recent"][-1].trigger or cognition.active_goal(),
      r["system"][-500:])
rows = cognition.store().turns(200)
check("every turn's metrics were recorded (level, model calls with provider/model and tokens, actions, observations...)",
      rows and all("level" in m and "model_calls" in m for m in rows)
      and any(c["model"] == "gpt-5-mini" and c["in"] == 3000 and c["out"] == 30 for m in rows for c in m["model_calls"]), rows[:1])
from room_agent.cognition.metrics import summarize  # noqa: E402

report = summarize(rows)
check("metrics summarize per level (to compare models objectively)", {"REFLEX", "FAST", "DELIBERATE"} <= set(report)
      and report["REFLEX"]["model_calls"] == 0, report)
check("the trace is operational (GOAL / LEVEL / OBSERVE / STEP / STATUS), with no model reasoning and no secrets",
      all(k in "\n".join(LOGS + [r["trace"]]) for k in ("LEVEL:", "GOAL:", "STATUS:"))
      and not re.search(r"sk-|GOCSPX-|ya29\.", "\n".join(LOGS)))
exp = cognition.store().similar("dev server", k=5)
check("experiences are stored structured (goal, observations, actions, outcome), separate from memory and learning",
      exp and all(k in exp[0] for k in ("goal", "observations", "actions", "outcome", "success"))
      and rt.memory.count() == 0, exp[:1])

t.done("COGNITION SCENARIOS")
