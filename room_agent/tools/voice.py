"""Switching the speaking voice by voice command."""

import logging
import re

import requests

from room_agent import config, runtime as rt
from room_agent.audio import styles, tts, voices
from room_agent.config import EL_KEY, EL_MODEL, LISTEN_PATIENCE, OUT_SR, SILENCE_S, SPEECH_RATES

log = logging.getLogger("room-agent")


def set_listening_patience(level):
    """How long a pause it waits through before treating you as finished. Not the same as being quiet."""
    rt.patience = LISTEN_PATIENCE[level]
    voices.save_setting("patience", rt.patience)
    return (f"OK: patience is now '{level}': it waits {SILENCE_S * rt.patience:.1f} seconds of silence before treating "
            "them as finished. Kept across restarts. It is still listening and answering normally.")


def set_speaking_rate(rate):
    if not rt.tts_enabled:
        return "UNAVAILABLE: there's no voice output in this mode."
    rt.speech_rate = SPEECH_RATES[rate]
    voices.save_setting("speech_rate", rt.speech_rate)
    return f"OK: speaking pace is now '{rate}'. It's used from the next sentence on and kept."


def set_speaking_style(style, also=None):
    """Make the way it talks (softer, more serious, more engaged...) the default from now on. `also`: a second, lighter
    quality they asked for ("serious, but a little friendly" -> serious + warm). "previous" goes back to the style
    before the last change."""
    wanted = style.lower().strip()
    if not rt.tts_enabled:
        return "UNAVAILABLE: there's no voice output in this mode."
    if wanted == "previous":
        wanted = str(voices._saved.get("previous_speaking_style") or "normal")
    if wanted == "normal":  # ("talk normal": always possible, right away, and kept)
        _remember_previous("style", voices.current.style, "normal")
        voices.save_style("normal")
        rt.turn_style = ""  # (the rest of this reply is plain too)
        return "OK: back to my normal voice, from now on."
    if not styles.supported():
        return ("UNAVAILABLE: the current voice model can't change how it sounds. That needs ELEVENLABS_MODEL=eleven_v4_turbo "
                "(or eleven_v3_conversational) in .env.")
    parts = [x for x in wanted.split("+") if x]
    second = str(also or "").lower().strip()
    if second and second not in ("normal", parts[0]) and len(parts) == 1:
        parts.append(second)
    bad = [x for x in parts if x not in styles.BASE_STYLES]
    if bad or not parts:
        return f"FAILED: no style called '{bad[0] if bad else style}'. Styles: {', '.join(styles.BASE_STYLES)}."
    target = "+".join(parts[:2])
    _remember_previous("style", voices.current.style, target)
    voices.save_style(target)
    said = " with a little ".join(parts[:2])
    return f"OK: speaking style is now '{said}'. It's used from the next sentence on and kept."


def _remember_previous(kind, before, after):
    """The configuration before a change, so "I don't like it" can go straight back to it (handle_complaint)."""
    if before == after:
        return
    voices.save_setting(f"previous_{'speaking_style' if kind == 'style' else 'voice'}", before)
    rt.voice_change = (rt.turn_no, kind, before)


# They don't like how it sounds or talks: "it sounds so weird", "why are you talking to me like you're seducing me",
# "I don't like that voice". About Jarvis's own voice/manner (not "that sounds bad" about their news, which only counts
# right after a change).
_NEG = (r"(?:weird|strange|off|bad|awful|terrible|creepy|robotic|wrong|gay|seduc\w*|flirt\w*|annoying|fake|cringe|whiny|"
        r"sad|depress\w*|soft|too \w+)")
_ABOUT_YOU = re.compile(r"\b(?:you(?:'re| are)?|your (?:voice|tone))\s+(?:\w+\s+){0,3}?(?:sound|talk|speak)\w*\b[^.?!]{0,40}?\b"
                        + _NEG + r"\b|\b(?:don'?t|do not) like (?:that|this|the|your) (?:new )?(?:voice|tone|style)\b", re.I)
_SOUNDS = re.compile(r"\b(?:it|that|this|the voice|the tone)\s+(?:\w+\s+){0,2}?sounds?\s+(?:so\s+|really\s+|kind of\s+|kinda\s+)?"
                     + _NEG + r"\b", re.I)
RECENT_CHANGE_TURNS = 3


def handle_complaint(text):
    """In code, before the model answers: they dislike how it sounds. A style or voice changed in the last few turns
    goes back to the verified previous one; otherwise the plain normal style. -> a note for the model, or ""."""
    change = getattr(rt, "voice_change", None)
    recent = bool(change and rt.turn_no - change[0] <= RECENT_CHANGE_TURNS)
    if not (_ABOUT_YOU.search(text or "") or (recent and _SOUNDS.search(text or ""))):
        return ""
    done = ""
    if recent and change[1] == "style" and rt.tts_enabled:
        voices.save_setting("previous_speaking_style", voices.current.style)
        voices.save_style(change[2])
        done = f"your speaking style is back to how it was before ('{change[2]}')"
    elif recent and change[1] == "voice" and rt.tts_enabled:
        name = next((k for k, (vid, _) in voices.table().items() if vid == change[2]), None)
        out = set_voice(name) if name else "FAILED: unknown voice"
        done = (f"your voice is back to {name}, as before" if out.startswith("OK") else
                f"switching back to the previous voice didn't work ({out[:80]})")
    elif voices.current.style != "normal" and rt.tts_enabled:
        voices.save_style("normal")
        done = "your speaking style is back to plain normal"
    rt.voice_change = None
    log.info("they don't like how it sounds: %s", done or "already the plain normal voice")
    return (" (System note from code: they don't like how you sound or talk. "
            + (f"Already done: {done}. Say that in a few words. " if done else
               "Your voice is already the plain normal one (nothing in it changes with mood). ")
            + "Then answer what they said plainly and briefly, no flowery or soothing wording. Don't ask how it sounds or "
              "what tone they want.)")


def list_voices():
    if not rt.tts_enabled:
        return "UNAVAILABLE: there's no voice output in this mode."
    options = "; ".join(f"{k} ({d})" for k, (_, d) in voices.table().items())
    return f"OK: using {voices.label()}. Available: {options}."


def set_voice(wanted):
    """Switch the speaking voice for real (and remember the choice)."""
    provider = voices.provider()
    table = voices.table()
    w = wanted.lower().strip()
    pick = next((k for k in table if k.lower() == w), None)
    if not pick:  # match on a description: "british", "woman", "deep"...
        words = [x for x in re.findall(r"[a-z]+", w) if x not in ("a", "an", "the", "voice", "one", "with", "accent")]
        words = ["woman" if x in ("female", "girl", "lady") else "man" if x in ("male", "guy", "dude") else x for x in words]
        current = voices.current.eleven if provider != "piper" else voices.current.piper
        hits = [k for k, (vid, d) in table.items() if words and all(re.search(rf"\b{x}", d.lower()) for x in words)]
        pick = next((k for k in hits if table[k][0] != current), hits[0] if hits else None)
    if not rt.tts_enabled:
        return "UNAVAILABLE: there's no voice output in this mode."
    if not pick:
        return f"FAILED: no voice matches '{wanted}'. " + list_voices()
    voice_id = table[pick][0]
    if provider == "piper":
        try:  # load and test the new voice on the side; the current one keeps working meanwhile
            voice = tts.open_piper(voice_id)
            if not next(iter(voice.synthesize("ok")), None):
                raise RuntimeError("it produced no audio")
        except Exception as e:
            return f"FAILED: couldn't switch to {pick} ({e}). Still using {voices.label()}."
        tts.install_piper_voice(voice_id, voice)
    else:
        try:  # one tiny request with the new voice proves it works on this account
            r = tts.el.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=pcm_{OUT_SR}",
                            headers={"xi-api-key": EL_KEY}, json={"text": "ok", "model_id": EL_MODEL}, timeout=15)
            r.raise_for_status()
        except requests.RequestException as e:
            return f"FAILED: ElevenLabs wouldn't use {pick} ({e}). Still using {voices.label()}."
        voices.current.eleven = voice_id
    _remember_previous("voice", voices.current.eleven if provider != "piper" else voices.current.piper, voice_id)
    voices.save_choice(provider, voice_id)
    tts.clear_clips()
    # the lines needed first get re-recorded now; the rest when they're first used
    tts.record_in_background([p for kind in ("wake", "ack", "wait", "quiet") for p in rt.phrases.pools[kind]])
    log.info("voice changed to %s", pick)
    return f"OK: switched to {pick} ({table[pick][1]}); verified working. Your next sentence uses it."


def set_assistant_name(name):
    """Change what it calls itself (used in its own system prompt). Takes effect from the next reply; kept across
    restarts. A real, lasting change - not a one-reply bit."""
    cleaned = " ".join(str(name or "").split())[:40].strip(" .!?\"'")
    if not cleaned:
        return "NEEDS: what name?"
    rt.assistant_name = cleaned
    voices.save_setting("assistant_name", cleaned)
    return f"OK: going by {cleaned} from now on."


def wake_word_options():
    """The ready-made openWakeWord models actually installed - never a guessed or invented list."""
    try:
        import openwakeword

        return sorted(openwakeword.MODELS.keys())
    except Exception:
        return []


def set_wake_word(model_name):
    """Change which word wakes it up - only among openWakeWord's own ready-made models. A brand new custom wake word
    needs training a model first (a separate, offline process); this never invents or assumes one exists. Saved for
    the NEXT start, not immediate: the detector is built once, at startup (conversation/loops.py)."""
    wanted = re.sub(r"[\s-]+", "_", str(model_name or "").strip().lower())
    options = wake_word_options()
    if not options:
        return "UNAVAILABLE: couldn't check which wake words are installed right now."
    if wanted not in options:
        return (f"FAILED: '{model_name}' isn't one of the ready-made wake words ({', '.join(options)}). A brand new "
                "custom word needs training a new model first, which isn't something done by voice.")
    rt.wake_word = wanted
    voices.save_setting("wake_word_env", config.WAKE_WORD)  # (a later WAKE_WORD change in .env wins over this)
    voices.save_setting("wake_word", wanted)
    return f"OK: saved '{wanted}' as the new wake word. It takes effect the next time Jarvis starts, not right now."
