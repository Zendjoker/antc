"""Live diagnostic mode, offline: every event kind is written (speech, state, tool, turn, barge), secrets / phone numbers /
emails / tool argument values never reach the file, and nothing is written when the mode is off. No audio, no API calls.

Run:  .venv\\Scripts\\python -m tests.test_diagnostics
"""

import json
import os
from pathlib import Path

from tests.harness import setup_env

tmp = setup_env(DIAGNOSTICS="1", TWILIO_AUTH_TOKEN="tw-auth-0123456789abcdef")
os.environ["DIAGNOSTICS_DIR"] = os.path.join(tmp, "logs")

from room_agent import config, livelog  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.conversation.states import State  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

t = Checker()
convo = Conversation()
convo.say("set a timer for 2 minutes for the pasta", scripts=[{"tools": [("set_timer", {"seconds": 120, "label": "pasta",
           "message": "Hey, the secret pasta is ready!"})]}, {"text": "Two minutes."}])
rt.state.go(State.LISTENING, "test")
rt.state.go(State.PROCESSING, "test")
livelog.event("speech", accepted=True, label="USER", why="test", text="call me at +1 415 555 0100 or mail me@example.com, "
                  "my password is hunter22 and the token tw-auth-0123456789abcdef", stt_s=0.16)
livelog.event("speech", accepted=False, label="AGENT_ECHO", why="short fragment of what the agent was saying", text="okay")
livelog.event("barge", confirmed=True, why="voice verified", voice_to_stop_s=0.31)

files = list(Path(config.DIAGNOSTICS_DIR).glob("diagnostics-*.jsonl"))
t.check("DIAGNOSTICS=1 -> one JSONL file in the logs folder", len(files) == 1, files)
raw = files[0].read_text(encoding="utf-8") if files else ""
rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
kinds = {r["kind"] for r in rows}
t.check("every line is valid JSON with a time and a kind", rows and all("t" in r and "kind" in r for r in rows), len(rows))
t.check("all event kinds recorded (speech, state, tool, turn, barge)", {"speech", "state", "tool", "turn", "barge"} <= kinds, kinds)
tool = next((r for r in rows if r["kind"] == "tool" and r["name"] == "set_timer"), {})
t.check("tool event: name, success, timing, argument NAMES", tool.get("success") is True and "seconds" in tool
        and tool.get("args") == ["label", "message", "seconds"], tool)
t.check("...never argument values (what they asked can be private)", "secret pasta" not in raw, raw[:200])
turn = next((r for r in rows if r["kind"] == "turn"), {})
t.check("turn event has the whole-turn latency", isinstance(turn.get("whole_turn"), float), turn)
state = [r for r in rows if r["kind"] == "state"]
t.check("state transitions with from/to", any(r["frm"] == "LISTENING" and r["to"] == "PROCESSING" for r in state), state[-2:])
t.check("rejected speech recorded with why", any(r["kind"] == "speech" and r["accepted"] is False and "fragment" in r["why"] for r in rows))
t.check("barge-in timing recorded", any(r["kind"] == "barge" and r.get("voice_to_stop_s") == 0.31 for r in rows))
print("Redaction:")
t.check("no .env credential value", "tw-auth-0123456789abcdef" not in raw)
t.check("no phone number", "555 0100" not in raw and "4155550100" not in raw)
t.check("no email address", "me@example.com" not in raw)
t.check("no password", "hunter22" not in raw)
t.check("the rest of what was said is kept", "call me at" in raw)

print("Off by default:")
config.DIAGNOSTICS = False
before = len(raw.splitlines())
livelog.event("speech", text="should not be written")
t.check("DIAGNOSTICS=0 -> nothing written", len(files[0].read_text(encoding="utf-8").splitlines()) == before)
t.done("DIAGNOSTICS")
