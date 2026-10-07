"""Undo: put back the last change that can be put back (the executor keeps what each change can undo)."""

import re
import time

from room_agent.abilities._kit import FOLLOW_UP_S, params, tool
from room_agent.actions.context import env
from room_agent.actions.core import Group, register_group

register_group(Group(
    "undo", re.compile(r"undo|put (it|that|them) back|move (it|that) back|change (it|that) back|set (it|that) back|revert|"
                       r"as it was|the way it was|back how|redo", re.I),
    lambda: bool(env.undo_stack and time.time() - env.undo_stack[-1].at < FOLLOW_UP_S), "undo",
    "undoes the last change that can be undone (volume, a window's place or size, play/pause, a timer or app it just set or "
    "opened), and says plainly when something can't be undone"))


def _undo(args):
    from room_agent.actions.executor import undo

    return undo(args.get("action"), args.get("app"))


tool("undo_last_action", "Undo a recent change: 'undo that', 'put it back', 'move it back', 'change the volume back'. Only "
     "what's listed under can_undo in the runtime context can be undone; say plainly when something can't.",
     params({"action": {"type": "string", "description": "Only if they say which kind of change: 'move', 'volume', 'maximize', "
                                                         "'timer'... Leave out for the latest change."},
             "app": {"type": "string", "description": "Only if they say whose change: an app name, or 'it'."}}),
     _undo, group="undo", claim=["app", "media", "timer"])
