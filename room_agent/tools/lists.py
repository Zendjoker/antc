"""Lists and notes: "add milk to my shopping list", "what's on my to-do list?", "take a note: ...". Kept in lists.json.

A list is named loosely ("todo", "to do", "tasks" -> to-do; "grocery", "groceries" -> shopping); any other name makes a new
list. Items are matched loosely too ("milk" finds "Milk (2 liters)"). Checked-off items stay for a week, so "what did I
finish?" works, then they're dropped. "notes" is a list of dated notes.
"""

import json
import re
import threading
import time
import uuid

from room_agent import config

ALIASES = {"to-do": ("todo", "to do", "to-do", "todos", "to-dos", "tasks", "task", "things to do"),
           "shopping": ("shopping", "grocery", "groceries", "grocery list", "shopping list", "groceries list"),
           "notes": ("notes", "note")}
DONE_KEEP_S = 7 * 86400
MAX_ITEMS = 200
_lock = threading.Lock()


def canonical(name):
    n = re.sub(r"\b(my|the|list)\b", " ", str(name or "").lower()).strip(" -_.")
    n = " ".join(n.split())
    if not n:
        return "to-do"
    for key, names in ALIASES.items():
        if n in names or n.replace(" list", "") in names:
            return key
    return n[:40]


def _load():
    try:
        data = json.loads(config.LISTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    """Write, then read back: raises OSError if the file doesn't hold what was written (a full disk, a sync tool...)."""
    tmp = config.LISTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(config.LISTS_FILE)
    if _load() != json.loads(json.dumps(data)):
        raise OSError("the list file doesn't read back as written")


def _prune(items, now=None):
    now = now or time.time()
    return [i for i in items if not i.get("done") or now - i.get("done_at", now) < DONE_KEEP_S]


def _match(items, text):
    """The open item that best matches what they said, or None."""
    want = str(text or "").lower().strip()
    if not want:
        return None
    words = set(re.findall(r"\w+", want))
    best, score = None, 0.0
    for i in items:
        t = i["text"].lower()
        s = 1.0 if t == want else 0.9 if want in t or t in want else (
            len(words & set(re.findall(r"\w+", t))) / max(len(words), 1) * 0.8)
        if s > score:
            best, score = i, s
    return best if score >= 0.5 else None


def _say(name):
    return "notes" if name == "notes" else f"{name} list"


def add(text, list_name="to-do"):
    text = " ".join(str(text or "").split()).strip(" .")
    if not text:
        return "NEEDS: what to add."
    name = canonical(list_name)
    with _lock:
        data = _load()
        items = _prune(data.get(name, []))
        dup = next((i for i in items if not i.get("done") and i["text"].lower() == text.lower()), None)
        if dup:
            return f"OK: nothing needed: \"{dup['text']}\" is already on your {_say(name)}."
        if len([i for i in items if not i.get("done")]) >= MAX_ITEMS:
            return f"FAILED: your {_say(name)} already has {MAX_ITEMS} items; clear some first."
        items.append({"id": uuid.uuid4().hex[:8], "text": text[:300], "added": time.time(), "done": False})
        data[name] = items
        _save(data)
    n = len([i for i in items if not i.get("done")])
    return f"OK: added \"{text}\" to your {_say(name)} ({n} item{'s' if n != 1 else ''} on it now)."


def show(list_name="to-do", include_done=False):
    name = canonical(list_name)
    data = _load()
    if name not in data:
        others = [k for k in data if any(not i.get("done") for i in data[k])]
        return f"OK: you don't have a {_say(name)}." + (f" Your lists: {', '.join(others)}." if others else "")
    items = _prune(data[name])
    open_ = [i for i in items if not i.get("done")]
    done = [i for i in items if i.get("done")]
    if not open_ and not (include_done and done):
        return f"OK: your {_say(name)} is empty." + (f" ({len(done)} checked off this week.)" if done else "")
    if name == "notes":
        lines = [f"{time.strftime('%a %b %d', time.localtime(i['added']))}: {i['text']}" for i in open_[-15:]]
        return f"OK: your notes ({len(open_)}), newest last:\n" + "\n".join(lines)
    out = f"OK: your {_say(name)} has {len(open_)} item{'s' if len(open_) != 1 else ''}: " + "; ".join(i["text"] for i in open_)
    if include_done and done:
        out += ". Checked off this week: " + "; ".join(i["text"] for i in done)
    return out + "."


def complete(text, list_name="to-do"):
    name = canonical(list_name)
    with _lock:
        data = _load()
        items = _prune(data.get(name, []))
        hit = _match([i for i in items if not i.get("done")], text)
        if hit is None:
            return f"FAILED: nothing like \"{text}\" is open on your {_say(name)}. Nothing was changed."
        hit.update(done=True, done_at=time.time())
        data[name] = items
        _save(data)
    left = len([i for i in items if not i.get("done")])
    return f"OK: checked off \"{hit['text']}\" ({left} left on your {_say(name)})."


def remove(text, list_name="to-do"):
    name = canonical(list_name)
    with _lock:
        data = _load()
        items = _prune(data.get(name, []))
        hit = _match(items, text)
        if hit is None:
            return f"FAILED: nothing like \"{text}\" is on your {_say(name)}. Nothing was removed."
        items.remove(hit)
        data[name] = items
        _save(data)
    return f"OK: removed \"{hit['text']}\" from your {_say(name)}."


def clear(list_name="to-do", only_done=False):
    name = canonical(list_name)
    with _lock:
        data = _load()
        items = data.get(name, [])
        keep = [i for i in items if not i.get("done")] if only_done else []
        n = len(items) - len(keep)
        if only_done:
            data[name] = keep
        else:
            data.pop(name, None)
        _save(data)
    return f"OK: cleared {n} item{'s' if n != 1 else ''} from your {_say(name)}."


def snapshot():
    """{list: [open item texts]} for the dashboard."""
    return {k: [i["text"] for i in _prune(v) if not i.get("done")] for k, v in _load().items()
            if any(not i.get("done") for i in v)}


def restore(name, item):
    """Undo of a removal: put the item back as it was."""
    with _lock:
        data = _load()
        data.setdefault(name, []).append(item)
        _save(data)
