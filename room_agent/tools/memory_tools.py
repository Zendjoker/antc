"""Persistent memory tools: remember, recall, forget."""

import logging
import re

from room_agent import runtime as rt
from room_agent.config import USER_NAME
from room_agent.memory import CALL_ME, FORGET_ALL, NAME_IS, PROFILE_KEYS, MemoryError_, mentions

log = logging.getLogger("room-agent")
# (the model's own note can only move a value to the SAFER key: "Prefer to be called 'boss'" is never their name)
_CALLED = re.compile(r"\b(call(ed)?|address(ed)?|nickname|refer(red)? to)\b", re.I)


def _their_words():
    """What they said for this: this turn's words, plus the request a 'yes' answers."""
    p = rt.pending
    source = getattr(p, "source_text", "") if p is not None and getattr(p, "capability", "") == "remember" else ""
    return f"{rt.turn_text or ''} {source}".strip()


def _name_key(value, content):
    """remember(key='name'): their real name, or what they want to be called? -> (key, override) or a NEEDS text.
    Only their own 'my name is ...' may change the name; 'call me boss' is address_as."""
    said = _their_words()
    call = CALL_ME.search(said)
    if call and (not NAME_IS.search(said) or value.lower() in said[call.end():call.end() + 40].lower()):
        return "address_as", False  # ("my name is Adam but call me boss": boss is what to call them)
    if NAME_IS.search(said):
        return "name", True
    if _CALLED.search(content):
        return "address_as", False
    stored = rt.memory.identity()["name"]
    if stored and stored.strip().lower() != value.strip().lower():
        return (f"NEEDS: is '{value}' their actual name or what they want to be called? Their name is stored as "
                f"{stored}. Ask in a few words. Nothing was saved.")
    return "name", False


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
            value, override = value or content, False
            if key == "name":
                decided = _name_key(value, content)
                if isinstance(decided, str):
                    return decided
                key, override = decided
            changed, previous = memory.set_key(key, value, source="explicit", override=override)
            stored = memory.identity()["name"] if key == "name" else memory.get(key)
            if key == "address_as":
                name = memory.identity()["name"] or USER_NAME
                kept = f" (their name stays {name})" if name and name.lower() != stored.lower() else ""
                return f"OK: {'saved' if changed else 'already stored'}: call them '{stored}'{kept}."
            if not changed and stored != value:
                return f"OK: kept: {PROFILE_KEYS.get(key, key)} = {stored} (they told you this directly). Nothing changed."
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
