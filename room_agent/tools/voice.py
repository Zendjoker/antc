"""Switching the speaking voice by voice command."""

import logging
import re

import requests

from room_agent import runtime as rt
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
    if voices.provider() == "piper":
        return "UNAVAILABLE: the free local voice can't change its speed (ElevenLabs can)."
    rt.speech_rate = SPEECH_RATES[rate]
    voices.save_setting("speech_rate", rt.speech_rate)
    return f"OK: speaking pace is now '{rate}'. It's used from the next sentence on and kept."


def set_speaking_style(style):
    """Make the way it talks (softer, more serious, more engaged...) the default from now on."""
    wanted = style.lower().strip()
    if not rt.tts_enabled:
        return "UNAVAILABLE: there's no voice output in this mode."
    if not styles.supported():
        return ("UNAVAILABLE: the current voice model can't change how it sounds. That needs ELEVENLABS_MODEL=eleven_v4_turbo "
                "(or eleven_v3_conversational) in .env.")
    if wanted not in styles.BASE_STYLES:
        return f"FAILED: no style called '{style}'. Styles: {', '.join(styles.BASE_STYLES)}."
    voices.save_style(wanted)
    return f"OK: speaking style is now '{wanted}'. It's used from the next sentence on and kept."


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
    voices.save_choice(provider, voice_id)
    tts.clear_clips()
    # the lines needed first get re-recorded now; the rest when they're first used
    tts.record_in_background([p for kind in ("wake", "ack", "wait", "quiet") for p in rt.phrases.pools[kind]])
    log.info("voice changed to %s", pick)
    return f"OK: switched to {pick} ({table[pick][1]}); verified working. Your next sentence uses it."
