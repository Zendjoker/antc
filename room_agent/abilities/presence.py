"""Quiet mode: stop talking until the wake word (handled by the conversation loop; this is the model's way to ask)."""

from room_agent import runtime as rt
from room_agent.abilities._kit import CONFIDENCE, params, tool
from room_agent.actions.core import Group, Risk, register_group

register_group(Group("quiet", title="quiet mode", summary="goes silent until the wake word", rules=[
    "- If they want you to be quiet, stop talking, or not answer until they call you, call go_quiet. Don't pretend to stay "
    "quiet by replying."]))


def _go_quiet(args):
    rt.control["request"] = "quiet"
    return "OK: going quiet until they say the wake word."


tool("go_quiet", "Go completely quiet: stop talking and stop answering until they say the wake word (hey Jarvis) again. Only "
     "when they clearly ask you to be quiet, stop talking, go to sleep, leave them alone, or not respond until they call "
     "you. Asking you to wait, be patient, let them finish or just listen is NOT this: that's set_listening_patience. Say "
     "nothing else; the system acknowledges it.",
     params({"confidence": CONFIDENCE}), _go_quiet, group="quiet", risk=Risk.CONFIRM, min_confidence=0.8)
