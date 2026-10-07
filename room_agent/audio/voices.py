"""The voices the agent can switch between, the one in use right now, and the saved choice."""

import json
import logging

from room_agent import config

log = logging.getLogger("room-agent")

# Voices the agent can switch between by voice ("change your voice", "use a British voice").
# ElevenLabs: the standard voices every plan can use through the API. Piper: free local voices,
# downloaded on first use. Add your own entries here.
ELEVEN_VOICES = {
    "Brian": ("nPczCjzI2devNBz1zQrb", "American man, deep and relaxed"),
    "Adam": ("pNInz6obpgDQGcFmaJgB", "American man, deep"),
    "George": ("JBFqnCBsd6RMkjVDRZzb", "British man, warm"),
    "Daniel": ("onwK4e9ZLuTAKqWW03F9", "British man, crisp, newsreader"),
    "Charlie": ("IKne3meq5aSn9XLyUdCD", "Australian man, casual"),
    "Liam": ("TX3LPaxmHKxFdv7VOQHJ", "American man, young"),
    "Chris": ("iP95p4xoKVk53GoZ742B", "American man, casual"),
    "Eric": ("cjVigY5qzO86Huf0OWal", "American man, smooth"),
    "Will": ("bIHbv24MWmeRgasZH58o", "American man, friendly"),
    "Roger": ("CwhRBWXzGAHq8TQ4Fs17", "American man, confident"),
    "Sarah": ("EXAVITQu4vr4xnSDxMaL", "American woman, soft"),
    "Jessica": ("cgSgspJ2msm6clMCkdW9", "American woman, expressive"),
    "Laura": ("FGY2WhTYpPnrIDTdsKH5", "American woman, upbeat"),
    "Matilda": ("XrExE9yKIg1WjnnlVkGX", "American woman, warm"),
    "Alice": ("Xb7hH8MSUJpSbSDYk0k2", "British woman, clear"),
    "River": ("SAz9YHcvj6GT2YYXdXww", "American, calm, gender-neutral"),
}
PIPER_VOICES = {
    "Ryan": ("en_US-ryan-high", "American man"),
    "Joe": ("en_US-joe-medium", "American man"),
    "Bryce": ("en_US-bryce-medium", "American man"),
    "John": ("en_US-john-medium", "American man"),
    "Norman": ("en_US-norman-medium", "American man, older"),
    "Lessac": ("en_US-lessac-high", "American woman"),
    "Amy": ("en_US-amy-medium", "American woman"),
    "Kristin": ("en_US-kristin-medium", "American woman"),
    "Alan": ("en_GB-alan-medium", "British man"),
    "Northern": ("en_GB-northern_english_male-medium", "Northern English man"),
    "Cori": ("en_GB-cori-high", "British woman"),
    "Alba": ("en_GB-alba-medium", "Scottish woman"),
    "Jenny": ("en_GB-jenny_dioco-medium", "British woman"),
}


def _load_saved():
    try:
        data = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


_saved = _load_saved()


class _Current:
    """The voices in use right now. They can differ from .env: picked by voice, or the Piper fallback."""

    def __init__(self):
        self.eleven = config.EL_VOICE
        self.piper = config.PIPER_VOICE
        self.fallback = False  # set when ElevenLabs runs out of credits: switch to the free local voice
        self.style = str(_saved.get("speaking_style") or "normal")  # how it delivers sentences (audio/styles.py)
        # A voice picked by voice survives restarts, unless you changed the voice in .env since (then .env wins).
        if _saved.get("elevenlabs_voice") and _saved.get("elevenlabs_env", config.EL_VOICE) == config.EL_VOICE:
            self.eleven = _saved["elevenlabs_voice"]
        if _saved.get("piper_voice") and _saved.get("piper_env", config.PIPER_VOICE) == config.PIPER_VOICE:
            self.piper = _saved["piper_voice"]


current = _Current()


def provider():
    """The speech backend in use right now: "elevenlabs" or "piper"."""
    return "piper" if current.fallback else config.TTS_PROVIDER


def fall_back_to_piper(reason):
    """ElevenLabs refused (quota, key): keep talking with the local Piper voice instead of going silent."""
    if not current.fallback:
        log.warning("ElevenLabs unavailable (%s): switching to the free local Piper voice", reason)
        current.fallback = True


def table():
    """The voices that can be chosen with the backend in use."""
    return PIPER_VOICES if provider() == "piper" else ELEVEN_VOICES


def label():
    if provider() == "piper":
        name = next((k for k, (v, _) in PIPER_VOICES.items() if v == current.piper), current.piper)
        note = " (the free local voice, because ElevenLabs is out of credits)" if current.fallback else ""
        return f"{name}{note}"
    return next((k for k, (v, _) in ELEVEN_VOICES.items() if v == current.eleven), "a custom ElevenLabs voice")


def _write_saved():
    try:
        config.SETTINGS_FILE.write_text(json.dumps(_saved, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("couldn't save settings: %s", e)


def save_choice(backend, voice_id):
    """Keep the chosen voice across restarts."""
    if backend == "piper":
        _saved.update(piper_voice=voice_id, piper_env=config.PIPER_VOICE)
    else:
        _saved.update(elevenlabs_voice=voice_id, elevenlabs_env=config.EL_VOICE)
    _write_saved()


def save_style(style):
    """Keep the chosen speaking style across restarts."""
    current.style = style
    save_setting("speaking_style", style)


def saved(key, default=None):
    return _saved.get(key, default)


def save_setting(key, value):
    """Keep any simple setting across restarts (settings.json)."""
    _saved[key] = value
    _write_saved()
