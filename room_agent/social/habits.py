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
