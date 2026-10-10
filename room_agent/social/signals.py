"""Evidence about how the conversation is going right now, from four sources. Each piece of evidence is weak on its
own; SocialState combines them. Nothing here is a diagnosis: these are interaction signals ("sounds tired", "is
joking"), never conclusions about someone's inner state.

    language      cue words and phrasing in what they just said (supporting; meaning.py reads what it MEANS)
    conversation  what just happened (Jarvis failed twice, then "bro..." means something it wouldn't otherwise)
    behavior      repeats, corrections, interruptions, very short replies, asking for shorter/longer answers
    audio         speaking rate, loudness, pauses, pitch movement vs THEIR usual (supporting evidence only)

Dimensions (0..1 strength each): frustration, annoyed (at Jarvis), low, positive, excited, energy_up, energy_down,
joking, serious, urgent, brief (wants a quick answer), talk (wants to talk), dismiss (wants a tiny reply), stop.
Topics, for the personality only (never mood): motivate (asks for a push), excuse (putting it off), win (did something).
"""

import re
from dataclasses import dataclass


@dataclass
class Evidence:
    dim: str
    weight: float
    source: str        # language | conversation | behavior | audio | situation
    at: float
    why: str = ""
    clears: tuple = ()  # dimensions whose OLDER evidence this overrides ("haha" makes an earlier insult playful)
    seq: int = 0        # which update it arrived in (set by SocialState: "older" never depends on clock resolution)


@dataclass
class Cue:
    rx: re.Pattern
    effects: dict                      # dim -> weight
    clears: tuple = ()
    why: str = ""
    needs_context: bool = False        # only counts (fully) after recent failures ("bro...")


def _c(pattern, effects, clears=(), why="", needs_context=False):
    return Cue(re.compile(pattern, re.I), effects, tuple(clears), why or pattern[:30], needs_context)


PROFANITY = re.compile(r"\b(fuck\w*|shit\w*|damn\w*|hell|crap|bloody|goddamn\w*)\b", re.I)

LANGUAGE = [
    # trouble with something not working (the thing, not Jarvis)
    _c(r"\b(still|again|keeps?|won'?t|doesn'?t|isn'?t|not|never) (\w+ )?(work(ing|s)?|load(ing)?|respond(ing)?|open(ing)?|play(ing)?|"
       r"connect(ing)?|start(ing)?)\b|\bbroken\b|\bwhat the (hell|fuck)\b|\bwtf\b|\bffs\b|\bugh+\b|\bfor (god'?s|christ'?s) sake\b|"
       r"\bare you kidding( me)?\b|\bthis is (ridiculous|annoying|a joke)\b",
       {"frustration": 0.45}, clears=("positive", "excited"), why="something isn't working"),
    _c(r"^\W*(come on|seriously|bro|dude|man|really|jesus|omg|oh my god)\W*(\.\.\.|…|!|\?)?\W*$",
       {"frustration": 0.15}, why="exasperated one-liner", needs_context=True),
    # annoyed with Jarvis itself
    _c(r"\byou('?re| are)? (so |really |completely )?(useless|stupid|dumb|an idiot|terrible|trash|garbage|broken|the worst)\b|"
       r"\byou never (listen|get (it|anything) right)\b|\bare you (deaf|serious)\b|\byou'?re not listening\b|"
       r"\bthat'?s not what i (said|asked|meant)\b|\bi (just |literally )?(said|told you)\b|\blisten to me\b",
       {"annoyed": 0.5, "frustration": 0.2}, why="annoyed with Jarvis"),
    # low energy / low mood (words they used about themselves)
    _c(r"\b(tired|exhausted|exhausting|drained|wiped( out)?|knackered|sleepy|beat|long day|rough day|hard day|burn(ed|t) out|"
       r"can'?t be bothered|no energy|meh|bummed|down today|feeling down|lonely|sad|miserable)\b",
       {"low": 0.5, "energy_down": 0.5}, clears=("excited", "energy_up"), why="sounds low/tired"),
    # positive / excited
    _c(r"\b(let'?s go+|finally|yay+|woo+|hell yeah|awesome|amazing|incredible|nailed it|so (happy|excited|hyped|pumped)|"
       r"can'?t wait|i (passed|won|did it|made it)|we (won|did it)|best day)\b|\byes{2,}\b|\byes!",
       {"positive": 0.45, "excited": 0.4, "energy_up": 0.35}, clears=("low", "frustration", "annoyed", "energy_down"),
       why="good news / excitement"),
    _c(r"\b(nice|great|perfect|cool|thanks|thank you|love it|that works|works now|fixed)\b",
       {"positive": 0.25}, clears=("frustration", "annoyed"), why="pleased"),
    # joking
    _c(r"\b(lol|lmao|lmfao|rofl|haha\w*|hehe\w*|jk|just kidding|i'?m kidding|kidding)\b|[\U0001F602\U0001F923\U0001F61C]",
       {"joking": 0.6, "positive": 0.15}, clears=("annoyed", "frustration"), why="joking"),
    # serious / heavy
    _c(r"\b(this is (serious|important)|i need to talk|can we talk|seriously,? though|passed away|died|funeral|hospital|cancer|"
       r"diagnos\w+|broke up|break ?up|divorce|lost my (job|mom|dad|mother|father|brother|sister|friend|dog|cat)|got fired|"
       r"scared|worried|anxious|panick?ing|depressed|grieving)\b",
       {"serious": 0.6}, clears=("joking",), why="something heavy"),
    # urgent: a danger or emergency (pressure words alone, "hurry up and play the next song", only want it quick: below;
    # a problem said under pressure is read as urgent by meaning.py)
    _c(r"\b(urgent|emergency|on fire|fire!|bleeding|can'?t breathe|"
       r"call (911|112|an ambulance)|ambulance|(kitchen|grease|house|a) fire|fire alarm|smoke alarm|gas leak|choking|"
       r"not breathing|unconscious|overdose|heart attack|seizure|flooding)\b",
       {"urgent": 0.6, "brief": 0.4, "serious": 0.3}, clears=("joking",), why="urgent"),
    _c(r"^\W*(quick|quickly|hurry|fast)\b[,!]|\b(asap|right now|immediately|hurry( up)?|help me)\b|\b(quick(ly)?|now)!",
       {"brief": 0.45, "urgent": 0.2}, why="in a hurry"),
    # wants a quick answer
    _c(r"\b(quick question|quickly|short version|tl;?dr|just (tell me|the answer|say it|give me)|keep it short|shorter|too long|"
       r"get to the point|in a word|yes or no|bottom line)\b",
       {"brief": 0.6}, why="wants it short"),
    # wants to talk
    _c(r"\b(guess what|you won'?t believe|can i tell you|let me tell you|i need to vent|wanna talk|want to talk|tell me more|"
       r"go on|keep going|what do you think)\b",
       {"talk": 0.5}, why="wants to talk"),
    # wants a tiny reply / to be left alone
    _c(r"^\W*(whatever|never ?mind|just leave it|leave it|forget it|forget about it|doesn'?t matter|fine|okay fine|ok fine|"
       r"nothing|nah,? (it'?s )?fine|leave me alone|drop it|it'?s fine)\W*$",
       {"dismiss": 0.8, "brief": 0.5}, clears=("talk",), why="wants a tiny reply"),
    _c(r"^\W*(enough|ok(ay)?,? ok(ay)?|shh+|hush|stop|stop it|that'?s enough|got it,? got it)\W*$",
       {"stop": 0.8, "brief": 0.6, "dismiss": 0.4}, why="wants Jarvis to stop"),
    # topics for the personality (social/personality.py), not moods: asking for a push, putting it off, a real win
    _c(r"\b(motivate me|hype me up|get me (going|hyped|motivated)|i need (a|some) (push|motivation|kick)|push me|"
       r"kick my (butt|ass)|(keep|hold) me accountable|i (don'?t|do not) feel like (\w+ing|doing (it|anything|this|that))|"
       r"i'?m (so |being )?(lazy|unmotivated|procrastinating)|procrastinat\w+|can'?t (get|make) myself|no motivation)\b",
       {"motivate": 0.6}, why="wants a push"),
    _c(r"\b(i'?ll (do|start|finish) it (tomorrow|later|next week|on monday|monday)|(start|do it|go) (tomorrow|next week|"
       r"on monday)|maybe (tomorrow|later|next week)|i don'?t have (the )?time|no time (today|for)|too (tired|busy|late) "
       r"(to|for)|skip(ping)? (the )?(gym|workout|run|class|studying|it today|today)|not (today|tonight),? (i'?m|i am)|"
       r"i'?ll get to it (later|eventually)|i can'?t because|it'?s (too|kinda) (late|hard) to start)\b",
       {"excuse": 0.55}, why="putting it off"),
    _c(r"\b(i (finally )?(finished|completed|shipped|launched|passed|won|closed|landed|aced|beat|crushed|smashed|hit) "
       r"(it|the|my|a|an|that|all|every)\b|i (got|landed) (the|a|my) (job|offer|promotion|deal|raise|client|contract)|"
       r"got promoted|signed (the|a|my first) (deal|client|contract)|(made|got) my first (sale|client|customer|dollar)|"
       r"first (sale|client|customer)|new (pr|personal (best|record))|lost \d+ (pounds|lbs|kilos|kg)|"
       r"(\d+|ten|twenty|thirty) days (straight|in a row)|i did it|we did it|nailed it)\b",
       {"win": 0.6}, why="a real win"),
]

POSITIVE_WORDS = re.compile(r"\b(awesome|amazing|great|incredible|good|best|love|happy|excited|nice|fun)\b", re.I)
NEGATIVE_WORDS = re.compile(r"\b(work\w*|broken|tired|exhaust\w*|hate|annoying|wrong|stupid|useless|bad|worst|sick|done)\b", re.I)


def language(text, now, recent_failures=0):
    """Evidence from the words alone. Weak by design: 'fucking' isn't frustration ('fucking awesome' isn't)."""
    t = str(text or "")
    out = []
    for cue in LANGUAGE:
        if not cue.rx.search(t):
            continue
        scale = 1.0
        if cue.needs_context:
            scale = 3.0 if recent_failures >= 2 else 1.6 if recent_failures == 1 else 0.6
        for dim, w in cue.effects.items():
            out.append(Evidence(dim, min(0.85, w * scale), "language", now, cue.why, cue.clears))
    # "haha you're useless": the joke marker makes an insult in the SAME sentence playful, not annoyance
    if any(e.dim == "joking" for e in out):
        out = [e for e in out if e.dim != "annoyed"]
        out = [Evidence(e.dim, e.weight * 0.3, e.source, e.at, e.why + " (joking)", e.clears) if e.dim == "frustration" else e
               for e in out]
    # profanity is intensity, not a direction: it strengthens whatever the sentence already leans toward
    if PROFANITY.search(t) and out:
        out = [Evidence(e.dim, min(0.9, e.weight + 0.15), e.source, e.at, e.why + " (intense)", e.clears) for e in out]
    elif PROFANITY.search(t):
        if POSITIVE_WORDS.search(t):
            out.append(Evidence("excited", 0.35, "language", now, "intense + positive"))
        elif NEGATIVE_WORDS.search(t):
            out.append(Evidence("frustration", 0.35, "language", now, "intense + negative"))
    # typed emphasis (speech recognition rarely produces these)
    letters = re.findall(r"[A-Za-z]{2,}", t)
    if letters and len(letters) <= 12 and sum(w.isupper() for w in letters) >= max(2, len(letters) * 0.6):
        out.append(Evidence("energy_up", 0.3, "language", now, "all caps"))
    if t.count("!") >= 2:
        out.append(Evidence("energy_up", 0.2, "language", now, "!!"))
    if len(t.split()) >= 35:
        out.append(Evidence("talk", 0.25, "language", now, "long telling"))
    return out


CORRECTION = re.compile(r"^\W*(no\b|nope\b|not that|wrong|i said|i meant|that'?s not|i asked)", re.I)
MORE = re.compile(r"\b(more detail|explain( that)?( more)?|longer|elaborate|tell me more|go deeper)\b", re.I)


def _norm(t):
    return " ".join(re.findall(r"[a-z0-9']+", str(t).lower()))


def behavior(text, now, history):
    """Evidence from how they're interacting: `history` is SocialState's recent turns (newest last)."""
    out = []
    t = _norm(text)
    recent = [h for h in history if now - h["at"] < 180]
    prev = recent[-1] if recent else None
    if t and any(_norm(h["user"]) == t for h in recent[-3:]) and len(t.split()) >= 2:
        failed = bool(prev and prev.get("failed"))
        out.append(Evidence("frustration", 0.5 if failed else 0.35, "behavior", now, "repeated the request"))
        out.append(Evidence("brief", 0.3, "behavior", now, "repeated the request"))
    if prev and CORRECTION.search(text or ""):
        out.append(Evidence("frustration", 0.4 if prev.get("failed") else 0.2, "behavior", now, "corrected Jarvis"))
        out.append(Evidence("annoyed", 0.25 if prev.get("failed") else 0.1, "behavior", now, "corrected Jarvis"))
    interrupted = [h for h in recent[-3:] if h.get("interrupted")]
    if interrupted:
        out.append(Evidence("brief", 0.3 + 0.15 * (len(interrupted) - 1), "behavior", now, "cut Jarvis off"))
        if len(interrupted) >= 2:
            out.append(Evidence("annoyed", 0.15, "behavior", now, "cut Jarvis off repeatedly"))
    if MORE.search(text or ""):
        out.append(Evidence("talk", 0.5, "behavior", now, "asked for more", clears=("brief",)))
    words = len(t.split())
    if 0 < words <= 2 and prev and not str(prev.get("reply", "")).rstrip().endswith("?"):
        out.append(Evidence("brief", 0.15, "behavior", now, "very short reply"))
    return out


def conversation(now, history):
    """How many of the last few exchanges went wrong (failed tools, 'not sure that worked', 'missed that')."""
    return sum(1 for h in history[-4:] if now - h["at"] < 300 and h.get("failed"))


def situation(now, hour, driving=False, on_call=False, ringing=False):
    out = []
    if hour >= 23 or hour < 5:
        out.append(Evidence("energy_down", 0.15, "situation", now, "late at night"))
    if driving:
        out.append(Evidence("brief", 0.5, "situation", now, "driving"))
    elif on_call:
        out.append(Evidence("brief", 0.25, "situation", now, "on a call"))
    if ringing:
        out.append(Evidence("brief", 0.2, "situation", now, "an alarm was ringing"))
    return out
