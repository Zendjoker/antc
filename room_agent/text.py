"""Pure text helpers: wake-word and quiet-command parsing, stage-direction stripping."""

import re

WAKE_PREFIX = re.compile(r"^\W*(?:(?:hey|hi|hay|okay|ok|yo)\W+)?(?:jarvis|jarvas|jervis)\b\W*", re.I)

# "be quiet until I call you", "don't talk till I say hey Jarvis", "go quiet", "shut up", "can you stop talking"
_QUIET_START = re.compile(
    r"^(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+|just\s+|now\s+|ok(?:ay)?,?\s+|alright,?\s+|hey,?\s+)*"
    r"(?:(?:be|stay|go|keep|remain)\s+(?:quiet|silent)|quiet\s+(?:down|mode)|shut\s+up|hush|zip\s+it"
    r"|go\s+(?:to\s+)?sleep|sleep\s+mode|stop\s+listening|mute(?:\s+yourself)?|no\s+more\s+talking"
    r"|(?:don'?t|do\s+not|stop)\s+(?:talk(?:ing)?|speak(?:ing)?|answer(?:ing)?|respond(?:ing)?|repl(?:y|ying)"
    r"|say(?:ing)?\s+anything))\b(?P<rest>.*)$", re.I)
# ...and the WHOLE rest of the sentence must be one of these, so "shut up and listen, I have a question" or
# "don't answer until I finish" are NOT quiet commands
_QUIET_REST = re.compile(
    r"^(?:[\s,.!?]*(?:please|ok(?:ay)?|jarvis|now|right\s+now|anymore|any\s+more"
    r"|for\s+(?:a\s+(?:while|bit|sec(?:ond)?|minute|few\s+minutes)|now|some\s+time|the\s+rest\s+of\s+the\s+\w+)"
    r"|(?:until|till|til|unless)\s+i\s+(?:say|call|wake)\b[^.?!]{0,40}"
    r"|(?:and|then)\s+wait(?:\s+(?:for|till|until)\s+[^.?!]{0,30})?))*[\s,.!?]*$", re.I)


# "stop", "can you stop?", "please stop", "okay, enough", "hold on": said over a reply, it means stop THAT reply
_STOP = re.compile(r"^\W*(?:(?:ok(?:ay)?|jarvis|hey|wait|no|please|just|(?:can|could|would|will) you)[\s,.!]+)*"
                   r"(?:stop(?:\s+(?:it|that|talking|there|now|please|jarvis))*|enough|that'?s enough|hold on|shh+|"
                   r"(?:ok(?:ay)?|alright)\s+stop)(?:[\s,.!]+(?:please|jarvis|now|thanks|thank you))*\W*$", re.I)


def is_stop_request(text):
    """Only "stop" (in some form), nothing else to answer: decided in code."""
    return bool(_STOP.match(str(text or "").strip()))


def is_quiet_command(text):
    """An explicit request to go quiet until the wake word. Decided in code, not by the LLM."""
    m = _QUIET_START.match(text.strip())
    return bool(m and _QUIET_REST.match(m.group("rest")))


# "let me finish my sentence", "hold on, let me think", "I'm not done": a request to wait, not something to answer
_FINISH = re.compile(
    r"^(?:(?:ok(?:ay)?|hold\s+on|hang\s+on|wait|hey|jarvis|please|just|no|but|now|one\s+(?:sec(?:ond)?|moment))[\s,.!]+)*"
    r"(?:let\s+me\s+(?:finish|talk|explain|speak|say|continue|think)|let\s+me\s+get\s+this\s+out"
    r"|(?:give|gimme)\s+me\s+a\s+(?:sec(?:ond)?|moment|minute|bit)\s+to\s+(?:think|finish|explain)"
    r"|i'?m\s+(?:not\s+(?:done|finished)|still\s+(?:talking|thinking|explaining))"
    r"|(?:don'?t|do\s+not)\s+(?:interrupt|cut\s+me\s+off)(?:\s+me)?)\b[^.?!]{0,40}[.!]*\s*$", re.I)


def is_let_me_finish(text):
    return bool(_FINISH.match(text.strip()))


def strip_wake(text):
    """'Hey Jarvis, what's the weather?' -> "what's the weather?"   'Hey Jarvis.' -> ''"""
    return WAKE_PREFIX.sub("", text).strip()


# words that can't end a thought (no end punctuation + one of these = they're still talking), and openers like "can you"
_DANGLING = frozenset("a an the to for and or but with in on at of from by if that because then my your our their some about "
                      "into than as plus also while until before after".split())
_OPENER = re.compile(r"(?:^|\s)(?:can|could|would|will|do|did|are|should)\s+you(?:\s+please)?$|\bi\s+(?:want|need)\s+you$", re.I)


_FILLER = re.compile(r"(?:[\s,]+(?:u+m+|u+h+|uhm|e+r+m?|hmm+|you know|i mean|kind of|kinda|sort of|basically))+$", re.I)
_REQUEST = re.compile(r"(?:^|\s)(?:can|could|would|will)\s+you(?:\s+please)?$|\bi\s+(?:want|need)\s+you$", re.I)


def looks_unfinished(text):
    """Said so it trails off: an ellipsis, dash or comma at the end, or no end punctuation and a last word that needs
    something after it. Judged from the words alone, so it works for any request."""
    t = text.strip().rstrip("\"')")
    if t.endswith(("...", "\u2026", "-", "\u2014", ",")):
        return True
    if not t or t[-1] in ".?!":
        return False
    if t.split()[-1].lower() in _DANGLING or _OPENER.search(t):
        return True
    # trailing fillers ("but can you, like", "and um", "so I was, kind of"): look at what comes before them
    core = _FILLER.sub("", t).strip()
    if core != t:
        return not core or len(core.split()) <= 3 or looks_unfinished(core)
    # "like" is only a filler after an opener or a dangling word ("can you like", "and like"); "what do you like" is complete
    m = re.search(r"^(.*\S)\s+like$", t, re.I)
    return bool(m and (_REQUEST.search(m.group(1)) or m.group(1).split()[-1].lower() in _DANGLING))


def join_fragments(first, second):
    """Two pieces of one thought, without the trailing/leading ellipses that marked the pause."""
    first = re.sub(r"(?:\.{2,}|[\s,\-\u2014\u2026])+$", "", first.strip())
    second = re.sub(r"^[\s.\u2026]+", "", second.strip())
    return f"{first.rstrip('.')} {second}".strip() if second else first


_ACTIONS = (r"(?:stays?|staying|laughs?|laughing|chuckles?|giggles?|sighs?|pauses?|nods?|smiles?|grins?|shrugs?|"
            r"clears? (?:my |his |her |their )?throat|whispers?|remains? (?:silent|quiet)|silence|silently|waits?|listens?|"
            r"goes quiet|stays silent)")
STAGE_DIRECTION = re.compile(rf"\*\s*{_ACTIONS}\b[^*]{{0,60}}\*|\(\s*{_ACTIONS}\b[^()]{{0,60}}\)|^\s*\*[^*]{{1,80}}\*\s*$", re.I)


def strip_stage_directions(text):
    """'*stays quiet*' or '(pauses)' would be read out loud: drop them. '*not*' (emphasis) keeps its word."""
    text = STAGE_DIRECTION.sub("", text)
    text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)  # emphasis markers go, the word stays
    return re.sub(r"\s{2,}", " ", text).strip()
