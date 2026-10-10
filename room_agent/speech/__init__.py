"""Spoken delivery: what Jarvis decided to say (SEMANTIC text) -> how it's performed (PERFORMANCE script) -> audio.

    reason -> semantic sentence -> SocialState / ResponseStrategy -> SpeechDirector (director.py, provider-independent)
           -> provider renderer (elevenlabs.py: tags, punctuation, validation, request; local voices: plain text)
           -> speech-only normalization (normalize.py) + pronunciations (pronounce.py) -> TTS -> audio

Nothing here can change the words, tools, memory, permissions or facts: the semantic sentence is what's stored and
shown everywhere; the performance script exists only inside the request to the voice engine. timing.py measures it all.
"""

import collections
import dataclasses
import logging
import re
import time

from room_agent import config
from room_agent import runtime as rt
from room_agent.speech import director, elevenlabs, language, normalize, pronounce

log = logging.getLogger("room-agent")
TAG = elevenlabs.TAG


REACTION_STYLES = ("laughs", "chuckles", "sighs")  # (a reaction: once, on the first sentence, never a whole reply's tone)
CALM_STYLES = ("soft", "serious", "warm")             # (the only chosen styles a warning / confirmation / exact detail keeps)


def _flavor():
    try:
        from room_agent.social import personality

        return None if personality.profile().preset == "friend" else personality.flavor()
    except Exception:
        return None


def perform(sentence):
    """The SpeechPerformance for the next sentence of the current reply (called when it's queued)."""
    from room_agent import social
    from room_agent.audio import styles, voices

    previous = getattr(rt.turn, "last_performance", None)
    level, emotion, pauses = director.settings()
    try:
        snapshot = social.state.snapshot() if social.state.turns else None
        flavor = _flavor()
        confirming = bool(getattr(rt.pending, "confirm", False) or getattr(flavor, "confirming", False))
        position = max(0, rt.spoken_count - 1)  # (say() has already counted this sentence)
        p = director.direct(sentence, getattr(rt.turn, "strategy", None), snapshot, position=position,
                            previous=previous, language=language.detect(sentence), level=level, emotion=emotion,
                            pauses=pauses, confirming=confirming, flavor=flavor)
    except Exception as e:  # (delivery is decoration: a failure here must never cost the sentence)
        log.warning("speech director skipped a sentence: %s", e)
        p = director.SpeechPerformance(sentence)
    if level == "steady":
        # one steady voice (Oct 9 live test): only the style they chose, never a delivery guessed from their mood
        # or a tag the model picked; a clear, firm warning is kept (steady())
        p = steady(p, voices.current.style, previous)
    else:
        # an explicit style: the model's own [tag] for this reply wins, then the director, then their saved style; never
        # against what the sentence carries (a warning, a yes/no, an exact detail stay calm) and never past the settings
        chosen = rt.turn_style or ("" if p.direction else voices.current.style)
        allowed = level in ("natural", "expressive") or (level == "subtle" and chosen in CALM_STYLES)
        if (chosen and chosen != "normal" and styles.STYLE_TAGS.get(chosen) and emotion and allowed
                and p.purpose not in ("apology", "warning") and (p.safety == "normal" or chosen in CALM_STYLES)
                and not (chosen in REACTION_STYLES and (p.seriousness != "low" or p.safety != "normal"))):
            if chosen in REACTION_STYLES:
                if p.position == 0 and not p.reaction:
                    p.reaction = {"laughs": "laughs softly"}.get(chosen, chosen)
                    p.why.append(f"their reply opened with [{chosen}]")
            else:
                p.direction = [w.strip() for w in styles.STYLE_TAGS[chosen].strip("[]").split(",")][:3]
                p.why.append(f"style '{chosen}'")
                p.changed = previous is None or previous.direction != p.direction
    rt.turn.last_performance = p
    return p


def style_words(style):
    """'serious' / 'serious+warm' (a qualified request: "serious but a little friendly") -> delivery words, at most 3."""
    from room_agent.audio import styles

    parts = [s for s in str(style or "").split("+") if styles.STYLE_TAGS.get(s)]
    words = [[w.strip() for w in styles.STYLE_TAGS[s].strip("[]").split(",") if w.strip()] for s in parts]
    if len(words) > 1:
        return (words[0][:2] + words[1][:1])[:3]
    return words[0][:3] if words else []


def steady(p, saved, previous=None):
    """One consistent voice: no delivery guessed from their mood (pace, energy, warmth, softness) and no tag the model
    picked for itself; only the style they chose. "normal" is the plain baseline. Kept: a clear, firm warning."""
    urgent = p.direction[:2] == ["clear", "firm"]
    p.pause_before = 0.0  # (no added gap between thoughts either)
    p.direction, p.energy, p.pace, p.reaction, p.emphasis = (["clear", "firm"] if urgent else []), "normal", 1.0, "", ""
    if not urgent:
        p.pauses = "natural"
        words = style_words(saved)
        if words:
            p.direction = words
            p.why.append(f"their style '{saved}'")
    p.changed = previous is None or previous.direction != p.direction
    return p


MARKUP = re.compile(r"\[[^\[\]]{1,60}\]|<break time=\"\d(?:\.\d)?s\" />")
_CUE = re.compile(r"^[a-z][a-z ,'-]{0,40}$")
audit = collections.deque(maxlen=200)  # (model, transport, intent, safety, tags, timings, fallbacks: never the words)


def _outside_tags(text, fn):
    """Apply `fn` to the spoken words only, never inside [tags] or <break> markup."""
    def keep_spacing(seg):  # ("[warm] Hey" stays "[warm] Hey", not "[warm]Hey")
        done = fn(seg)
        return (" " if seg[:1].isspace() and done else "") + done + (" " if seg[-1:].isspace() and done else "")

    out, last = [], 0
    for m in MARKUP.finditer(text):
        out += [keep_spacing(text[last:m.start()]), m.group(0)]
        last = m.end()
    out.append(keep_spacing(text[last:]))
    return "".join(out).strip()


def clean_for_speech(text):
    """The words to perform, without any bracket the voice would act on or read out: delivery cues the model wrote
    ("[short pause]", "[laughs]"), and brackets from anywhere else ("[Adam]" keeps its word). Speech only: the semantic
    text shown and stored is never changed here."""
    def one(m):
        inner = m.group(1).strip()
        if re.fullmatch(r"\d+(?:\s*[,–-]\s*\d+)*", inner):
            return " "  # (a citation marker)
        words = re.findall(r"[a-z-]+", inner.lower())
        if _CUE.match(inner) and len(inner.split()) <= 4 or (words and all(w in elevenlabs.ALLOWED_WORDS for w in words)):
            return " "
        return inner
    t = re.sub(r"\[([^\[\]]{1,60})\]", one, str(text))
    t = re.sub(r"</?[a-zA-Z][^<>]{0,60}>", " ", t)  # (no markup of any kind from the text itself: "<break ...>", "<b>")
    return re.sub(r"\s+([,.!?;:])", r"\1", re.sub(r"\s{2,}", " ", t)).strip()


def provider_text(item, provider, model):
    """What the voice engine actually receives for this spoken item (and why, for SPEECH_DEBUG)."""
    p = getattr(item, "performance", None) or director.SpeechPerformance(str(item))
    words = clean_for_speech(str(item))
    if provider == "piper":
        text = pronounce.apply(normalize.speech_text(words, "full"))
        problems = []
    else:
        if words != p.semantic_text:
            p = dataclasses.replace(p, semantic_text=words)
        level = elevenlabs.caps(model)["normalize"]
        script, problems = elevenlabs.script_for(p, model, repeat_direction=True)  # (one request per sentence)
        text = _outside_tags(script, lambda s: pronounce.apply(
            normalize.speech_text(s, "full", acronyms=()) if level == "numbers" else normalize.speech_text(s, "light")))
    if config.SPEECH_DEBUG:
        strategy = getattr(rt.turn, "strategy", None)
        log.info("SPEECH\n    SEMANTIC: %r\n    STRATEGY: %s\n    PERFORMANCE: %r\n    MODEL: %s%s\n    INTENT: %s, safety %s, "
                 "level %s%s", str(item),
                 f"mode={strategy.mode} energy={strategy.response_energy} humor={strategy.humor_level}" if strategy else "-",
                 text, model if provider != "piper" else "piper", f"\n    REJECTED: {problems}" if problems else "",
                 p.intent, p.safety, p.level, f", pause {p.pause_before:.2f}s before" if p.pause_before else "")
    return text, p


def note(**fields):
    """One spoken sentence in the speech audit (no text: what was decided and how it went)."""
    audit.append({"at": round(time.time(), 3), **fields})
