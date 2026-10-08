"""How names should be SAID, kept by the user ("say AimChart like aim chart"). Spoken-only: the spelling in the
conversation, memory and UI never changes. Stored in settings.json ("pronunciations": {spelling: how to say it}).

Applied as whole-word aliases right before speech, so it works with every voice (Piper, every ElevenLabs model, the
Text to Dialogue WebSocket). ElevenLabs pronunciation dictionaries (alias / phoneme rules, up to 3 per request) are the
next step once an API key is configured: the same table can be uploaded as alias rules.

Only real needs go in (names, projects, brands, jargon a voice gets wrong); not ordinary words.
"""

import re

from room_agent.audio import voices


def table():
    saved = voices.saved("pronunciations", {}) or {}
    return {str(k): str(v) for k, v in saved.items() if str(k).strip() and str(v).strip()}


def apply(text, entries=None):
    entries = table() if entries is None else entries
    for spelling in sorted(entries, key=len, reverse=True):
        text = re.sub(rf"(?<![\w-]){re.escape(spelling)}(?![\w-])", entries[spelling], text, flags=re.I)
    return text


def set_pronunciation(args):
    term, said = str(args.get("term") or "").strip(), str(args.get("say_as") or "").strip()
    if not term or not said:
        return "NEEDS: the word and how to say it."
    if len(term) > 60 or len(said) > 80 or "[" in said or "]" in said:
        return "FAILED: that's too long or has brackets; give a short word and how it sounds."
    entries = table()
    entries[term] = said
    voices.save_setting("pronunciations", entries)
    return f"OK: from now on '{term}' is said like '{said}' (the spelling stays '{term}')."


def forget_pronunciation(args):
    term = str(args.get("term") or "").strip()
    entries = table()
    hit = next((k for k in entries if k.lower() == term.lower()), None)
    if not hit:
        return f"OK: nothing special was set for '{term}'."
    entries.pop(hit)
    voices.save_setting("pronunciations", entries)
    return f"OK: '{hit}' is said the normal way again."
