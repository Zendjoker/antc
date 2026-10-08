"""SpeechDirector: decides HOW a sentence should sound, never WHAT it says. Provider-independent.

    semantic sentence + ResponseStrategy (social/) + SocialState + its place in the reply + language
        -> SpeechPerformance (direction, energy, pace, pauses, emphasis, a rare reaction, seriousness)

The direction is composed from the sentence's own purpose (an apology, a surprised reaction, a question, a warning, a
plain answer...) and the strategy's dimensions (energy, warmth, seriousness, humor, urgency, directness), not looked up
from a mood: a frustrated user's "I found it, the backend's on the wrong port" comes out calm and direct, not "sad".
Most sentences get NO direction at all: plain conversational delivery is the default, and deviations stay small.
Consecutive sentences keep the same direction (one speaker, not a new act per sentence). No model call.
"""

import re
import time
from dataclasses import dataclass, field

APOLOGY = re.compile(r"\b(sorry|my bad|that'?s on me|my mistake|i messed (that |it )?up|i got (that|it) wrong|i was wrong)\b", re.I)
SURPRISE = re.compile(r"^\W*(no way|wait|whoa|woah|wow|really|seriously|oh,? (wow|nice|no)|hold on)\b", re.I)  # (interjections)
GREETING = re.compile(r"^\W*(hey|hi|hello|morning|good (morning|evening|afternoon)|yo)\b", re.I)
FAILURE = re.compile(r"\b(couldn'?t|can'?t|didn'?t work|failed|isn'?t working|not working|no luck|still broken)\b", re.I)
ACK = re.compile(r"^\W*(alright|all right|okay|ok|got it|sure|done|yep|yeah|on it|will do|fair|noted|there you go|right)\W*$", re.I)
INTERJECTION = re.compile(r"^(\W*)(yeah|man|hmm|well|okay|oh|so|alright|right|honestly|ugh)(,)\s+", re.I)
WARNING = re.compile(r"\b(don'?t|do not|never|careful|stop|get out|turn off|call (911|112)|right now|immediately)\b", re.I)
EMPHASIS_WORDS = ("actually", "finally", "really", "never", "always", "totally", "so", "huge", "nailed")
REACTION_GAP_S = 300  # at most one laugh/chuckle/sigh every five minutes, whatever happens
DELIVERY_CONFIDENCE = 0.35  # a reading this sure may shape HOW it sounds (small shifts); changing WHAT is said takes more
_last_reaction = {"at": 0.0}


@dataclass
class SpeechPerformance:
    semantic_text: str
    language: str = "en"
    purpose: str = "answer"     # answer | ack | question | apology | surprise | warning | failure | greeting | explanation
    direction: list = field(default_factory=list)  # natural-language delivery words, e.g. ["calm", "direct"]; [] = plain
    energy: str = "normal"      # low | normal | high
    pace: float = 1.0           # multiplier on their chosen speaking rate (kept within +-6%)
    pauses: str = "natural"     # natural | thoughtful (an interjection may trail off) | brisk (no added pauses)
    emphasis: str = ""          # at most one word to stress
    reaction: str = ""          # rare: "chuckles" / "laughs softly" / "sighs", only when it fits the meaning
    seriousness: str = "low"
    position: int = 0           # sentence number in this reply
    changed: bool = True        # the direction differs from the previous sentence of this reply
    why: list = field(default_factory=list)

    @property
    def direction_text(self):
        return ", ".join(self.direction)


def purpose_of(sentence):
    s = sentence.strip()
    if APOLOGY.search(s):
        return "apology"  # (sympathy, "I'm so sorry to hear that", is told apart in direct(): it depends on the moment)
    if SURPRISE.search(s):
        return "surprise"
    if s.endswith("?"):
        return "question"
    if ACK.match(s):
        return "ack"
    if GREETING.match(s):
        return "greeting"
    if FAILURE.search(s):
        return "failure"
    if len(s.split()) >= 22 or re.search(r"\b(because|which means|so that|the reason)\b", s, re.I):
        return "explanation"
    return "answer"


def direct(sentence, strategy=None, snapshot=None, position=0, previous=None, language="en", now=None):
    """-> SpeechPerformance for one sentence of a reply. `previous`: the SpeechPerformance of the sentence before it."""
    from room_agent.social.strategy import ResponseStrategy

    s = strategy or ResponseStrategy()
    v = (snapshot or {}).get("values", {})
    p = SpeechPerformance(sentence, language=language, purpose=purpose_of(sentence), position=position)
    confident = s.confidence >= DELIVERY_CONFIDENCE
    mode = s.mode
    serious = mode in ("urgent", "emotional") or v.get("serious", 0) >= 0.45
    p.seriousness = "high" if mode == "urgent" or v.get("serious", 0) >= 0.6 else "medium" if serious else "low"
    words = []

    # 1. the situation (strategy), only when it's a real reading
    if mode == "urgent":
        words, p.energy, p.pace, p.pauses = ["clear", "firm"], "normal", 1.05, "brisk"
        p.why.append("urgent: clear and firm, controlled, never dramatic")
    elif mode == "emotional" and confident:  # (a weak guess about their mood never changes how it sounds)
        words = ["quiet", "warm"] if s.response_energy == "low" else ["calm", "warm"]
        p.energy, p.pace, p.pauses = "low", 0.95, "thoughtful"
        p.why.append("something heavy: calm and warm")
    elif mode == "focused" and confident:
        words, p.pauses = ["calm", "direct"], "natural"
        p.why.append("they're frustrated: calm and direct, never irritated back")
    elif s.response_energy == "low" and confident:
        words, p.energy, p.pace, p.pauses = ["quiet", "warm"], "low", 0.96, "thoughtful"
        p.why.append("low energy: quieter and warm, not sad")
    elif mode == "joking" and s.humor_level != "off" and confident:
        words = ["lightly amused"] if s.roast_level == "off" else ["playful", "teasing"]
        p.why.append("joking: light amusement")
    elif s.response_energy == "high" and confident:
        words, p.energy, p.pace = ["upbeat"], "high", 1.03
        p.why.append("good energy: a bit livelier")

    # 2. the sentence itself
    if p.purpose == "apology" and serious and not re.search(r"\b(my bad|on me|my mistake|i messed|i got (that|it) wrong|"
                                                           r"i was wrong)\b", sentence, re.I):
        p.purpose = "sympathy"  # ("I'm so sorry" about their news: no ownership, the moment's own direction stays)
        p.why.append("sympathy, not an apology")
    if p.purpose == "apology":
        words = ["sincere", "calm"] if mode != "urgent" else words
        p.why.append("owning a mistake: sincere, no joke")
    elif p.purpose == "surprise" and not serious:
        words = (["excited", "genuinely surprised"] if p.energy == "high" or s.response_energy == "high"
                 else ["genuinely surprised"])
        p.emphasis = next((w for w in EMPHASIS_WORDS if re.search(rf"\b{w}\b", sentence, re.I)), "") if p.energy == "high" or \
            s.response_energy == "high" else ""
        p.why.append("a surprised reaction")
    elif p.purpose == "failure" and mode != "urgent":
        words = ["calm", "matter-of-fact"] if mode in ("focused", "task") or not words else words
        p.why.append("reporting something that didn't work: calm, no drama")
    elif p.purpose == "warning" or (mode == "urgent" and WARNING.search(sentence)):
        words = ["clear", "firm"]
    elif p.purpose == "question" and mode in ("casual", "joking") and not words:
        words = ["curious"]
    elif p.purpose == "ack" and mode in ("task",) and not confident:
        words = []  # (a plain "Alright." in a plain task needs no acting)

    # 3. a rare human reaction, only where the meaning calls for it
    t = now or time.time()
    if (mode == "joking" and s.humor_level == "full" and p.purpose not in ("apology", "failure", "warning", "question")
            and p.seriousness == "low" and position == 0 and v.get("joking", 0) >= 0.5 and t - _last_reaction["at"] > REACTION_GAP_S):
        p.reaction = "chuckles"
        _last_reaction["at"] = t
        p.why.append("they made a joke: a small chuckle")

    p.direction = words[:3]
    if (p.purpose == "explanation" or len(sentence.split()) > 30) and not serious:
        p.pauses = "natural"  # (long sentences: continuity over acting)
    # one speaker: keep the previous sentence's direction unless this one has a reason to differ
    if previous is not None:
        if not p.direction and previous.direction and p.purpose not in ("apology", "surprise", "warning"):
            p.direction = list(previous.direction)
        p.changed = p.direction != previous.direction
    p.pace = max(0.94, min(1.06, p.pace))
    return p
