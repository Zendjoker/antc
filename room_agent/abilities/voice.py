"""How Jarvis sounds and listens: voice, speaking pace and style, listening patience (implementation: tools/voice.py)."""

import re

from room_agent import runtime as rt
from room_agent.abilities._kit import NO_ARGS, params, tool, tts_on
from room_agent.actions.core import Group, register_claim, register_group, register_line
from room_agent.audio import styles, voices
from room_agent.audio.styles import BASE_STYLES
from room_agent.config import LISTEN_PATIENCE, SPEECH_RATES


def _detail():
    if not rt.tts_enabled:
        return "no voice output in this mode"
    if styles.supported():
        return (f"{len(voices.table())} voices (list_voices names them); using {voices.label()}; style {voices.current.style}; "
                f"pace x{rt.speech_rate}; listening patience x{rt.patience}")
    return (f"{len(voices.table())} voices (list_voices names them); using {voices.label()}; listening patience x{rt.patience}; "
            "can't change its delivery with this voice model")


register_group(Group("voice", re.compile(r"voice|speak|talk|slow|fast|pace|soft|loud|whisper|serious|tone|sound|patien|wait|"
                                         r"interrupt|cut me|rush|accent|calm|excit|normal", re.I)))
register_line("changing its voice, speaking pace and listening patience", _detail, available=tts_on)
register_claim("voice", r"\b(switched|changed|swapped)\b.{0,30}\bvoices?\b|\bnew voice\b|\bthis is .{0,15}\bvoice now\b")
register_claim("style", r"\b(talking|speaking|going|getting|sounding)\s+(more\s+)?(softly|softer|quieter|gentler|serious(ly)?|warmer)\b"
                        r"|\bi'?ll (talk|speak|sound|be)\s+(more\s+)?(softly|softer|quieter|gentler|serious(ly)?|warmer)\b")


def _call(fn_name, key=None):
    def run(args):
        from room_agent.tools import voice

        fn = getattr(voice, fn_name)
        return fn(args[key]) if key else fn()
    return run


tool("set_listening_patience", "Wait longer after they pause before treating them as finished, and keep it for good. Only for "
     "a standing preference: 'always wait longer before answering', 'you keep cutting me off'; 'normal' goes back to the "
     "default. Not for a single 'let me finish' (the system handles that). This does not make you quiet.",
     params({"level": {"type": "string", "enum": list(LISTEN_PATIENCE)}}, ["level"]),
     _call("set_listening_patience", "level"), group="voice")
tool("set_speaking_rate", "Change how fast you talk and keep it: 'talk slower', 'much slower', 'speed up', or 'normal'.",
     params({"rate": {"type": "string", "enum": list(SPEECH_RATES)}}, ["rate"]), _call("set_speaking_rate", "rate"),
     group="voice", available=lambda: tts_on() and voices.provider() != "piper")
tool("set_speaking_style", "Change how you sound from now on and keep it: softer, whispering, warmer, more engaged, more "
     "excited, more serious, playful, or back to normal. Use when they say things like 'talk softer', 'be more serious', "
     "'more energy' or 'talk normal again'.",
     params({"style": {"type": "string", "enum": list(BASE_STYLES)}}, ["style"]), _call("set_speaking_style", "style"),
     group="voice", claim="style", available=lambda: tts_on() and styles.supported())
tool("list_voices", "List the voices you can switch to right now, and which one you're using.", NO_ARGS,
     _call("list_voices"), group="voice", changes_state=False, available=tts_on)
tool("set_voice", "Change your speaking voice. Give a voice name from list_voices, or a description like 'British man' or "
     "'woman'. The new voice is used from your next sentence on and kept.",
     params({"name": {"type": "string"}}, ["name"]), _call("set_voice", "name"), group="voice", claim="voice",
     available=tts_on)
