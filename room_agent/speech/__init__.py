"""Spoken delivery: what Jarvis decided to say (SEMANTIC text) -> how it's performed (PERFORMANCE script) -> audio.

    reason -> semantic sentence -> SocialState / ResponseStrategy -> SpeechDirector (director.py, provider-independent)
           -> provider renderer (elevenlabs.py: tags, punctuation, validation, request; local voices: plain text)
           -> speech-only normalization (normalize.py) + pronunciations (pronounce.py) -> TTS -> audio

Nothing here can change the words, tools, memory, permissions or facts: the semantic sentence is what's stored and
shown everywhere; the performance script exists only inside the request to the voice engine. timing.py measures it all.
"""

import logging

from room_agent import config
from room_agent import runtime as rt
from room_agent.speech import director, elevenlabs, language, normalize, pronounce

log = logging.getLogger("room-agent")
TAG = elevenlabs.TAG


def perform(sentence):
    """The SpeechPerformance for the next sentence of the current reply (called when it's queued)."""
    from room_agent import social
    from room_agent.audio import styles, voices

    previous = getattr(rt.turn, "last_performance", None)
    try:
        snapshot = social.state.snapshot() if social.state.turns else None
        p = director.direct(sentence, getattr(rt.turn, "strategy", None), snapshot, position=rt.spoken_count,
                            previous=previous, language=language.detect(sentence))
    except Exception as e:  # (delivery is decoration: a failure here must never cost the sentence)
        log.warning("speech director skipped a sentence: %s", e)
        p = director.SpeechPerformance(sentence)
    # an explicit style: the model's own [tag] for this reply wins, then the director, then their saved style
    chosen = rt.turn_style or ("" if p.direction else voices.current.style)
    if chosen and chosen != "normal" and styles.STYLE_TAGS.get(chosen) and p.purpose not in ("apology", "warning"):
        p.direction = [w.strip() for w in styles.STYLE_TAGS[chosen].strip("[]").split(",")][:3]
        p.why.append(f"style '{chosen}'")
        p.changed = previous is None or previous.direction != p.direction
    rt.turn.last_performance = p
    return p


def _outside_tags(text, fn):
    """Apply `fn` to the spoken words only, never inside [tags]."""
    parts = TAG.split(text)
    tags = TAG.findall(text)
    out = [fn(parts[0])]
    for i, tag in enumerate(tags):
        out += [f"[{tag}]", fn(parts[2 * i + 2]) if 2 * i + 2 < len(parts) else ""]
    return "".join(out)


def provider_text(item, provider, model):
    """What the voice engine actually receives for this spoken item (and why, for SPEECH_DEBUG)."""
    p = getattr(item, "performance", None) or director.SpeechPerformance(str(item))
    if provider == "piper":
        text = pronounce.apply(normalize.speech_text(str(item), "full"))
        problems = []
    else:
        script, problems = elevenlabs.script_for(p, model, repeat_direction=True)  # (one request per sentence)
        text = _outside_tags(script, lambda s: pronounce.apply(normalize.speech_text(s, "light")))
    if config.SPEECH_DEBUG:
        strategy = getattr(rt.turn, "strategy", None)
        log.info("SPEECH\n    SEMANTIC: %r\n    STRATEGY: %s\n    PERFORMANCE: %r\n    MODEL: %s%s", str(item),
                 f"mode={strategy.mode} energy={strategy.response_energy} humor={strategy.humor_level}" if strategy else "-",
                 text, model if provider != "piper" else "piper", f"\n    REJECTED: {problems}" if problems else "")
    return text, p
