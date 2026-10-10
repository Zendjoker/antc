"""SpeechDirector: decides HOW a sentence should sound, never WHAT it says. Provider-independent.

    semantic sentence + ResponseStrategy (social/) + SocialState + its place in the reply + language
        -> SpeechPerformance (direction, energy, pace, pauses, emphasis, a rare reaction, seriousness)

The direction is composed from the sentence's own purpose (an apology, a surprised reaction, a question, a warning, a
plain answer...) and the strategy's dimensions (energy, warmth, seriousness, humor, urgency, directness), not looked up
from a mood: a frustrated user's "I found it, the backend's on the wrong port" comes out calm and direct, not "sad".
Most sentences get NO direction at all: plain conversational delivery is the default, and deviations stay small.
Consecutive sentences keep the same direction (one speaker, not a new act per sentence). No model call.

What a sentence carries decides how much acting it may get (its `safety`):
    warning    security / danger ("don't share that code", "the door is unlocked"): serious and clear, nothing playful
    confirm    a yes/no before an action ("Want me to send it?"): calm and clear, never playful or excited
    precise    money, codes, emails, phone numbers, addresses, links: plain (calm at most), no added pauses or stress
    normal     everything else
How much it performs overall is a setting (settings(): off | subtle | natural | expressive, emotion on/off, pauses on/off):
    off         plain words, nothing added (only their chosen speed)
    subtle      only what keeps a moment right: calm for serious, firm for urgent, sincere for an apology, clear for a
                warning or a confirmation; no reactions, no added pauses
    natural     + warmth for a greeting, excitement for a real win, curiosity, thoughtfulness, light amusement, a rare
                chuckle (at most one every five minutes)
    expressive  + a livelier default in relaxed talk, reactions a little more often (still never when it's serious)
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
REACTION_GAP_EXPRESSIVE_S = 120
LEVELS = ("off", "steady", "subtle", "natural", "expressive")  # steady: one voice, only their chosen style (speech.steady)
# what a sentence carries (see the docstring)
SECURITY = re.compile(r"\b(password|passcode|pin code|verification code|one[- ]time code|2fa|two[- ]factor|scam|phishing|"
                      r"suspicious (login|sign[- ]in|email|link|message|activity)|fraud\w*|hack(ed|er|ers)|breach\w*|"
                      r"malware|security (alert|warning|risk)|(don'?t|do not|never) (share|give out|send|enter|type|click)\b"
                      r"[^.?!]{0,40}\b(code|password|pin|link|details|card|account|login)|is (still )?unlocked|left unlocked|"
                      r"alarm (is )?(going off|triggered)|smoke (alarm|detector)|carbon monoxide|gas leak)\b", re.I)
# a yes/no before something with consequences (a casual offer, "want me to put some music on?", keeps the moment's tone)
CONFIRM_Q = re.compile(r"\b(want me to|do you want me to|should i|shall i|would you like me to|you want me to|are you sure|"
                       r"okay to|ok to|go ahead and|confirm)\b[^?]*\b(send|delete|remove|erase|wipe|buy|pay|order|purchase|"
                       r"book|call|text|email|post|share|forward|cancel|unlock|transfer|install|uninstall|reset|format|"
                       r"shut ?down|restart|disarm|submit|sign)\b[^?]*\?", re.I)
PRECISE = re.compile(r"[$€£¥]\s?\d|\b\d[\d,.]*\s?(dollars|euros|pounds|dirhams|bucks|usd|eur|gbp|mad)\b|"
                     r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b|https?://|\bwww\.|\b[a-z0-9-]+\.(com|org|net|io|ai|dev|app|co)\b|"
                     r"\b(code|otp|pin|password|account|card|iban|routing|confirmation|order|tracking|reference)( number| no\.?)?"
                     r"( is| was|:)?\s+(?=[\w-]*\d)[\w-]{3,}|\+?\d[\d\s().-]{8,}\d|\b\d{4,}\b(?![,.]\d)|"
                     r"\b\d+\s+\w+\s+(street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr)\b", re.I)
NUMBERS = re.compile(r"\d")
CALM_WORDS = {"calm", "clear", "firm", "quiet", "warm", "sincere", "direct", "matter-of-fact", "serious", "thoughtful"}
PROTECTIVE = {"urgent", "serious", "apology", "sympathy", "failure", "warning", "confirm", "frustrated", "low energy"}
OPENER = re.compile(r"^\W*(okay|ok|alright|all right|hmm+|well|right|so|got it|i see)\W*$", re.I)
TRANSITION = re.compile(r"^\W*(so|also|next|second|third|finally|anyway|now|then|plus|on top of that|the other thing)\b,?", re.I)
UNSURE = re.compile(r"\b(not sure|i think|probably|maybe|might|i'?d guess|depends|hard to say|i don'?t know)\b", re.I)
TECHNICAL = re.compile(r"\b(port|server|config\w*|api|error|exception|function|variable|database|query|script|commit|"
                       r"branch|install\w*|driver|cpu|gpu|ram|latency|endpoint|backend|frontend|python|file|folder|"
                       r"\w+\.(py|js|ts|json|md|txt|yaml|toml|exe))\b", re.I)
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
    intent: str = "plain"       # the delivery intention, for tests and the speech audit (never sent anywhere)
    safety: str = "normal"      # normal | precise | confirm | warning (see the docstring)
    protect: bool = False       # numbers, codes, addresses...: no punctuation or stress changes in this sentence
    pause_before: float = 0.0   # seconds of silence before it (between two thoughts; never before the first sentence)
    level: str = "natural"      # the expressiveness this was decided at

    @property
    def direction_text(self):
        return ", ".join(self.direction)


def purpose_of(sentence):
    s = sentence.strip()
    if APOLOGY.search(s):
        return "apology"  # (sympathy, "I'm so sorry to hear that", is told apart in direct(): it depends on the moment)
    if SURPRISE.search(s):
        return "surprise"
    if GREETING.match(s) and len(s.split()) <= 5:  # ("Hey, what's up?" greets more than it asks)
        return "greeting"
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


def settings():
    """-> (level, emotion, pauses) in effect: what was chosen and kept (settings.json) over .env."""
    from room_agent import config
    from room_agent.audio import voices

    level = str(voices.saved("speech_expressiveness") or config.SPEECH_EXPRESSIVENESS).strip().lower()
    emotion, pauses = voices.saved("speech_emotion"), voices.saved("speech_pauses")
    return (level if level in LEVELS else "natural", config.SPEECH_EMOTION if emotion is None else bool(emotion),
            config.SPEECH_PAUSES if pauses is None else bool(pauses))


def safety_of(sentence, confirming=False):
    if SECURITY.search(sentence):
        return "warning"
    if confirming and sentence.rstrip().endswith("?") or CONFIRM_Q.search(sentence):
        return "confirm"
    if PRECISE.search(sentence):
        return "precise"
    return "normal"


def direct(sentence, strategy=None, snapshot=None, position=0, previous=None, language="en", now=None, level="natural",
           emotion=True, pauses=True, confirming=False, flavor=None):
    """-> SpeechPerformance for one sentence of a reply. `previous`: the SpeechPerformance of the sentence before it.
    `level` / `emotion` / `pauses`: settings(); `confirming`: a yes/no about an action is open; `flavor`: the
    personality's reading of this reply (social/personality.py Flavor: a win, an excuse, something serious)."""
    p = _direct(sentence, strategy, snapshot, position, previous, language, now, level, confirming, flavor)
    p.level = level
    if level == "off" or not emotion:  # (plain words; only the pace hint stays)
        p.direction, p.reaction, p.emphasis = [], "", ""
        p.why.append("expressiveness off" if level == "off" else "emotional delivery off")
        if previous is not None:
            p.changed = p.direction != previous.direction
    if level == "off" or not pauses:
        p.pauses, p.pause_before = "brisk", 0.0
    return p


def _direct(sentence, strategy, snapshot, position, previous, language, now, level, confirming, flavor):
    from room_agent.social.strategy import ResponseStrategy

    s = strategy or ResponseStrategy()
    v = (snapshot or {}).get("values", {})
    p = SpeechPerformance(sentence, language=language, purpose=purpose_of(sentence), position=position)
    p.safety = safety_of(sentence, confirming)
    p.protect = p.safety != "normal" or bool(NUMBERS.search(sentence))
    confident = s.confidence >= DELIVERY_CONFIDENCE
    mode = s.mode
    serious = (mode in ("urgent", "emotional") or v.get("serious", 0) >= 0.45
               or bool(flavor is not None and getattr(flavor, "serious", False)))
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

    elif not words and level in ("natural", "expressive") and not serious and mode != "focused":
        # what the moment gives this sentence when nothing above did (natural and up)
        if flavor is not None and getattr(flavor, "celebrate", False) and p.purpose not in ("question", "failure"):
            words, p.energy, p.intent = ["excited"], "high", "excitement"
            p.why.append("a real win: excited")
        elif flavor is not None and getattr(flavor, "motivation", "") == "challenge":
            words, p.intent = ["confident", "direct"], "challenge"
            p.why.append("calling out an excuse: confident and direct, not harsh")
        elif flavor is not None and getattr(flavor, "motivation", "") == "push":
            words, p.intent = ["confident", "upbeat"], "push"
            p.why.append("a push: confident")
        elif p.purpose == "greeting" and position == 0:
            words, p.intent = ["warm"], "greeting"
            p.why.append("a greeting: warm")
        elif position == 0 and re.match(r"^\W*(hmm+|well)\b", sentence, re.I) and UNSURE.search(sentence):
            words, p.pauses, p.intent = ["thoughtful"], "thoughtful", "hesitation"
            p.why.append("thinking it over: thoughtful, a natural hesitation")
        elif level == "expressive" and mode in ("casual", "joking") and p.purpose in ("answer", "explanation"):
            words, p.intent = ["engaged"], "casual"
            p.why.append("relaxed talk (expressive): engaged")

    # what the sentence carries limits all of the above
    if p.safety == "warning" and mode != "urgent":
        words, p.intent = ["serious", "clear"], "warning"
        p.why.append("a security or safety warning: serious and clear")
    elif p.safety == "confirm" and mode != "urgent":
        words, p.intent = ["calm", "clear"], "confirmation"
        p.why.append("a yes/no before an action: calm and clear, nothing playful")
    elif p.safety == "precise":
        kept = [w for w in words if w in CALM_WORDS]
        if kept != words:
            p.why.append("money / codes / addresses: plain, no performance")
        words, p.intent = kept, ("precise" if not kept else p.intent)
    if level == "subtle" and not (serious or p.purpose in ("apology", "sympathy", "failure", "warning")
                                  or p.safety in ("warning", "confirm") or mode in ("focused", "urgent")
                                  or (s.response_energy == "low" and confident)):
        if words:
            p.why.append("subtle: only what keeps the moment right")
        words = []
    if p.intent == "plain" and words:
        p.intent = {"urgent": "urgent", "emotional": "empathy", "focused": "calm"}.get(mode, p.purpose)
    if p.intent == "plain" and TECHNICAL.search(sentence) and p.purpose in ("answer", "explanation"):
        p.intent = "calm_technical"

    # 3. a rare human reaction, only where the meaning calls for it
    t = now or time.time()
    gap = REACTION_GAP_EXPRESSIVE_S if level == "expressive" else REACTION_GAP_S
    if (level in ("natural", "expressive") and mode == "joking" and s.humor_level == "full"
            and p.purpose not in ("apology", "failure", "warning", "question") and p.safety == "normal"
            and p.seriousness == "low" and position == 0 and v.get("joking", 0) >= 0.5 and t - _last_reaction["at"] > gap):
        p.reaction = "laughs softly" if level == "expressive" and v.get("joking", 0) >= 0.7 else "chuckles"
        _last_reaction["at"] = t
        p.why.append(f"they made a joke: {p.reaction}")

    p.direction = words[:3]
    if (p.purpose == "explanation" or len(sentence.split()) > 30) and not serious:
        p.pauses = "natural"  # (long sentences: continuity over acting)
    # one speaker: keep the previous sentence's direction unless this one has a reason to differ
    if previous is not None:
        if (not p.direction and previous.direction and p.purpose not in ("apology", "surprise", "warning")
                and (p.safety == "normal" or set(previous.direction) <= CALM_WORDS)
                and previous.intent not in ("greeting", "hesitation", "confirmation", "warning")):
            p.direction = list(previous.direction)
        p.changed = p.direction != previous.direction
        # a breath between thoughts: after a standalone "Okay." / "Hmm.", or before a new point in a longer answer
        if p.safety == "normal" and mode != "urgent" and level in ("natural", "expressive"):
            if position == 1 and OPENER.match(previous.semantic_text):
                p.pause_before = 0.15
            elif position >= 2 and TRANSITION.match(sentence):
                p.pause_before = 0.2
    if p.protect:
        p.emphasis = ""  # (no stress games in a sentence with numbers, codes or an address)
        p.pace = min(p.pace, 1.0)
        if p.pauses == "thoughtful":
            p.pauses = "natural"
    if p.pauses == "brisk":
        p.pause_before = 0.0
    p.pace = max(0.94, min(1.06, p.pace))
    return p
