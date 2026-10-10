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
                                         r"pronounc|say (?:it|\w+) like|interrupt|cut me|rush|accent|calm|excit|normal", re.I)))
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
     group="voice", available=tts_on)
def _style(args):
    from room_agent.tools import voice

    return voice.set_speaking_style(args["style"], args.get("also"))


tool("set_speaking_style", "Change how you sound from now on and keep it: softer, whispering, warmer, more engaged, more "
     "excited, more serious, playful, or back to normal. Use when they say things like 'talk softer', 'be more serious', "
     "'more energy' or 'talk normal again'. A qualified request keeps both parts: 'serious but a little friendly' = "
     "style serious, also warm. 'previous' = the style before the last change.",
     params({"style": {"type": "string", "enum": list(BASE_STYLES) + ["previous"]},
             "also": {"type": "string", "enum": [s for s in BASE_STYLES if s != "normal"],
                      "description": "a second, lighter quality they asked for, if any"}}, ["style"]), _style,
     group="voice", claim="style", available=tts_on,
     reflex=[(r"(?:talk|speak|sound|be)\s+(?:like\s+)?(?P<style>normal)(?:ly)?(?:\s+again)?", {}),
             (r"(?:talk|speak)\s+(?:like\s+)?(?:a\s+)?(?:regular|normal)(?:\s+person)?", {"style": "normal"}),
             (r"(?:go\s+)?back\s+to\s+(?:your\s+)?(?P<style>normal)(?:\s+voice)?", {})],
     reflex_say=lambda result: "Okay, normal voice." if result.success else None)
tool("list_voices", "List the voices you can switch to right now, and which one you're using.", NO_ARGS,
     _call("list_voices"), group="voice", changes_state=False, available=tts_on)
tool("set_voice", "Change your speaking voice. Give a voice name from list_voices, or a description like 'British man' or "
     "'woman'. The new voice is used from your next sentence on and kept.",
     params({"name": {"type": "string"}}, ["name"]), _call("set_voice", "name"), group="voice", claim="voice",
     available=tts_on)


def _pron(fn_name):
    def run(args):
        from room_agent.speech import pronounce

        return getattr(pronounce, fn_name)(args)
    return run


tool("set_pronunciation", "Remember how to SAY a word (a name, project, brand): 'say AimChart like aim chart', 'it's "
     "pronounced Shiv-awn'. Only changes how it's spoken; the spelling stays the same everywhere.",
     params({"term": {"type": "string", "description": "The word as it's written, e.g. 'AimChart'"},
             "say_as": {"type": "string", "description": "How it should sound, in plain letters, e.g. 'aim chart'"}},
            ["term", "say_as"]), _pron("set_pronunciation"), group="voice", available=tts_on)
tool("forget_pronunciation", "Go back to saying a word the normal way.", params({"term": {"type": "string"}}, ["term"]),
     _pron("forget_pronunciation"), group="voice", available=tts_on)


def _set_name(args):
    from room_agent.tools import voice

    return voice.set_assistant_name(args["name"])


def _set_wake(args):
    from room_agent.tools import voice

    return voice.set_wake_word(args["model"])


tool("set_assistant_name", "Change what you call yourself (used in your own system prompt) when they ask you to go by "
     "a different name. A real, lasting change, not a one-reply bit. Takes effect from your next reply; kept across "
     "restarts.", params({"name": {"type": "string", "description": "The new name, e.g. 'Friday'"}}, ["name"]),
     _set_name, group="voice", claim="name_change")
tool("set_wake_word", "Change the word that wakes you up, only if they explicitly ask for this - and only among "
     "ready-made wake-word models (never an arbitrary phrase: a genuinely new one needs training a model first, which "
     "this can't do by voice). Saved for the next restart, not immediate.",
     params({"model": {"type": "string", "description": "One of the ready-made wake-word models, e.g. 'alexa', "
                                                         "'hey_mycroft', 'hey_rhasspy'"}}, ["model"]),
     _set_wake, group="voice")
register_claim("name_change", r"\bi'?m (?:now )?(?:called|named)\b|\bgoing by\b.{0,20}\bnow\b|\byou can call me\b.{0,20}"
                              r"\bnow\b|\bcall me\b.{0,15}\bfrom now on\b", verified_by=["set_assistant_name"])
