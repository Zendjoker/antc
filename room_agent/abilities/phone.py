"""Phone mode (implementation: room_agent/phone/): what Jarvis knows about you being on the road or on a call."""

from room_agent import config
from room_agent.actions.core import register_context, register_line


def _ready():
    from room_agent.phone.server import ready

    return config.PHONE_MODE and not ready()


def _detail():
    if not config.PHONE_MODE:
        return "not set up (phone.md)"
    from room_agent.phone.server import ready

    missing = ready()
    if missing:
        return "not finished setting up: needs " + ", ".join(missing) + " (python main.py --setup-phone)"
    vip = f"; email from {', '.join(config.VIP_SENDERS)} counts as important" if config.VIP_SENDERS else ""
    return ("they can call Jarvis's number, and while they're driving Jarvis calls them, only for something important "
            f"(an important email, a meeting soon or moved, an alarm going off){vip}")


register_line("phone calls with them", _detail, available=_ready)


def _context(user_text):
    from room_agent.phone import state

    lines = []
    if state.in_call:
        lines.append("- channel: you're on a phone call with them (they may be driving). Talk naturally and briefly; they "
                     "can't see anything, so never refer to screens or windows.")
    if state.is_driving():
        lines.append(f"- right now they're {state.describe()}: keep it short and spoken, never ask them to look at "
                     "anything, only bring up what matters.")
    return lines


register_context(_context, order=12)


# ---------------------------------------------------------------- ending a call (offered only while on one)
import re  # noqa: E402

from room_agent import runtime as rt  # noqa: E402
from room_agent.abilities._kit import NO_ARGS, tool  # noqa: E402
from room_agent.actions.core import Group, register_group  # noqa: E402


def _on_call():
    from room_agent.phone import state

    return bool(state.in_call)


register_group(Group("call", re.compile(r"hang up|bye|goodbye|end (the|this) call|that'?s all|gotta go|talk (to you )?later|"
                                        r"see ya|later", re.I), _on_call, available=_on_call))


def _end_call(args):
    rt.control["end_call"] = True  # (the call session hangs up right after this reply is spoken)
    return "OK: the call ends right after your goodbye. Say a short, warm goodbye."


tool("end_call", "Hang up this phone call when they're done or ask you to hang up ('bye', 'you can hang up'). Say a quick "
     "goodbye in the same reply.", NO_ARGS, _end_call, group="call")
