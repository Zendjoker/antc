"""Deterministic checks for the reliability layer (no network, no microphone): python tests/test_reliability.py"""
import os
import pathlib
import sys
import tempfile
import time

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ.update(REMINDERS_FILE=str(tmp / "rem.json"), MEMORY_DB=str(tmp / "mem.db"), SETTINGS_FILE=str(tmp / "set.json"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from room_agent import runtime as rt  # noqa: E402
from room_agent.audio import speech_check as sc  # noqa: E402
from room_agent.llm import guard as G  # noqa: E402
from room_agent.tools import registry, timers  # noqa: E402
from room_agent.tools.validate import current_pending, validate  # noqa: E402

failures = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name} {detail}")
    if not condition:
        failures.append(name)


rt.tts_enabled = False
print("tool validation (schema is the source of truth)")
clean, problem = validate("set_timer", {})
check("timer without seconds -> NEEDS seconds", problem and problem.startswith("NEEDS:") and "seconds" in problem)
check("...and the request is remembered", rt.pending and rt.pending["tool"] == "set_timer")
check("timer: label is not asked for", validate("set_timer", {"seconds": 5})[1] is None)
check("timer: '10' (string) is coerced", validate("set_timer", {"seconds": "10"})[0] == {"seconds": 10})
check("timer: 0 seconds refused", validate("set_timer", {"seconds": 0})[1].startswith("FAILED"))
check("timer: absurdly long refused", validate("set_timer", {"seconds": 10**9})[1].startswith("FAILED"))
check("alarm without time -> NEEDS time", validate("set_alarm", {"label": "stove"})[1].startswith("NEEDS: time"))
check("alarm: label optional", validate("set_alarm", {"time": "07:30"})[1] is None)
check("enum enforced", validate("set_speaking_rate", {"rate": "warp"})[1].startswith("FAILED"))
check("go_quiet low confidence -> confirmation", validate("go_quiet", {"confidence": 0.4})[1].startswith("NEEDS_CONFIRMATION"))
check("go_quiet missing confidence -> confirmation", validate("go_quiet", {})[1].startswith("NEEDS_CONFIRMATION"))
check("go_quiet high confidence runs", validate("go_quiet", {"confidence": 0.95}) == ({}, None))
check("forget needs confidence too", validate("forget", {"text": "x"})[1].startswith("NEEDS_CONFIRMATION"))
rt.pending["turn"] = rt.turn_no - 4
check("old pending request expires", current_pending() is None)

print("tool execution and verification")
out = registry.run_tool("set_timer", {"seconds": 5})
check("set_timer(5) runs and is verified", out.startswith("OK: timer 'timer' is running, 5 seconds"), out[:60])
check("list shows it", "timer: " in registry.run_tool("list_timers", {}))
check("cancel low confidence is not executed", registry.run_tool("cancel_timer", {"label": "all", "confidence": 0.3}).startswith("NEEDS_CONFIRMATION"))
check("...and it still exists", "timer: " in registry.run_tool("list_timers", {}))
check("cancel confident works", registry.run_tool("cancel_timer", {"label": "all", "confidence": 0.9}).startswith("OK: cancelled 1"))
check("unknown tool refused", registry.run_tool("make_coffee", {}).startswith("UNAVAILABLE"))
check("unavailable capability refused (Home Assistant)", registry.run_tool("home_assistant", {"domain": "light", "service": "turn_on", "entity_id": "light.x"}).startswith("UNAVAILABLE"))
check("missing params come back as NEEDS", registry.run_tool("set_timer", {}).startswith("NEEDS:"))
timers.REMINDERS_FILE = pathlib.Path("Z:/no/such/dir/rem.json")
out = registry.run_tool("set_timer", {"seconds": 30})
check("save failure is reported truthfully", out.startswith("OK") and "won't survive a restart" in out, out[-60:])
registry.run_tool("cancel_timer", {"label": "all", "confidence": 1})

print("audio ownership (user vs the agent's own voice)")


class Delay:
    delay = 0.45


class Engine:
    delay = Delay()


rt.engine = Engine()
rt.recent_speech.extend(["Want me to set a 30-second timer instead?", "That's rough, especially after such a big day."])
now = time.time()
rt.tts_end = now
cases = [
    ("you, 3 s after it stopped, similar words", "Set a timer for 10 seconds.", now + 3.0, sc.USER),
    ("you, 5 s after, 'rough' words", "Just a rough night.", now + 5.0, sc.USER),
    ("echo while it was talking", "Want me to set a thirty second timer instead", now - 0.5, sc.AGENT_ECHO),
    ("exact copy just after it stopped", "Want me to set a 30-second timer instead", now + 0.2, sc.AGENT_ECHO),
    ("you answering with its words, 0.2 s after it stopped", "Yeah set a timer for ten seconds instead", now + 0.2, sc.UNCERTAIN),
    ("different words during playback", "What's the weather in Chicago today", now - 0.5, sc.USER),
    ("silence hallucination", "Thank you.", now + 5, sc.NOISE),
    ("start unknown, near-exact copy", "Want me to set a 30-second timer instead?", None, sc.AGENT_ECHO),
]
for name, text, started, expected in cases:
    label, detail = sc.classify_audio(text, started)
    check(name, label == expected, f"-> {label} ({detail})")
rt.tts_end = now + 10  # still speaking: uncertain must not be kept
check("loosely similar while it is still talking is UNCERTAIN (the session drops it only then)",
      sc.classify_audio("Okay maybe a timer for ten seconds", now + 9.9)[0] == sc.UNCERTAIN)
rt.engine = None

print("turn outcomes")
spoken = []
guard = G.new_guard()
rt.turn_signal = ""
G.speak_checked(guard, "<listen>", spoken)
check("<listen> is captured, never spoken", rt.turn_signal == "listen" and spoken == [])
rt.turn_signal = ""
G.speak_checked(guard, "<silent>", spoken)
check("<silent> is captured, never spoken", rt.turn_signal == "silent" and spoken == [])
G.speak_checked(guard, "Sure thing.", spoken)
check("normal sentences still speak", spoken == ["Sure thing."])

print("\nALL PASSED" if not failures else f"\nFAILED: {failures}")
sys.exit(1 if failures else 0)
