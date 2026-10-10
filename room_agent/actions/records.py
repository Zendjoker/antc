"""What was really written where, and a check in code when they say it isn't there.

Live test, 10 Oct 12:03-12:10: an issue went to the to-do list, then to a LIST named "LED_strip_issue.txt", a Twilio
problem went to memory only, and the model told them it was in the Desktop file; later, after only a memory lookup, it
told them the file had never been written (it had been, twice) and that it couldn't write files at all. Nothing in
code told the model where things actually went, and "I don't see it" was answered with more claims instead of a look.

    context_lines(text)   recent_records: the last writes this session by destination, with what the executor verified
                          (a list item or a memory is NOT a file on their PC); earlier_points: what they told Jarvis a
                          while ago that the conversation window no longer shows, so "that issue" / "both" / "the second
                          one" can be resolved instead of asking them to say it again
    dispute_note(text)    "I don't see it" / "you didn't add it" / "it's not there": every recent destination (and any
                          file they name) is read back NOW and the model gets exactly what's there, for this reply

Reads only: nothing here writes or runs an action. The journal (actions/journal.py) is the source: an action is
"done and checked" only when the executor verified it.
"""

import re
import time

from room_agent import runtime as rt
from room_agent.actions import journal

RECORD_TOOLS = ("append_to_file", "save_file", "add_to_list", "take_note", "remember")
WINDOW_S = 1800
_SEEN = r"(?:it|them|that|those|anything|any of (?:it|them)|the\s+(?!point\b|reason\b|why\b|difference\b)\w+|[\w.-]+\.(?:txt|md|csv|json))"
DISPUTE = re.compile(
    r"\b(?:i\s+)?(?:still\s+)?(?:don'?t|do not|can'?t|cannot|didn'?t|did not)\s+(?:see|find)\s+" + _SEEN +
    r"|\bi'?m\s+(?:still\s+)?not\s+(?:seeing|finding)\b(?!\s+(?:why|the point|how))"
    r"|\b(?:it'?s|its|it is|they'?re|that'?s)\s+not\s+(?:there|in\s+(?:it|there|the\s+\w+))\b"
    r"|\b(?:isn'?t|aren'?t|wasn'?t)\s+(?:there|in\s+(?:it|there|the\s+\w+))\b|\bnothing\s+(?:is\s+)?(?:there|in\s+(?:it|there))\b"
    r"|\byou\s+(?:didn'?t|did not|never|haven'?t)\s+(?:actually\s+|really\s+)?(?:add|write|save|put|record|log|do|call|send|"
    r"make|create|update)\b"
    r"|\b(?:it|that)\s+(?:didn'?t|did not|never)\s+(?:work|happen|save|go through|get (?:added|saved|written))\b"
    r"|\bwhere\s+is\s+it\b|\b(?:it'?s|the file is|the list is)\s+(?:empty|missing)\b", re.I)
STATE = {journal.COMPLETED: "done and checked", journal.UNVERIFIED: "done, not checked", journal.FAILED: "FAILED",
         journal.WAITING: "NOT done (it was waiting for their yes)", journal.CANCELED: "NOT done (cancelled)",
         journal.UNKNOWN: "outcome unknown: may not have happened"}


def _short(text, n=90):
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _norm(text):
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def _args(e):
    return e.get("args") if isinstance(e.get("args"), dict) else {}


def _what(e):
    a = _args(e)
    return a.get("content") or a.get("item") or a.get("text") or ""


def _where(e):
    a, n = _args(e), e["action"]
    if n in ("append_to_file", "save_file"):
        return f"the file {a.get('name') or '?'} ({a.get('folder') or 'desktop'}, on their PC)"
    if n == "add_to_list":
        return f"the {a.get('list') or 'to-do'} list inside Jarvis (not a file)"
    if n == "take_note":
        return "Jarvis's notes list (not a file)"
    return "Jarvis's memory (not a file)"


def recent(window_s=WINDOW_S):
    now = time.time()
    return [e for e in journal.recent(80) if e["action"] in RECORD_TOOLS and now - e["at"] < window_s]


# a request or a question (it was answered then); what's worth keeping is what they TOLD Jarvis: an issue, a situation
_ASKED = re.compile(r"^\W*(?:(?:hey|ok(?:ay)?|so|jarvis|please|now|and|can you|could you|would you|will you)\W+)*"
                    r"(?:set|start|stop|open|close|play|pause|turn|call|text|add|put|remind|show|tell|give|what|what'?s|whats|"
                    r"how|when|where|who|which|is|are|do|does|did|can|could|would|will|should)\b", re.I)


def _earlier_points(skip_last=3, limit=4):
    """What they told Jarvis in the last half hour (statements, not requests or questions) that the model's message
    window may no longer show."""
    users = [m for m in rt.recent if m.get("role") == "user"]
    older = users[:-skip_last] if len(users) > skip_last else []
    out, seen = [], set()
    for m in older[-30:]:
        text = str(m.get("text") or "")
        if len(text.split()) >= 6 and not _ASKED.match(text) and _norm(text) not in seen:
            seen.add(_norm(text))
            out.append((str(m.get("time", ""))[11:16], _short(text, 160)))
    return out[-limit:]


def context_lines(user_text):
    lines = []
    rec = recent()[-6:]
    if rec:
        lines.append(
            "- recent_records (what code knows was written where this session, oldest first; only 'done and checked' "
            "happened; a list item or a memory is NOT a file on their PC, so never call one 'added to the file'): "
            + " | ".join(f"[{time.strftime('%H:%M', time.localtime(e['at']))}] {_where(e)}: {_short(_what(e))!r} -> "
                         f"{STATE.get(e['state'], e['state'].lower())}"
                         + (f" ({_short(e.get('note'), 80)})" if e.get("note") and e["state"] != journal.COMPLETED else "")
                         for e in rec))
    points = _earlier_points()
    if points:
        lines.append("- earlier_points (things they told you before the messages above; when they say 'that issue', "
                     "'both' or 'the second one', it's most likely here: use it, don't ask them to repeat it): "
                     + " | ".join(f"[{t}] {x!r}" for t, x in points))
    return lines


# ---------------------------------------------------------------------------------------------- checking, in code
def _file_for(name, folder):
    from room_agent.computer import filewrite

    try:
        return filewrite._find_existing(name, folder or "desktop"), filewrite.folder(folder or "desktop")
    except Exception:  # noqa: BLE001
        return None, None


def _file_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _contains(haystack, snippet):
    n = _norm(snippet)
    return bool(n) and n[:80] in _norm(haystack)


def _named_files(text):
    """Text files in their Desktop / Documents whose name they said ("the LED strip issue" -> LED_strip_issue.txt)."""
    from room_agent.computer import filewrite

    said = re.sub(r"[^a-z0-9]", "", str(text or "").lower())
    out = []
    for where in ("desktop", "documents"):
        try:
            d = filewrite.folder(where)
            files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in filewrite.WRITE_EXT]
        except OSError:
            continue
        for p in files:
            stem = re.sub(r"[^a-z0-9]", "", p.stem.lower())
            if len(stem) >= 6 and stem in said:
                out.append(p)
    return out


def _check_file(path, folder_name, snippets):
    text = _file_text(path)
    if text is None:
        return f"{path.name} ({folder_name}): exists but couldn't be read"
    lines = len(text.splitlines())
    have = [f"{_short(s, 50)!r}: {'yes' if _contains(text, s) else 'NO'}" for s in snippets[:4] if s]
    return (f"{path.name} ({folder_name}, {lines} line{'s' if lines != 1 else ''}) is there; it contains "
            + (", ".join(have) if have else "nothing from the recent records"))


def dispute_note(text):
    """'I don't see it': read every recent destination back now. -> a system note for this reply, or ''."""
    if not DISPUTE.search(str(text or "")):
        return ""
    rec = recent()
    snippets = [_what(e) for e in rec if _what(e)][-4:]
    checks, seen_files = [], set()
    for e in reversed(rec[-6:]):
        a, n, what = _args(e), e["action"], _what(e)
        if n in ("append_to_file", "save_file"):
            path, folder = _file_for(a.get("name"), a.get("folder"))
            if path is None:
                checks.append(f"the file {a.get('name')} in {getattr(folder, 'name', 'Desktop')}: does NOT exist")
            elif str(path) not in seen_files:
                seen_files.add(str(path))
                checks.append(_check_file(path, path.parent.name, snippets))
        elif n in ("add_to_list", "take_note"):
            from room_agent.tools import lists

            name = lists.canonical(a.get("list") or ("notes" if n == "take_note" else "to-do"))
            items = [i["text"] for i in lists._load().get(name, []) if not i.get("done")]
            checks.append(f"the {name} list (inside Jarvis, not a file) has {_short(what, 50)!r}: "
                          f"{'yes' if any(_contains(i, what) for i in items) else 'NO'}")
        elif n == "remember":
            try:
                facts = [f["content"] for f in rt.memory.facts()] if rt.memory.available else []
            except Exception:  # noqa: BLE001
                facts = []
            checks.append(f"Jarvis's memory (not a file) has {_short(what, 50)!r}: "
                          f"{'yes' if any(_contains(f, what) for f in facts) else 'NO'}")
    for path in _named_files(text):
        if str(path) not in seen_files:
            seen_files.add(str(path))
            checks.append(_check_file(path, path.parent.name, snippets))
    if not checks:
        checks.append("nothing was written anywhere in the last half hour (no file, list or memory entry)")
    return (" (System note from code: they say something isn't there or didn't happen, so it was checked just now: "
            + "; ".join(dict.fromkeys(checks)) + ". Tell them exactly what this check found and where things really "
            "are (a list item or a memory is not the file). If something they wanted is missing, put it where they "
            "asked now with the right tool (it's checked again). Don't answer with just an acknowledgement, and don't "
            "claim anything this check didn't show.)")


def register():
    from room_agent.actions import core

    core.register_context(context_lines, order=14)
