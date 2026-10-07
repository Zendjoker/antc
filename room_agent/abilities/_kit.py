"""Small helpers shared by the ability files."""

import time

from room_agent import runtime as rt
from room_agent.actions.core import Capability, register

FOLLOW_UP_S = 600  # an area stays "live" this long after it was used ("close it", "turn it down", "undo that")

# Risky tools take this: the code refuses to run them (and asks the user) unless it's high enough (tools/validate.py)
CONFIDENCE = {"type": "number", "minimum": 0, "maximum": 1,
              "description": "0 to 1: how sure you are they explicitly asked for exactly this. Use below 0.8 when the "
                             "wording is only loosely related, or could mean something milder."}
NO_ARGS = {"type": "object", "properties": {}}


def params(properties=None, required=()):
    schema = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = list(required)
    return schema


def tool(name, description, parameters, run, **kw):
    """Register one capability. `run(args)` returns "OK: ..." / "FAILED: ..." / ... text."""
    return register(Capability(name=name, description=description, parameters=parameters, execute=run, **kw))


def recently(at):
    return bool(at) and time.time() - at < FOLLOW_UP_S


def tts_on():
    return bool(rt.tts_enabled)
