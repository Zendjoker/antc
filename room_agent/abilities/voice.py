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


PERSONALITY_WORDS = (r"swear|curs(?:e|ing)|cuss|profan|slang|nickname|personality|call(?:ing)? me|tone it|intens|hype|"
                     r"motivat|pep talk|old self|original self")
# their own words must ask for a personality change (never an email's or a web page's say-so: actions/executor.py)
PERSONALITY_ASK = re.compile(PERSONALITY_WORDS + r"|dial it|energy|street|friend|default|the way you talk|how you talk", re.I)
register_group(Group("voice", re.compile(r"voice|speak|talk|slow|fast|pace|soft|loud|whisper|serious|tone|sound|patien|wait|"
                                         r"pronounc|say (?:it|\w+) like|interrupt|cut me|rush|accent|calm|excit|normal|"
                                         + PERSONALITY_WORDS, re.I)))
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
tool("set_speaking_style", "Change how you sound from now on and keep it: softer, whispering, warmer, more engaged, more "
     "excited, more serious, playful, or back to normal. Use when they say things like 'talk softer', 'be more serious', "
     "'more energy' or 'talk normal again'.",
     params({"style": {"type": "string", "enum": list(BASE_STYLES)}}, ["style"]), _call("set_speaking_style", "style"),
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


def _personality_state(args=None):
    from dataclasses import asdict

    from room_agent.social import personality

    return {"saved": personality.snapshot(), "profile": asdict(personality.profile())}


def _personality_changes(args):
    if args.get("reset"):
        return {"reset": True}
    out = {k: args[k] for k in ("preset", "intensity", "slang", "profanity") if args.get(k)}
    if args.get("stop_calling"):
        out["never_call"] = args["stop_calling"]
    if args.get("nicknames"):
        out["address"] = args["nicknames"]
    if isinstance(args.get("motivation"), bool):
        out["motivation"] = args["motivation"]
    return out


def _set_personality(args):
    from room_agent.social import personality

    changes = _personality_changes(args)
    if not changes:
        return ("FAILED: nothing to change. Say what: the style (street or friend), intensity, slang, swearing, a nickname "
                "to stop using, or motivation on/off.")
    p, said = personality.update(**changes)
    terms = p.terms()
    return (f"OK: {said}; kept for good. Now: {p.preset} style, intensity {p.intensity}, slang {p.slang}, swearing "
            f"{p.profanity}, nicknames {', '.join(terms) or 'none'}, motivation {'on' if p.motivation else 'off'}.")


def _personality_applied(args, before, after):
    """Read back: the profile now in effect says what they asked for."""
    p = after["profile"]
    changes = _personality_changes(args)
    if changes.get("reset"):
        return not after["saved"]
    for k in ("preset", "intensity", "slang", "profanity", "motivation"):
        if k in changes and p.get(k) != changes[k]:
            return False
    used = [t.lower() for t in p["address"] + p["casual_address"] if t.lower() not in (n.lower() for n in p["never_call"])]
    stop = [t.strip().lower() for t in re.split(r"[,;]| and ", str(changes.get("never_call") or "")) if t.strip()]
    if any(t in used for t in stop):
        return False
    want = [t.strip().lower() for t in re.split(r"[,;]| and ", str(changes.get("address") or "")) if t.strip()]
    return not want or bool(p["address"]) and p["address"][0].lower() in want


def _undo_personality(args, before, after):
    from room_agent.social import personality

    personality.restore(before["saved"])
    return "OK: personality back to how it was."


tool("set_personality", "Change how you come across and keep it: the style ('street': confident big-brother energy; "
     "'friend': the original easygoing tone), intensity, slang, swearing, nicknames you use for them, or motivation (pushes, "
     "calling out excuses). Use for 'stop swearing', 'tone it down', 'less slang', 'stop calling me bro', 'go back to your "
     "old personality'. What to call them by name ('call me Mike') is memory's address_as, not this. How the VOICE sounds "
     "(softer, more excited) is set_speaking_style.",
     params({"preset": {"type": "string", "enum": ["street", "friend"]},
             "intensity": {"type": "string", "enum": ["low", "medium", "high"]},
             "slang": {"type": "string", "enum": ["off", "light", "full"]},
             "profanity": {"type": "string", "enum": ["off", "mild"]},
             "stop_calling": {"type": "string", "description": "A nickname to stop using, e.g. 'bro'"},
             "nicknames": {"type": "string", "description": "Nicknames to use instead, main one first, e.g. 'boss'"},
             "motivation": {"type": "boolean"},
             "reset": {"type": "boolean", "description": "Back to the default personality"}}, []),
     _set_personality, group="voice", observe=_personality_state, verify=_personality_applied, undo=_undo_personality,
     undo_if=lambda before, after: before["saved"] != after["saved"], intent=PERSONALITY_ASK,
     reflex=[(r"(?:stop|quit|no more|don'?t|no)\s+(?:swearing|cursing|cussing)(?:\s+(?:anymore|around me))?",
              {"profanity": "off"}),
             (r"(?:you can|it'?s (?:ok|okay|fine) to)\s+(?:swear|curse|cuss)(?:\s+(?:again|around me))?", {"profanity": "mild"}),
             (r"(?:stop|quit|don'?t)\s+call(?:ing)?\s+me\s+(?P<stop_calling>bro|brother|boss|bruh|homie|chief|king|man|dude)"
              r"(?:\s+anymore)?", {}),
             (r"(?:tone it down|less intense|less hype|dial it (?:back|down))(?:\s+a (?:bit|little|notch))?", {"intensity": "low"}),
             (r"(?:no|less|stop (?:using|with) the)\s+slang", {"slang": "off"}),
             (r"(?:go\s+)?back\s+to\s+(?:your\s+)?(?:old|original)\s+(?:personality|self)", {"preset": "friend"}),
             (r"(?:go\s+)?back\s+to\s+(?:your\s+)?(?:default|street)\s+personality", {"reset": True})],
     reflex_say=lambda result: "Got it." if result.success else None)


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
