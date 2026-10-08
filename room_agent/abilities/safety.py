"""Emergency stop by voice (implementation: emergency.py; also the global hotkey and the dashboard)."""

import re

from room_agent.abilities._kit import NO_ARGS, tool
from room_agent.actions.core import Group, register_group

register_group(Group("safety", re.compile(r"stop everything|emergency|abort|cancel everything|stop all", re.I), lambda: False,
                     "emergency stop", "'stop everything' (or Ctrl+Alt+J anywhere) stops all Jarvis activity at once"))


def _stop(args):
    from room_agent import emergency

    done = emergency.stop_everything("voice")
    return "OK: stopped " + (", ".join(done) if done else "everything (nothing else was running)") + "."


tool("emergency_stop", "Stop EVERYTHING Jarvis is doing right now (speech, research, page actions, pending requests, a "
     "scheduled shutdown). For 'stop everything', 'emergency stop', 'abort'.", NO_ARGS, _stop, group="safety",
     reflex=[(r"(?:emergency\s+stop|stop\s+everything|stop\s+all(?:\s+actions)?|abort(?:\s+everything)?|"
              r"cancel\s+everything|stop\s+what\s+you'?re\s+doing)", {})],
     reflex_say=lambda r: "Stopped.")
