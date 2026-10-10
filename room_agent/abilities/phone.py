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
    from room_agent.phone.texts import vips

    v = vips()
    vip = f"; email from {', '.join(v)} counts as important" if v else ""
    return ("they can call Jarvis's number, and while they're driving Jarvis calls them by itself, only for something "
            f"important (an important email, a meeting soon or moved, an alarm going off, the door opening){vip}. On "
            "request you can call them (call_me) or text their phone (text_me)")


register_line("phone calls with them", _detail, available=_ready)
register_line("texting or calling other people, or setting alarms on their phone",
              "not built: only their own phone can be texted or called; alarms and timers ring here in the room",
              available=lambda: False)


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


# ---------------------------------------------------------------- on request: text / call their phone, VIPs
import re as _re  # noqa: E402

from room_agent.abilities._kit import NO_ARGS, params, tool  # noqa: E402
from room_agent.actions.core import register_claim  # noqa: E402

PHONE_HINTS = _re.compile(r"text me|send (it|that|this|the link)? ?to my phone|my phone|sms|call me|ring me|\bvip|"
                          r"always call me|call me if|emails? from", _re.I)
register_group(Group("phone_requests", PHONE_HINTS, lambda: False, "texting or calling their own phone",
                     "texts or a call to their own phone on request",
                     lambda: bool(config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_NUMBER
                                  and config.MY_PHONE)))  # (only offered once the phone line is set up)
register_claim("phone_text", r"\b(texted|sent) (it|that|you|the link|a text)\b.{0,30}\b(phone|text)\b|\bi'?m calling you\b|"
                             r"\bcalling your phone\b")


def _texts():
    from room_agent.phone import texts

    return texts


def _call_me(args):
    from room_agent.phone.server import place_call, ready
    from room_agent.phone import state

    if not config.PHONE_MODE or ready():
        return "UNAVAILABLE: the phone line isn't set up (phone.md, python main.py --setup-phone)."
    if state.in_call:
        return "FAILED: you're already on a call with Jarvis."
    place_call("asked", "Hey, it's Jarvis. You asked me to call. What's up?")
    return "OK: calling their phone now (it rings in a few seconds)."


tool("text_me", "Text THEIR OWN phone (never anyone else): a note, or 'send that link to my phone' with attach='page' "
     "(the open browser tab) or 'source 2' (from the last research). A few cents per text; daily limit.",
     params({"message": {"type": "string"}, "attach": {"type": "string", "description": "'page' or 'source N'; "
                                                                                       "leave out for none"}}),
     lambda a: _texts().text_me(a.get("message", ""), a.get("attach", "")), group="phone_requests", claim="phone_text",
     intent=_re.compile(r"text|send|sms|message|my phone", _re.I),
     examples=["text me the wifi password reminder", "send that link to my phone"])
tool("call_me", "Call their phone now (they asked Jarvis to ring them).", NO_ARGS, _call_me, group="phone_requests",
     describe=lambda a: "call your phone now",
     claim="phone_text", intent=_re.compile(r"\b(?:call|ring|phone|buzz) me\b|\bgive me a (?:phone )?(?:call|ring)\b|"
                                            r"\b(?:call|ring) my (?:phone|cell|mobile)\b|\bphone call\b", _re.I))
register_group(Group("vips", _re.compile(r"\bvip|always call me|call me (if|when)|emails? from|who do you call", _re.I),
                     lambda: False, "who's worth a call while driving (VIPs)", "add / remove / list, by voice",
                     lambda: True))
tool("add_vip", "Someone whose emails are worth a call while they drive ('always call me if Sarah emails').",
     params({"who": {"type": "string", "description": "Their name or email address"}}, ["who"]),
     lambda a: _texts().add_vip(a["who"]), group="vips",
     intent=_re.compile(r"call me|vip|important|always|emails?", _re.I))
tool("remove_vip", "Stop calling them about someone's emails.", params({"who": {"type": "string"}}, ["who"]),
     lambda a: _texts().remove_vip(a["who"]), group="vips")
tool("list_vips", "Who they get called about (VIPs).", NO_ARGS, lambda a: _texts().list_vips(), group="vips",
     changes_state=False)
