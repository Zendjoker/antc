"""Assistant habits taken out of what Jarvis says, in code, sentence by sentence: stock openers ("Certainly!"), stock
closers ("Let me know if you need anything else."), therapy lines ("I understand how frustrating that must be."), and
using their name again right after it was just used. Only these fixed patterns; the rest of a reply is untouched.
"""

import re

DROP = re.compile(
    r"^\W*(?:is there anything else (?:i can (?:help|do)(?: you)?(?: with)?|you need)[^.?!]*|"
    r"(?:just |please |feel free to )?let me know if (?:you need|there'?s) anything(?: else)?[^.?!]*|"
    r"feel free to (?:ask|reach out)[^.?!]*|i'?m (?:always )?here (?:to help|if you need (?:me|anything))[^.?!]*|"
    r"(?:i )?hope (?:this|that) helps[^.?!]*|happy to help[^.?!]*|"
    r"i (?:completely |totally )?understand how (?:frustrating|difficult|hard|annoying|upsetting) (?:that|this|it) "
    r"(?:must be|is|can be)[^.?!]*|i hear (?:that )?you'?re feeling [^.?!]*|it sounds like you'?re feeling [^.?!]*)"
    r"\s*[.!?]*\s*$", re.I)
OPENER = re.compile(r"^\s*(?:certainly|of course|absolutely|great question|i'?d be (?:happy|glad) to help(?: with that)?|"
                    r"sure thing,? i'?d be happy to)\s*[!.,]\s*", re.I)


def scrub(sentence, user_name="", name_recently=False):
    """-> the sentence without stock assistant phrasing ('' = drop it entirely)."""
    s = sentence.strip()
    if DROP.match(s):
        return ""
    s = OPENER.sub("", s)
    if user_name and name_recently:  # (not again right away: "Hey Adam, ..." / "..., Adam.")
        n = re.escape(user_name)
        s = re.sub(rf"^(?:(?:hey|oh|okay|ok|well|so|alright)\s+)?{n}\s*,\s*", "", s, flags=re.I)
        s = re.sub(rf",\s*{n}(\s*[.!?]+)$", r"\1", s, flags=re.I)
    s = s.strip()
    if s and s[0].islower() and sentence.strip()[:1].isupper():
        s = s[0].upper() + s[1:]
    return s if re.search(r"\w", s) else ""


def uses_name(text, user_name):
    return bool(user_name) and re.search(rf"\b{re.escape(user_name)}\b", text or "", re.I) is not None



# ---------------------------------------------------------------------------------------------- the personality's limits
# (social/personality.py decides what fits THIS reply; these keep it to that in code, whatever the model wrote. Text that
# is quoted or read out (an email, a message) is never rewritten: what it says stays exactly what it says.)
QUOTED = re.compile(r"[\"“”]|\b(?:says|said|wrote|writes|texted|replied|reads)\s*[:,]|\bsubject\b", re.I)
_LEAD = (r"(?:^\W*|[,;:—–]\s*|\b(?:yo|hey|ay+|aight|alright|okay|ok|look|listen|bet|nah|yeah|yep|yes|no|thanks|thank you|"
         r"come on|let'?s go+|let'?s get it|good morning|good night|morning|night|what'?s good|what'?s up|sup)\s+)")


def vocatives(text, terms):
    """Where `text` calls someone one of these names ("Bet, boss." / "Boss, listen." / "yo bro"), not where it just
    mentions one ("your boss emailed", "Boss wants it Friday"). -> [(start, end, term)], first first, no overlaps"""
    found = []
    for term in sorted({t for t in terms if t}, key=len, reverse=True):
        rx = re.compile(rf"{_LEAD}({re.escape(term)})(?=\s*(?:[,.!?;:—–]|$))", re.I)
        for m in rx.finditer(text or ""):
            if not any(a < m.end(1) and m.start(1) < b for a, b, _ in found):
                found.append((m.start(1), m.end(1), term))
    return sorted(found)


def _cut_vocative(s, start, end):
    """Take one vocative out with its comma: "Bet, boss." -> "Bet." / "Boss, listen." -> "listen." / "yo bro" -> "yo"."""
    before, after = s[:start], s[end:]
    if re.search(r",\s*$", before):
        before = re.sub(r",\s*$", "", before)
    elif re.match(r"\s*,", after):  # ("Alright man, it's 5." -> "Alright, it's 5.")
        after = re.sub(r"^\s*,\s*", ", " if before.strip() else "", after)
    return re.sub(r"\s{2,}", " ", before.rstrip() + (after if after[:1] in ".!?;:" else " " + after.lstrip())).strip()


# strong words: never (the profile's swearing is "mild" at most); mild ones only where this reply allows them
STRONG = [(r"\bwhat the (?:fuck|hell)\b", "what"), (r"\b(?:holy|oh|aw|ah) (?:shit|fuck)\b", "wow"),
          (r"\bno shit\b", "no kidding"), (r"\bbullshit\b", "nonsense"), (r"\bshitty\b", "bad"), (r"\bshit\b", "stuff"),
          (r"\bmother ?fuck\w*\b", ""), (r"\bthe fuck\s+", ""), (r"\bfuck(?:ing|in'?|ed)?\s+(?=\w)", ""), (r"\bfuck\w*\b", ""),
          (r"\b(?:bitch|bastard|cunt|dick|asshole)\w*\b", "")]
MILD = [(r"\bhell (?:yeah|yes)\b", "yeah"), (r"\bhell no\b", "no way"), (r"\bthe (?:hell|heck)\s+", ""),
        (r"\b(?:as|like) hell\b", ""), (r"\bhell\b", "heck"), (r"\b(?:god ?)?damn(?:ed|n)?\s+(?=\w)", ""),
        (r"\b(?:god ?)?damn\w*\b[,!]?", ""), (r"\bbadass\b", "solid"), (r"\bcrap(?:py)?\b", "junk"),
        (r"\bpissed(?: off)?\b", "mad")]
PROFANE = re.compile(r"\b(?:fuck\w*|shit\w*|damn\w*|goddamn\w*|hell|crap\w*|bitch\w*|bastard\w*|asshole\w*|badass|piss\w*)\b",
                     re.I)
# swearing AT them or anyone, insults, put-downs (any moment)
ABUSE = re.compile(r"\byou(?:'re| are)? (?:such )?(?:an? )?(?:fucking |damn |lazy |stupid |dumb |pathetic |little )*"
                   r"(?:idiot|moron|loser|dumbass|jackass|clown|bitch|failure|pathetic|worthless|useless|stupid|dumb|"
                   r"lazy|weak|a joke|trash|garbage)\b|"
                   r"\b(?:screw|fuck|damn) (?:you|him|her|them)\b|\bshut (?:the (?:hell|fuck) )?up\b", re.I)
# feelings, a body or a past it doesn't have ("I feel you" is just "I hear you": it stays)
PRETEND = re.compile(r"(?:^|(?<=[,;]))\s*(?:trust me,?\s*)?(?:i'?ve been there|i'?ve lived (?:it|that|through)|i (?:know|get) "
                     r"(?:exactly )?how (?:that|it|you) feels?|i feel your pain|been there,? done that|back in my day|"
                     r"when i was (?:young|a kid|your age|growing up)|i remember when i|i'?ve felt (?:that|it|the same)|"
                     r"i went through (?:that|the same)|that hits me)\b[^,;.!?]*[,;]?", re.I)
FEELING = re.compile(r"\bi'?m (?:so |really |hella |mad |super )?(?:proud|happy|excited|thrilled) (?:of|for) "
                     r"(?:you\b|(?=how you|what you|the way you))|\bi feel (?:so |really )?(?:proud|happy) (?:of|for) you\b|"
                     r"\bthat makes me (?:so )?(?:happy|proud)\b", re.I)
# lines that turn into a costume when they come back: said as a line of their own, once in a while at most
CATCHPHRASES = ("let's get it", "let's go", "you got this", "you've got this", "we move", "no excuses", "lock in",
                "locked in", "stay hard", "keep pushing", "that's what i'm talking about", "say less", "no cap", "on god",
                "built different", "different breed", "let's work", "no days off", "big moves", "that's fire",
                "real talk", "level up", "stay locked in", "keep that same energy", "proud of you", "that's big")
SLOGAN = re.compile(r"^\W*(?:as (?:the saying goes|they say|somebody (?:once )?said)|like they say|remember,? (?:champions|winners)|"
                    r"(?:champions|winners) (?:never|don'?t) (?:quit|make excuses)|no pain,? no gain|rise and grind|"
                    r"hustle (?:harder|beats)|discipline (?:is|equals) freedom|the grind never stops)\b", re.I)


def _phrase(phrase):
    """A catchphrase said as a clause of its own ("Let's go!", "..., you got this.")."""
    body = re.escape(phrase).replace("'", "['’]")
    return re.compile(rf"(?:^|(?<=[,;:—–.!?]))\s*({body})\s*(?:[,.!?;:—–]+|$)", re.I)


def _feeling(m):
    t = m.group(0).lower()
    return "respect for " if t.rstrip().endswith(("of", "for")) else "that's big"


def _apply(s, table):
    for rx, sub in table:
        s = re.sub(rx, sub, s, flags=re.I)
    return s


def _tidy(s, original):
    s = re.sub(r"\s+([,.!?;:])", r"\1", s)
    s = re.sub(r"([,;:])\s*([,.!?;:])", r"\2", s)
    s = re.sub(r"([.!?])(?:\s*[.,;:])+", r"\1", s)
    s = re.sub(r"^\W+(?=\w)", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    if s and s[0].islower() and original.strip()[:1].isupper():
        s = s[0].upper() + s[1:]
    return s if re.search(r"\w", s) else ""


def enforce(sentence, flavor, said=(), recent=(), confirming=False, reporting=False, outside=False):
    """The personality's hard limits on one sentence (flavor: personality.Flavor for this reply; said: this reply's
    sentences already let through; recent: the last replies; outside: this reply passes on outside content, an email,
    a page, a file: its words are never rewritten, only the profile's own nicknames are kept to the limit).
    -> the sentence, trimmed ('' = drop it)."""
    from room_agent.social.personality import NEVER_ADDRESS

    s = (sentence or "").strip()
    if not s:
        return s
    if SLOGAN.match(s) and not outside:
        return ""
    quoted = outside or bool(QUOTED.search(s))
    if not quoted:
        if ABUSE.search(s):
            return ""
        s = PRETEND.sub("", s)
        s = FEELING.sub(_feeling, s)
        # swearing: never strong; mild only where this reply allows it, once, and never in a confirmation or a report
        s = _apply(s, STRONG)
        if (not flavor.profanity or flavor.serious or confirming or reporting
                or any(PROFANE.search(x) for x in said)):
            s = PROFANE.sub("", _apply(s, MILD))
        else:
            first = PROFANE.search(s)
            if first and PROFANE.search(s, first.end()):  # (one is emphasis; two is a habit)
                s = s[:first.end()] + PROFANE.sub("", _apply(s[first.end():], MILD))
    # nicknames: one per reply at most, the casual ones only when it's relaxed, none when serious or confirming, never
    # one they asked to stop, never one nobody chose ("man", "dude", "sir"...)
    allowed = set() if not flavor.address or flavor.serious or confirming else \
        {t.lower() for t in flavor.terms if flavor.casual_ok or t.lower() not in (c.lower() for c in flavor.casual)}
    banned = set() if outside else {t.lower() for t in NEVER_ADDRESS + ("bro", "brother", "bruh", "my guy", "my man", "big dog")}
    banned |= {t.lower() for t in flavor.terms}
    used = any(vocatives(x, flavor.terms) for x in said)
    keep = None
    found = vocatives(s, banned)
    for start, end, term in found:
        if term.lower() in allowed and not used:
            keep, used = start, True
            break
    for start, end, term in reversed(found):
        if start != keep:
            s = _cut_vocative(s, start, end)
    # the same catchphrase again (in this reply or the last few): that phrase goes, the rest stays
    before = list(said) + list(recent)
    for phrase in CATCHPHRASES:
        rx = _phrase(phrase)
        if rx.search(s) and any(rx.search(x) for x in before):
            s = rx.sub(" ", s)
    s = _tidy(s, sentence)
    if s and flavor.terms and not re.sub(r"\W+", "", re.sub("|".join(re.escape(t) for t in flavor.terms), "", s, flags=re.I)):
        return ""  # (nothing left but a nickname: "Boss!")
    return s
