"""Registry lint, offline: every tool's schema is valid, reflex patterns compile and fill real parameters, risky tools can
describe themselves for the confirmation question, each intent-gated tool accepts its own example phrases (or its
instant command would be blocked by its own gate), and no tool name or reflex phrase is claimed by two tools.

Run:  .venv\\Scripts\\python -m tests.test_tool_registry
"""

import re

from tests.harness import setup_env

setup_env()

from room_agent.actions import core  # noqa: E402
from room_agent.cognition import reflex  # noqa: E402
from tests.harness import Checker  # noqa: E402
from tests.tool_audit import lint  # noqa: E402

t = Checker()
core.ensure_loaded()
caps = core.capabilities()
problems = [(c.name, p) for c in caps for p in lint(c)]
t.check(f"all {len(caps)} tool schemas valid, reflexes compile and fill real parameters", not problems, problems)
t.check("every SENSITIVE tool can say what it will do (for the yes/no question)",
        all(c.describe is not None for c in caps if c.risk == core.Risk.SENSITIVE),
        [c.name for c in caps if c.risk == core.Risk.SENSITIVE and c.describe is None])
blocked = [(c.name, e) for c in caps if c.intent is not None for e in c.examples if not c.intent.search(e)]
t.check("intent-gated tools accept their own example phrases (no self-blocking)", not blocked, blocked)
gated_reflex = []
for c in caps:
    if c.intent is None:
        continue
    for pattern, _ in c.reflex or []:
        sample = re.sub(r"\(\?P<\w+>[^)]*\)", "x", pattern)  # (a rough check: the fixed words of the pattern)
        words = re.findall(r"[a-z]{3,}", sample.replace("\\s", " "))
        if words and not c.intent.search(" ".join(words)):
            gated_reflex.append((c.name, pattern[:60]))
t.check("an intent-gated tool's instant commands contain words its gate accepts", not gated_reflex, gated_reflex)
SAMPLES = ["open YouTube", "search YouTube for jazz", "go back", "scroll down", "lock the pc", "turn on dark mode",
           "add milk to my shopping list", "what's on my shopping list", "play my gym playlist", "stop everything",
           "cancel the shutdown", "brightness to 40", "turn off bluetooth", "open source 2",
           "check off milk", "pause", "next song", "volume up"]
from room_agent.computer import browsers  # noqa: E402

browsers.foreground_browser = lambda: ("opera", 1)  # (a browser in front: its instant commands apply)
browsers.browser_windows = lambda key=None: [("opera", 1, "x - Opera")]
owners = {s: (reflex.match(s)[0].name if reflex.match(s) else None) for s in SAMPLES}
t.check("common instant commands each reach one tool (no shadowing by an earlier pattern)", all(
    v is not None for k, v in owners.items() if k != "open source 2"), owners)
t.check("...and the right one", owners["open YouTube"] == "open_url"
        and owners["stop everything"] == "emergency_stop" and owners["play my gym playlist"] == "play_music"
        and owners["lock the pc"] == "lock_pc" and owners["pause"] == "play_pause", owners)
browsers.foreground_browser = lambda: (None, None)  # (Spotify in front, no recent browser use)
from room_agent.computer.context import desk  # noqa: E402

desk.clear()
t.check("'go back' with no browser in play -> not an instant browser command (could mean the previous song)",
        reflex.match("go back") is None or reflex.match("go back")[0].name != "browser_navigate",
        reflex.match("go back") and reflex.match("go back")[0].name)
t.check("...'scroll down' / 'open the second one' likewise", all(
    (reflex.match(x) is None or not reflex.match(x)[0].name.startswith("browser")) for x in ("scroll down", "open the second one")))
print("Verification is explicit:")
from room_agent.actions import executor, journal  # noqa: E402
from room_agent import runtime as rt  # noqa: E402

kinds = {c.name: c.verification for c in caps}
t.check("every tool says how its result is checked (independent / internal / none / read)",
        set(kinds.values()) <= {"independent", "internal", "none", "read"} and all(kinds.values()))
internal_without_evidence = [c.name for c in caps if c.verification == "internal" and not c.verified_by]
t.check("every self-checking tool says HOW it checks", not internal_without_evidence, internal_without_evidence)
unchecked = sorted(n for n, k in kinds.items() if k == "none")
print("     not verifiable (reported UNVERIFIED when OK):", ", ".join(unchecked))
cap = core.get(unchecked[0]) if unchecked else None
fake = core.Capability(name="test_unchecked_action", description="a test action nothing can check", parameters={"type": "object",
                       "properties": {}}, execute=lambda a: "OK: done it.")
core.register(fake)
rt.new_turn("do the test action")
r = executor.execute("test_unchecked_action", {})
t.check("an unchecked OK is success but NOT verified, and the model is told", r.success and not r.verified
        and r.outcome == "unverified" and "Unverified" in r.to_model())
t.check("...the journal records UNVERIFIED, not COMPLETED", journal.recent(1)[0]["state"] == "UNVERIFIED", journal.recent(1)[0]["state"])
unknown = core.Capability(name="test_unknown_action", description="a test action whose outcome can't be told",
                          parameters={"type": "object", "properties": {}}, execute=lambda a: "UNKNOWN: not confirmed: maybe.",
                          event="email.sent", risk=core.Risk.SAFE)
core.register(unknown)
rt.new_turn("send the test thing")
r = executor.execute("test_unknown_action", {})
t.check("UNKNOWN: not success, outcome unknown, journal UNKNOWN, the model told not to repeat it", not r.success
        and r.outcome == "unknown" and journal.recent(1)[0]["state"] == "UNKNOWN" and "don't just repeat" in r.to_model())
plan = executor.Plan()
plan.run("test_unknown_action", {})
plan.round = 1
r2 = plan.run("test_unknown_action", {})
t.check("...asked again in a later round of the same request -> NOT run again (it may have happened)",
        r2.outcome == "unknown" and "not run again" in r2.message, r2.message)
t.done("TOOL REGISTRY")
