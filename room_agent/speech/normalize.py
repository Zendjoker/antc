"""What gets SAID for text that reads badly aloud. Only the speech text changes; the semantic text (history, memory,
UI) keeps "$1,250" and "adam@gmail.com".

    full   (local voices: Piper reads the raw text) emails, URLs, currency, percent, degrees, ISO dates, symbols,
           listed acronyms
    light  (ElevenLabs: its own apply_text_normalization reads numbers and currency) only what it tends to get wrong:
           emails, URLs, ISO dates, symbols, listed acronyms
"""

import datetime
import re

from room_agent.emails import spoken_form

EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
URL = re.compile(r"\b(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|ai|dev|app|co|fr|uk|de|ma|edu|gov))"
                 r"((?:/[\w./%-]*)?)", re.I)
MONEY = re.compile(r"([$€£])\s?(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s?(k|m|bn|million|billion|thousand)?\b", re.I)
CURRENCY = {"$": "dollars", "€": "euros", "£": "pounds"}
PERCENT = re.compile(r"(\d)\s?%")
DEGREES = re.compile(r"(-?\d+(?:\.\d+)?)\s?°?\s?([FC])\b")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
SYMBOLS = [(re.compile(r"\s&\s"), " and "), (re.compile(r"(\w)\s?\+\s?(\w)"), r"\1 plus \2"), (re.compile(r"\s/\s"), " or "),
           (re.compile(r"\s->\s|\s=>\s|→"), " to "), (re.compile(r"\s~\s?(\d)"), r" about \1")]
ACRONYMS = {"API", "URL", "CPU", "GPU", "RAM", "SQL", "PDF", "USB", "HTML", "CSS", "JSON", "AI", "TV", "PC", "ETA", "FAQ"}


def _ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _url(m):
    host, path = m.group(1), m.group(2) or ""
    said = host.replace(".", " dot ").replace("-", " dash ")
    if path.strip("/"):
        said += " slash " + " slash ".join(x for x in path.strip("/").split("/") if x)
    return said


def _date(m):
    try:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return m.group(0)
    return f"{d.strftime('%B')} {_ordinal(d.day)}, {d.year}"


def _money(m):
    sym, amount, scale = m.group(1), m.group(2), (m.group(3) or "").lower()
    scale = {"k": "thousand", "m": "million", "bn": "billion"}.get(scale, scale)
    return f"{amount} {scale + ' ' if scale else ''}{CURRENCY[sym]}"


CITATION = re.compile(r"\s*\[(?:\d+(?:\s*[,–-]\s*\d+)*|sources?(?: used)?|source \d+)\]", re.I)


def speech_text(text, level="full", acronyms=ACRONYMS):
    t = CITATION.sub("", str(text))  # (source markers "[2]" / "[sources used]" are for the screen, never read out)
    t = EMAIL.sub(lambda m: spoken_form(m.group(0).lower()), t)
    t = URL.sub(_url, t)
    t = ISO_DATE.sub(_date, t)
    if level == "full":
        t = MONEY.sub(_money, t)
        t = PERCENT.sub(r"\1 percent", t)
        t = DEGREES.sub(lambda m: f"{m.group(1)} degrees", t)
    for rx, rep in SYMBOLS:
        t = rx.sub(rep, t)
    if level == "full" and acronyms:  # (local voices read "GPU" as a word; ElevenLabs reads acronyms well)
        t = re.sub(r"\b(" + "|".join(sorted(acronyms, key=len, reverse=True)) + r")s?\b",
                   lambda m: " ".join(m.group(0).rstrip("s")) + ("'s" if m.group(0).endswith("s") and len(m.group(0)) > 2 else ""),
                   t)
    return re.sub(r"\s{2,}", " ", t).strip()


CLAUSE = re.compile(r"[,;]\s+(?=(?:and|or|but|so|because|then|which|while|unless)\b)|;\s+", re.I)


def clauses(text, min_words=14, min_part=4):
    """A long sentence -> its clauses, for the local voice only: Piper renders a whole sentence before any sound comes
    out, and squashes a long run of short words ("dot ... at ... dot com") into mush. Split only at a comma or semicolon
    before a conjunction, so each part is still something said on one breath (it keeps its comma: not a full stop)."""
    t = str(text).strip()
    if len(t.split()) < min_words:
        return [t]
    parts, start = [], 0
    for m in CLAUSE.finditer(t):
        piece = t[start:m.start()].strip()
        if len(piece.split()) >= min_part and len(t[m.end():].split()) >= min_part:
            parts.append(piece + ",")
            start = m.end()
    parts.append(t[start:].strip())
    return parts
