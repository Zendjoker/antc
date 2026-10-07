"""Persistent memory tools: remember, recall, forget."""

import logging
import re

from room_agent import runtime as rt
from room_agent.config import USER_NAME
from room_agent.memory import FORGET_ALL, PROFILE_KEYS, MemoryError_, mentions

log = logging.getLogger("room-agent")


def remember(args):
    """Write to persistent memory now and read it back. Only then does the result say OK."""
    memory = rt.memory
    if not memory.available:
        return f"UNAVAILABLE: persistent memory isn't working ({memory.error}). Nothing was saved."
    content = str(args.get("content") or "").strip()
    key, value = args.get("key"), str(args.get("value") or "").strip()
    if not content and not (key and value):
        return "NEEDS: content (what to remember). Ask what they want remembered. Nothing was saved."
    try:
        if key:
            changed, previous = memory.set_key(key, value or content, source="explicit")
            stored = memory.get(key)
            if not changed:
                return f"OK: already stored: {PROFILE_KEYS.get(key, key)} = {stored}."
            replaced = f" (replaces the old value: {previous})" if previous else ""
            return f"OK: saved and verified: {PROFILE_KEYS.get(key, key)} = {stored}{replaced}."
        row = memory.add(content, args.get("category") or "fact", source="explicit")
        return f"OK: saved and verified: {content}." if row else f"OK: that was already stored: {content}."
    except (MemoryError_, Exception) as e:
        log.error("remember failed: %s", e)
        return f"FAILED: couldn't save it ({e}). Nothing was stored."


def recall(query=""):
    memory = rt.memory
    if not memory.available:
        return f"UNAVAILABLE: persistent memory isn't working ({memory.error})."
    profile, facts, sums = memory.recall(query)
    parts = [f"{PROFILE_KEYS.get(k, k.replace('_', ' '))}: {v}" for k, v in profile.items()]
    parts += [f"{f['content']} (noted {f['updated_at'][:10]}, {'told directly' if f['source'] == 'explicit' else 'learned'})"
              for f in facts]
    parts += [f"past conversation {s['created_at']}: {s['summary']}" for s in sums]
    if parts:
        return "OK: stored about " + USER_NAME + ": " + "; ".join(parts)
    if memory.count() == 0:
        return f"OK: nothing is stored about {USER_NAME} at all yet."
    return f"OK: nothing stored matches '{query}' ({memory.count()} other items are stored)."


def forget(text):
    """Forget from memory AND from the saved dialogue, and don't let the writer re-learn or summarize it."""
    memory = rt.memory
    if not memory.available:
        return f"UNAVAILABLE: persistent memory isn't working ({memory.error})."
    try:
        removed = memory.forget(text)
    except (MemoryError_, Exception) as e:
        return f"FAILED: couldn't delete it ({e})."
    term = " ".join(re.findall(r"\w+", text.lower()))
    if term in FORGET_ALL:
        rt.recent = []
        rt.control["forgot_everything"] = True
    elif len(term) >= 3:
        rt.recent = [m for m in rt.recent if not mentions(m["text"], term)]
        rt.control.setdefault("forgot_terms", []).append(term)
    try:
        memory.save_recent(rt.recent)  # the word-for-word dialogue mustn't keep what was just forgotten
    except Exception as e:
        log.warning("couldn't rewrite the saved dialogue after forgetting: %s", e)
    if rt.writer:
        rt.writer.forgot(term)
    rt.control["forgot"] = True
    if removed:
        return "OK: deleted and verified gone: " + "; ".join(removed[:20])
    return f"OK: nothing stored matched '{text}', so nothing was deleted."
