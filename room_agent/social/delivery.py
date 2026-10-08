"""VoiceDelivery: how a reply is spoken, between the words the model wrote and the voice engine. It never changes the
words: a style tag is added only in the text sent to a voice model that understands tags (ElevenLabs v3/v4), and is
never spoken, saved or shown.

Mapped only to what the current voice integration really supports:
    ElevenLabs   audio tag (audio/styles.py STYLE_TAGS: soft, serious, excited...) for v3/v4 models + voice_settings.speed
    Piper        SynthesisConfig.length_scale (pace) and volume (a small energy difference)
Kept subtle on purpose: pace within +-6%, volume within +-8%. No cartoon voices.
"""

from dataclasses import dataclass

PACE = {"slower": 0.95, "normal": 1.0, "faster": 1.05}


@dataclass
class VoiceDelivery:
    style: str = ""        # a key of audio/styles.STYLE_TAGS, or "" (the saved / default delivery)
    pace: float = 1.0      # multiplier on the speaking rate they chose
    energy: str = "normal"  # low | normal | high
    emphasis: str = "natural"  # natural | clear (urgent: crisp, no playful delivery)

    @property
    def volume(self):
        return {"low": 0.94, "normal": 1.0, "high": 1.05}[self.energy]


def from_strategy(s, confident=True):
    """ResponseStrategy -> VoiceDelivery. Below a confident reading only the pace hint is used (no style change)."""
    d = VoiceDelivery(pace=PACE.get(s.pace, 1.0), energy=s.response_energy)
    if not confident:
        return d
    if s.mode == "urgent":
        d.style, d.emphasis = "", "clear"         # clear, direct, a little quicker; no colouring
    elif s.mode == "emotional":
        d.style = "serious" if s.supportiveness != "high" or s.response_energy != "low" else "soft"
    elif s.response_energy == "low":
        d.style = "soft"
    elif s.mode == "joking" and s.humor_level != "off":
        d.style = "playful"
    elif s.response_energy == "high":
        d.style = "excited" if s.pace == "faster" else "engaged"
    elif s.mode == "focused":
        d.style = ""                               # plain and steady (not "serious": that sounds grave)
    return d


def elevenlabs_speed(base_rate, delivery):
    """ElevenLabs voice_settings.speed (allowed 0.7-1.2) from their chosen rate and this reply's pace."""
    return round(max(0.7, min(1.2, base_rate * (delivery.pace if delivery else 1.0))), 3)


def piper_config(delivery):
    """Piper SynthesisConfig arguments for this delivery (None: Piper's defaults)."""
    if not delivery or (delivery.pace == 1.0 and delivery.energy == "normal"):
        return None
    return {"length_scale": round(1.0 / delivery.pace, 3), "volume": delivery.volume}
