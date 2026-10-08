"""When streamed model text is ready to be spoken: at natural speech boundaries, never token fragments.

    complete sentence   "Yeah, it's fixed."  goes as soon as its end punctuation and the next word arrive
    long clause         a sentence still running past ~110 characters (70 for the reply's first chunk, so the first
                        sound comes sooner) is cut at its last clause break (, ; :) if the part before it is a phrase
                        that stands on its own (40+ characters)
Not boundaries: "Dr." / "e.g." style abbreviations, decimals, and a mid-sentence "..." ("Bro... you fixed it").
"""

import re

ABBREV = {"mr", "mrs", "ms", "dr", "st", "vs", "etc", "e.g", "i.e", "approx", "no", "fig", "jr", "sr", "inc", "ltd"}
_END = re.compile(r"[.!?…]+[\"')\]]*(?=\s+[A-Z0-9\"'“(\[])")
_CLAUSE = re.compile(r"[,;:](?=\s)")
LONG, LONG_FIRST, MIN_HEAD = 110, 70, 40


def _is_boundary(buf, m):
    word = re.search(r"([\w.]+)$", buf[:m.start()])
    if word and word.group(1).lower().rstrip(".") in ABBREV and m.group(0).startswith(".") and len(m.group(0)) == 1:
        return False
    return True


def ready(buf, first=False):
    """-> (chunks ready to speak, the rest still being written)."""
    chunks, start = [], 0
    for m in _END.finditer(buf):
        if not _is_boundary(buf, m):
            continue
        piece = buf[start:m.end()].strip()
        if piece:
            chunks.append(piece)
        start = m.end()
    rest = buf[start:]
    limit = LONG_FIRST if first and not chunks else LONG
    if len(rest.strip()) >= limit:
        cuts = [m.end() for m in _CLAUSE.finditer(rest) if m.end() >= MIN_HEAD and not re.search(r"\d$", rest[:m.start()])]
        if cuts:
            head, rest = rest[:cuts[-1]].strip(), rest[cuts[-1]:]
            chunks.append(head)
    return chunks, rest.lstrip() if chunks else rest
