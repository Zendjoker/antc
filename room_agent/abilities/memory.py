"""Persistent memory: remember, recall, forget (implementation: room_agent/memory + tools/memory_tools.py)."""

import time

from room_agent import runtime as rt
from room_agent.abilities._kit import CONFIDENCE, params, tool
from room_agent.actions.core import Group, Risk, register_claim, register_context, register_group, register_line
from room_agent.config import USER_NAME, WEATHER_LOCATION
from room_agent.memory import CATEGORIES, PROFILE_KEYS


def _detail():
    m = rt.memory
    if m.available:
        return (f"remembers things about {USER_NAME} across restarts in a local database ({m.count()} items stored); "
                "can remember, update (a new value replaces the old), forget (delete) and recall")
    return f"the memory database can't be opened ({m.error}), so nothing can be saved"


register_line("conversation memory (this session)", "this conversation")
register_group(Group("memory", title="persistent memory", summary=_detail, available=lambda: rt.memory.available))
register_claim("memory_save", r"\b(i'?ve|i have|i)\s+(just\s+|now\s+)?(saved|stored|noted|memorized|written down|wrote (it|that) down)\b"
                              r"|\b(it'?s|that'?s|this is)\s+(saved|stored|noted)\b|^(saved|stored|noted)\b|\bi'?ll remember\b|\bi will remember\b"
                              r"|\bi'?ll keep (that|it|this) in mind\b|\bwon'?t forget\b|\bmade a note\b|\bin my memory now\b|\bgot it saved\b"
                              r"|\b(saved|stored|noted)\s+(it|that|this|those|them)\b|(^|[,.!]\s*)(saved|stored|noted)\s*[.!]?\s*$"
                              r"|\b(consider it|it'?s|that'?s)\s+(remembered|memorized)\b|\b(put|added|wrote|written)\s+(it|that|this)\s+"
                              r"(in|into|to|down in)\s+(my\s+)?(notes|memory)\b|\bin my notes\b|\blocked (it|that) in\b|\bcommitted (it|that) to memory\b")
register_claim("memory_forget", r"\b(i'?ve|i have|i)\s+(just\s+)?(forgot(ten)?|erased|deleted|wiped|removed|cleared)\b"
                                r"|\b(it'?s|that'?s|everything'?s|everything is)\s+(gone|forgotten|erased|deleted|wiped)\b|^(forgotten|erased|deleted|wiped)\b")


def _detected():
    from room_agent.tools import location

    return bool(location.enabled() and location.locate())


_expired = {"at": 0.0}


def _context(user_text):
    memory = rt.memory
    if not memory.available:
        return ["- persistent_memory: UNAVAILABLE. You can't save or look up anything across restarts right now; say so if "
                "asked."]
    profile = memory.profile()
    known = "; ".join(f"{PROFILE_KEYS.get(k, k.replace('_', ' '))}: {v}" for k, v in profile.items())
    lines = [f"- persistent_memory: on, {memory.count()} items stored about {USER_NAME} (use recall to look them up, remember "
             "to add)",
             f"- known_user_context: {known or 'nothing stored yet (not even their city)'}"]
    if not profile.get("home_location") and WEATHER_LOCATION:
        lines.append(f"- default weather location from settings: {WEATHER_LOCATION}")
    elif not memory.home_location() and not WEATHER_LOCATION and not _detected():
        said = f" They said {rt.session_location} earlier in this conversation (get_weather uses it)." if rt.session_location else ""
        lines.append("- their_location: unknown. You can't detect where they are (no device, IP or GPS location), so never "
                     "imply you know it." + (said or " If a city is needed, ask once which city."))
    if time.time() - _expired["at"] > 3600:  # (dated plans that have passed stop being active, checked hourly)
        _expired["at"] = time.time()
        memory.expire_plans()
    rel = memory.relevant(user_text)
    lines.append("- relevant_persistent_memories (for this request): "
                 + ("; ".join(f"{f['content']} (noted {f['updated_at'][:10]})" for f in rel) if rel else "none matched"))
    sums = [x for x in memory.summaries(3) if "SKIP" not in x["summary"]][-2:]  # (SKIP: nothing worth summarizing)
    if sums:
        lines.append("- previous_conversations: " + "; ".join(f"{x['created_at']}: {x['summary'][:200]}" for x in sums))
    return lines


register_context(_context, order=10)


def _remember(args):
    from room_agent.tools.memory_tools import remember

    return remember(args)


def _recall(args):
    from room_agent.tools.memory_tools import recall

    return recall(args.get("query", ""))


def _forget(args):
    from room_agent.tools.memory_tools import forget

    return forget(args["text"])


available = dict(group="memory")
tool("remember", f"Write something to persistent memory now, because {USER_NAME} asked you to remember it (or corrected "
     "something stored). Returns OK only after the database confirms it. For core facts set `key` (a new value replaces the "
     "old one).",
     params({"content": {"type": "string", "description": "The fact, short, third person, real dates, e.g. 'Dentist "
                                                          "appointment on Friday, October 9, 2026'"},
             "key": {"type": "string", "enum": list(PROFILE_KEYS),
                     "description": "Only for core facts: " + ", ".join(f"{k} = {v}" for k, v in PROFILE_KEYS.items())},
             "value": {"type": "string", "description": "The value for `key`, e.g. 'San Francisco, CA' or 'metric'"},
             "category": {"type": "string", "enum": list(CATEGORIES)}}),
     _remember, claim="memory_save", **available,
     # (their own words must ask: a web page, email or file can't plant a "memory")
     intent=__import__("re").compile(r"remember|don'?t forget|keep in mind|note (that|this)|save (that|this)|store|"
                                     r"my name is|call me|i live|i'?m (from|in)|i prefer|i like|i love|i hate|i don'?t like|"
                                     r"actually|correct|not true|wrong|that'?s (right|it)|yes|yeah", __import__("re").I))
tool("recall", f"Look up what's stored in persistent memory about {USER_NAME}: give a topic (e.g. 'sister', 'where I live', "
     "'food') or leave empty for everything. Use it before saying you don't know or don't remember something.",
     params({"query": {"type": "string"}}), _recall, changes_state=False, **available)
tool("forget", "Delete stored memories that mention this (e.g. 'where I live', 'Sara'). Use 'everything' only if they ask you "
     "to forget everything. Returns exactly what was deleted.",
     params({"text": {"type": "string", "description": "What to forget, e.g. 'where I live', or 'everything'"},
             "confidence": CONFIDENCE}, ["text"]),
     _forget, claim="memory_forget", risk=Risk.CONFIRM, min_confidence=0.8, **available)
