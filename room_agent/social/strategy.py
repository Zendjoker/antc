"""ResponseStrategy: a few decisions about the NEXT reply, made in code before the model writes it.

    SocialState (now)  +  UserModel preferences (long term)  +  recent habits  ->  ResponseStrategy
        -> one short line in the runtime context (the model writes the actual words; nothing is canned)
        -> VoiceDelivery (delivery.py): how the words are spoken

Rules that matter most: don't mirror anger (a frustrated user gets calm, short, useful), don't fake cheer for someone
low, humor and teasing drop when things are serious or urgent, and a dismissal gets a tiny reply or nothing.
"""

import datetime
import re
from dataclasses import dataclass, field


@dataclass
class ResponseStrategy:
    mode: str = "task"                 # task | casual | emotional | joking | focused | urgent
    tone: str = "relaxed"
    response_energy: str = "normal"    # low | normal | high
    response_verbosity: str = "normal" # minimal | short | normal | detailed
    humor_level: str = "light"         # off | light | full
    roast_level: str = "off"           # off | light
    supportiveness: str = "medium"     # low | medium | high
    directness: str = "normal"         # normal | high
    questioning: str = "if_needed"     # none | if_needed | gentle | open
    pace: str = "normal"               # slower | normal | faster
    max_sentences: int = 0             # 0 = no cap; 1 = a tiny reply (enforced in code)
    allow_silence: bool = False        # a reply of nothing at all is fine (e.g. "whatever")
    notes: list = field(default_factory=list)   # extra guidance (habits, situation)
    confidence: float = 0.0
    why: list = field(default_factory=list)     # (for traces and tests, never shown to the model)

    def is_default(self):
        return self == ResponseStrategy(confidence=self.confidence, why=self.why)

    def render(self):
        """One compact line for the model (only when it says something)."""
        if self.is_default():
            return ""
        length = {"minimal": "a few words at most (like \"Alright.\")" + (", or nothing at all" if self.allow_silence else ""),
                  "short": "one or two short sentences", "normal": "as long as it needs, still conversational",
                  "detailed": "fuller detail is welcome"}[self.response_verbosity]
        humor = {"off": "no jokes", "light": "light humor only if it fits", "full": "humor welcome, play along"}[self.humor_level]
        parts = [f"mode {self.mode}", f"tone {self.tone}", f"energy {self.response_energy}", f"length: {length}", humor,
                 "teasing ok, lightly" if self.roast_level == "light" else "no teasing or roasting",
                 {"low": "skip comforting words", "medium": "a little warmth, no therapy talk",
                  "high": "warm and steady, no rushing to fix"}[self.supportiveness]]
        if self.directness == "high":
            parts.append("be direct: the useful thing first, no preamble")
        parts.append({"none": "don't ask a question", "if_needed": "ask only if you really need an answer",
                      "gentle": "at most one gentle, optional question", "open": "a question back is welcome"}[self.questioning])
        return ("- how to answer now (decided by code from their words, tone and what just happened; follow it, never "
                "mention it, never name or guess their feelings out loud): " + "; ".join(parts + self.notes) + ".")


NEGATIVE_PREF = re.compile(r"\b(no|don'?t|never|stop|less|without|not)\b", re.I)


def long_term(text, mode, now=None):
    """Stable preferences from the UserModel (learning/): verbosity, humor, roasting. SocialState never writes these."""
    out = {"verbosity": None, "humor": None, "roast": None}
    try:
        from room_agent import learning
        from room_agent.learning import model as um

        m = learning.user_model()
        d = m.resolve("response_style")
        if d:
            out["verbosity"] = str(d["value"])
        hour = (now or datetime.datetime.now()).hour
        for p in m.all():
            kind, subject = um.split_key(p["key"])
            if kind != "humor":
                continue
            applies = (subject in ("", "always") or (subject == "morning" and hour < 12)
                       or (subject in ("working", "work", "focused") and mode in ("task", "focused"))
                       or (subject and re.search(rf"\b{re.escape(subject)}\b", text or "", re.I)))
            if not applies:
                continue
            value = str(p["value"]).lower()
            negative = bool(NEGATIVE_PREF.search(value))
            if "roast" in value or "teas" in value:
                out["roast"] = "off" if negative else "on"
            if negative and ("joke" in value or "humor" in value or "funny" in value or "roast" not in value):
                out["humor"] = "off"
            elif not negative and ("joke" in value or "humor" in value or "funny" in value):
                out["humor"] = "on"
    except Exception:
        pass
    return out


def derive(snap, text="", prefs=None, habits=None, situation=None):
    """SocialState snapshot (+ preferences, recent habits, situation) -> ResponseStrategy."""
    v, mode = snap["values"], snap["interaction_mode"]
    prefs = prefs or {}
    habits = habits or {}
    situation = situation or {}
    s = ResponseStrategy(mode=mode, confidence=snap["confidence"])
    if prefs.get("verbosity") in ("short", "normal", "detailed"):
        s.response_verbosity = prefs["verbosity"]
        s.why.append(f"prefers {prefs['verbosity']} answers")
    humor_ok = prefs.get("humor") != "off"
    roast_ok = prefs.get("roast") == "on"

    if mode == "urgent":
        s.tone, s.response_energy, s.response_verbosity = "clear and direct", "normal", "short"
        s.humor_level, s.directness, s.supportiveness, s.pace = "off", "high", "low", "faster"
        s.why.append("urgent")
    elif mode == "emotional":
        s.tone, s.response_energy, s.humor_level, s.supportiveness = "calm and warm", "low", "off", "high"
        s.questioning, s.pace = "gentle", "slower"
        s.notes.append("don't be cheerful, don't rush to solutions")
        s.why.append("serious / heavy")
    elif mode == "focused":
        s.tone, s.directness, s.humor_level, s.supportiveness = "calm, matter-of-fact", "high", "off", "low"
        s.response_verbosity = "short"
        s.notes.append("don't match their irritation; just fix it or say plainly what's possible")
        if v["annoyed"] >= 0.35:
            s.notes.append("if you got something wrong, own it in a few words, no excuses, no long apology")
        s.why.append("frustrated")
    elif mode == "joking":
        s.tone, s.humor_level = "playful", "full" if humor_ok else "light"
        s.roast_level = "light" if roast_ok else "off"
        s.response_verbosity = "short" if s.response_verbosity == "normal" else s.response_verbosity
        s.questioning = "open"
        s.why.append("joking")
    elif mode == "casual":
        s.tone, s.questioning = "relaxed", "open" if v["talk"] >= 0.35 else "if_needed"
        if v["talk"] >= 0.35:
            s.why.append("wants to talk")
    # energy and mood on top of the mode (never mirrored blindly)
    if v["low"] >= 0.35 and mode not in ("urgent",):
        s.tone = "calm and gentle" if mode != "emotional" else s.tone
        s.response_energy, s.pace = "low", "slower"
        s.humor_level = "off" if v["low"] >= 0.5 else min(s.humor_level, "light", key=["off", "light", "full"].index)
        s.roast_level = "off"
        s.supportiveness = "medium" if s.supportiveness == "low" else s.supportiveness
        s.questioning = "gentle" if s.questioning in ("open", "if_needed") else s.questioning
        if s.response_verbosity == "normal" and v["talk"] < 0.35:
            s.response_verbosity = "short"
        s.notes.append("lower energy, no forced cheerfulness")
        s.why.append("low energy")
    elif (v["excited"] >= 0.35 or v["energy_up"] >= 0.45) and mode in ("casual", "joking", "task"):
        s.response_energy, s.pace = "high", "faster" if v["excited"] >= 0.5 else "normal"
        s.tone = "upbeat" if mode != "joking" else s.tone
        s.why.append("excited")
    if v["serious"] >= 0.45 or mode == "urgent":
        s.humor_level, s.roast_level = "off", "off"
    if not humor_ok:
        s.humor_level, s.roast_level = "off", "off"
    if v["brief"] >= 0.4 and s.response_verbosity in ("normal", "detailed"):
        s.response_verbosity = "short"
        s.why.append("wants it short")
    if (v["dismiss"] >= 0.5 or v["stop"] >= 0.5) and mode not in ("urgent", "emotional"):  # (never cut short what matters)
        s.response_verbosity, s.questioning, s.max_sentences, s.allow_silence = "minimal", "none", 1, True
        s.humor_level, s.roast_level, s.supportiveness = "off", "off", "low"
        s.why.append("wants a tiny reply")
    # recent habits of Jarvis's own (whatever the mood)
    if habits.get("questions_in_a_row", 0) >= 2 and s.questioning != "none":
        s.questioning = "none"
        s.why.append("ended the last replies with questions")
    if habits.get("name_recently"):
        s.notes.append("don't use their name")
    if situation.get("driving"):
        s.response_verbosity = "minimal" if s.response_verbosity == "minimal" else "short"
        s.humor_level = "off" if s.humor_level == "off" else "light"
        s.directness = "high"
    return s
