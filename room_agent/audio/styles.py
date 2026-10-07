"""Delivery styles: how a sentence is performed (soft, warm, excited...). Needs an ElevenLabs v3 or v4 model."""

import re

from room_agent import config
from room_agent.audio import voices

# style name -> the audio tag ElevenLabs understands ("" = plain delivery)
STYLE_TAGS = {
    "normal": "",
    "soft": "[softly, gently]",
    "whisper": "[whispers]",
    "warm": "[warmly, friendly]",
    "engaged": "[engaged, interested, lively]",
    "excited": "[excited]",
    "serious": "[serious, calm tone]",
    "playful": "[playful, teasing]",
    "laughs": "[laughs]",
    "chuckles": "[chuckles]",
    "sighs": "[sighs]",
}
BASE_STYLES = ("normal", "soft", "whisper", "warm", "engaged", "excited", "serious", "playful")  # can be made permanent
_LEADING_TAG = re.compile(r"^\s*\[([A-Za-z ]{2,20})\]\s*")
_ANY_STYLE_TAG = re.compile(r"\[(?:%s)\]\s*" % "|".join(STYLE_TAGS), re.I)


class Spoken(str):
    """A sentence queued for speaking, with the style it should be delivered in."""

    style = ""


def supported():
    return config.EL_MODEL.startswith(("eleven_v3", "eleven_v4")) and voices.provider() != "piper"


def split_style(text):
    """'[soft] Hey there.' -> ('Hey there.', 'soft'). Leading tags are never spoken; unknown ones are just dropped."""
    style = ""
    for _ in range(2):
        m = _LEADING_TAG.match(text)
        if not m:
            break
        name = m.group(1).strip().lower()
        style = name if name in STYLE_TAGS else style
        text = text[m.end():]
    return text, style


def strip_tags(text):
    """Remove style tags from words that are being saved or shown."""
    return _ANY_STYLE_TAG.sub("", text)


def delivery_text(item):
    """What actually goes to ElevenLabs: the sentence, led by its style tag when this model understands tags."""
    text = str(item)
    if not supported():
        return text
    style = getattr(item, "style", "") or voices.current.style
    tag = STYLE_TAGS.get(style, "")
    return f"{tag} {text}" if tag else text
