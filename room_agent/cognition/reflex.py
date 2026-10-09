"""REFLEX: simple, fully specified commands ("open Spotify", "volume down", "pause") run straight through the executor,
with no model call at all. Generic: each capability may declare `reflex` patterns in its own ability file
(named groups become its arguments) and a `reflex_check` (e.g. the app must exist in the cached app list). This module
only matches and runs them. Anything that doesn't match a pattern exactly goes to the model as before; a reflex that
doesn't come back OK hands its result to the model, so nothing is tried twice.

Every reflex still goes through actions/executor.execute: the same validation, permissions, verification, undo,
learned preferences, events and claims as a model's tool call.
"""

import random
import re

from room_agent import runtime as rt

PREFIX = (r"^\W*(?:(?:hey|ok(?:ay)?|yo)\s+)?(?:jarvis\W+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
          r"(?:just\s+)?")
SUFFIX = r"\s*(?:,?\s*(?:please|for me|now|thanks|thank you|jarvis))*\W*$"
DONE = ["Done.", "Got it.", "There you go.", "Okay, done."]


def match(text):
    """-> (capability, args) for a fully specified simple command, or None."""
    from room_agent.actions import core

    t = str(text or "").strip()
    if not t or len(t.split()) > 8:
        return None
    offered = None
    for cap in core.capabilities():
        for pattern, defaults in cap.reflex or []:
            m = re.match(PREFIX + pattern + SUFFIX, t, re.I)
            if not m:
                continue
            offered = offered if offered is not None else {x["name"] for x in core.offered()}
            if cap.name not in offered:
                return None  # (unavailable: the model explains it honestly)
            args = {**defaults, **{k: v.strip() for k, v in m.groupdict().items() if v and v.strip()}}
            try:
                if cap.reflex_check and not cap.reflex_check(args):
                    continue
            except Exception:
                continue
            return cap, args
    return None


def run(cap, args):
    """Run it now. -> (spoken line or None, ActionResult). None: not OK, the model takes over with the result."""
    from room_agent.actions.executor import Plan

    plan = Plan()
    rt.current_plan = plan
    rt.turn.via = "reflex"  # (the audit log says it ran without a model call)
    result = plan.run(cap.name, args)
    if not result.success:
        return None, result
    from room_agent.conversation.policy import minimal_replies

    ack = minimal_replies()
    if ack and result.verified:  # (they asked for just "Alright")
        return f"{ack}.", result
    if cap.reflex_say:  # (built from the verified new state, e.g. "Okay, it's at 55.")
        try:
            line = cap.reflex_say(result)
            if line:
                return line, result
        except Exception:
            pass
    return random.choice(DONE), result
