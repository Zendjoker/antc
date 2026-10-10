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
# documented ElevenLabs v3/v4 tags a model might copy into its words: delivery only, never content
DELIVERY_TAGS = ("whispers", "laughs harder", "starts laughing", "laughs softly", "giggles", "exhales", "gasps", "clears throat",
                 "short pause", "long pause", "pause", "curious", "sarcastic", "mischievously", "crying", "thoughtful",
                 "shouts", "softly", "warmly")
_LEADING_TAG = re.compile(r"^\s*\[([A-Za-z ]{2,20})\]\s*")
_ANY_STYLE_TAG = re.compile(r"\[(?:%s)\]\s*" % "|".join(sorted(set(STYLE_TAGS) | set(DELIVERY_TAGS), key=len, reverse=True)),
                            re.I)


class Spoken(str):
    """A sentence queued for speaking, with the style it should be delivered in."""

    style = ""
    delivery = None  # social/delivery.py VoiceDelivery for this reply (pace, energy), or None


def supported():
    return config.EL_MODEL.startswith(("eleven_v3", "eleven_v4")) and voices.provider() != "piper"


def hints_allowed():
    """The model may suggest a delivery tag: a tag model, and expressive speech not turned off."""
    from room_agent.speech import director

    level, emotion, _ = director.settings()
    return supported() and level != "off" and emotion


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


def clean_args(value):
    """A tool call's arguments without delivery tags (an email body, a text, a note never carries "[excited]"), changed
    in place so the call that runs and the call kept in the history are the same. -> the number of tags taken out."""
    removed = 0
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, str):
                new = strip_tags(v)
                if new != v:
                    removed += 1
                    value[k] = new
            else:
                removed += clean_args(v)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            if isinstance(v, str):
                new = strip_tags(v)
                if new != v:
                    removed += 1
                    value[i] = new
            else:
                removed += clean_args(v)
    return removed


def semantic_content(content):
    """A model reply as stored in the history: text without delivery tags (tool calls untouched). Speech-performance
    instructions exist only on the way to the voice, never in what's stored or shown."""
    if isinstance(content, str):
        return strip_tags(content)
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "text":
            out.append({**b, "text": strip_tags(b.get("text", ""))})
        elif getattr(b, "type", "") == "text" and not isinstance(b, dict):
            out.append({"type": "text", "text": strip_tags(b.text)})
        else:
            out.append(b)
    return out


def delivery_text(item):
    """What actually goes to ElevenLabs: the sentence, led by its style tag when this model understands tags."""
    text = str(item)
    if not supported():
        return text
    style = getattr(item, "style", "") or voices.current.style
    tag = STYLE_TAGS.get(str(style).split("+")[0], "")  # ("serious+warm": its main style)
    return f"{tag} {text}" if tag else text
