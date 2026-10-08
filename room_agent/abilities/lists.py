"""Lists, notes, reminders at a moment ("when I get home"), and the daily review (tools/lists.py, triggers.py)."""

import datetime
import re
import time

from room_agent import runtime as rt
from room_agent.abilities._kit import NO_ARGS, params, tool
from room_agent.actions.core import Group, Risk, register_claim, register_group

LIST_HINTS = re.compile(r"\blists?\b|to-?do|shopping|grocer|\bnotes?\b|jot|write (that|this|it) down|check (it )?off|"
                        r"cross (it )?off|remind me when|when i (get|go|leave|wake|come)|my day|daily review|"
                        r"what did i (do|get done)|recap", re.I)
register_group(Group("lists", LIST_HINTS, lambda: False, "lists and notes",
                     "to-do, shopping or any list; notes; reminders for a moment (home, leaving, bed, PC, morning); "
                     "a review of the day", rules=[
    "- Lists: add / check off / remove items exactly as they said them. 'Remind me when I get home / leave / go to bed / "
    "am back at my PC / in the morning' is remind_me_when (not a timer). A clock time or 'in 20 minutes' is a timer or "
    "alarm instead.",
    "- 'How was my day?' / 'recap my day': call daily_review and give a warm two or three sentence recap, not a list."]))
register_claim("lists", r"\b(added|put|checked off|crossed off|removed|took a note|noted|jotted)\b.{0,40}\b(list|notes?)\b"
                        r"|\bi'?ll remind you when\b")
LIST = {"type": "string", "description": "Which list as they called it: 'to-do' (default), 'shopping', or any name."}


def _l():
    from room_agent.tools import lists

    return lists


def _said(result):
    m = re.search(r'^OK: (added|checked off|removed) "(.+?)" (?:to|from)? ?(?:your )?(.+?)(?: \(|$)', result.message)
    if result.message.startswith("OK: added"):
        m2 = re.search(r'added "(.+?)" to your (.+?) \(', result.message)
        return f"Added {m2.group(1)} to your {m2.group(2)}." if m2 else "Added."
    if result.message.startswith("OK: checked off"):
        return "Checked off."
    if m:
        return "Done."
    return None


def _snap(args, before=None):
    from room_agent.tools import lists

    return {"items": lists._load().get(lists.canonical(args.get("list", "to-do")), [])}


def _grew(args, before, after):
    return len(after["items"]) > len(before["items"]) or "already" in str(after)


tool("add_to_list", "Add an item to one of their lists (to-do by default, shopping, or any list they name).",
     params({"item": {"type": "string"}, "list": LIST}, ["item"]),
     lambda a: _l().add(a["item"], a.get("list", "to-do")), group="lists", claim="lists",
     examples=["add milk to my shopping list", "put call the bank on my to-do list"],
     reflex=[(r"(?:add|put)\s+(?P<item>.{2,60}?)\s+(?:to|on)\s+(?:my|the)\s+(?P<list>[\w -]{2,25}?)\s+list", {})],
     reflex_say=_said, undo=lambda a, b, af: _l().remove(a["item"], a.get("list", "to-do")), observe=_snap,
     undo_if=lambda b, af: len(af["items"]) > len(b["items"]))
tool("show_list", "Read one of their lists (to-do, shopping, notes, or any list). include_done: also what was checked off "
     "this week.", params({"list": LIST, "include_done": {"type": "boolean"}}),
     lambda a: _l().show(a.get("list", "to-do"), bool(a.get("include_done"))), group="lists", changes_state=False,
     reflex=[(r"what'?s\s+on\s+(?:my|the)\s+(?P<list>[\w -]{2,25}?)\s+list", {}),
             (r"(?:read|show)\s+(?:me\s+)?(?:my|the)\s+(?P<list>[\w -]{2,25}?)\s+list", {})])
tool("check_off", "Mark an item on a list as done ('I bought the milk', 'check off call the bank').",
     params({"item": {"type": "string"}, "list": LIST}, ["item"]),
     lambda a: _l().complete(a["item"], a.get("list", "to-do")), group="lists", claim="lists",
     reflex=[(r"(?:check|cross)\s+off\s+(?P<item>.{2,60}?)(?:\s+(?:on|from)\s+(?:my|the)\s+(?P<list>[\w -]{2,25}?)\s+list)?", {})],
     reflex_say=_said)


def _remove(args):
    from room_agent.tools import lists

    name = lists.canonical(args.get("list", "to-do"))
    item = lists._match(lists._load().get(name, []), args["item"])
    out = lists.remove(args["item"], name)
    if out.startswith("OK") and item:
        _removed[0] = (name, item)
    return out


_removed = [None]
tool("remove_from_list", "Take an item off a list (not done, just removed).", params({"item": {"type": "string"}, "list": LIST},
     ["item"]), _remove, group="lists", claim="lists",
     undo=lambda a, b, af: (_l().restore(*_removed[0]), "OK: put it back.")[1] if _removed[0] else "FAILED: nothing to put back.",
     observe=_snap, undo_if=lambda b, af: len(af["items"]) < len(b["items"]))
tool("clear_list", "Empty a whole list (or only the checked-off items). Asks first.",
     params({"list": LIST, "only_done": {"type": "boolean"}}, ["list"]),
     lambda a: _l().clear(a.get("list", "to-do"), bool(a.get("only_done"))), group="lists", claim="lists",
     risk=Risk.SENSITIVE, describe=lambda a: f"empty the {a.get('list', 'to-do')} list")
tool("take_note", "Write down a note for them ('take a note: the wifi password is on the router' - kept on this PC).",
     params({"text": {"type": "string"}}, ["text"]), lambda a: _l().add(a["text"], "notes"), group="lists", claim="lists",
     reflex=[(r"(?:take|make)\s+a\s+note[:,]?\s+(?:that\s+)?(?P<text>.{3,200})", {}),
             (r"(?:note|jot)\s+(?:down\s+)?that\s+(?P<text>.{3,200})", {})],
     reflex_say=lambda r: "Noted." if r.success else None)


# ---------------------------------------------------------------- reminders at a moment
def _t():
    from room_agent import triggers

    return triggers


tool("remind_me_when", "Remind them at a MOMENT rather than a clock time: when they get home, leave, go to bed, are back at "
     "their PC, or in the morning ('remind me to call mom when I get home'). Said once, at that moment.",
     params({"text": {"type": "string", "description": "What to remind them about, in their words"},
             "moment": {"type": "string", "enum": ["home", "leave", "bed", "desk", "morning"]}}, ["text", "moment"]),
     lambda a: _t().add(a["text"], a["moment"]), group="lists", claim="lists",
     examples=["remind me to call mom when I get home", "when I go to bed remind me to charge my phone"])
tool("list_moment_reminders", "The reminders waiting for a moment (home, leaving, bed, PC, morning).", NO_ARGS,
     lambda a: _t().listing(), group="lists", changes_state=False)
tool("cancel_moment_reminder", "Cancel a reminder that's waiting for a moment (by what it's about, the moment, or 'all').",
     params({"what": {"type": "string"}}, ["what"]), lambda a: _t().cancel(a["what"]), group="lists", claim="lists")


# ---------------------------------------------------------------- the day in review
def _review(args):
    """Everything known about today, for a short spoken recap (the model writes it; nothing is invented here)."""
    today = datetime.date.today()
    start = time.mktime(today.timetuple())
    parts = []
    try:
        from room_agent.actions import journal

        done = [e for e in journal.recent(200) if e.get("at", 0) >= start and e.get("state") == "COMPLETED"
                and not e.get("private")]
        if done:
            counts = {}
            for e in done:
                counts[e["action"]] = counts.get(e["action"], 0) + 1
            parts.append("things Jarvis did for them today: " + ", ".join(f"{k.replace('_', ' ')} x{v}" for k, v in counts.items()))
    except Exception:
        pass
    from room_agent.tools import lists

    data = lists._load()
    added = [(k, i["text"]) for k, v in data.items() for i in v if i.get("added", 0) >= start and k != "notes"]
    finished = [(k, i["text"]) for k, v in data.items() for i in v if i.get("done") and i.get("done_at", 0) >= start]
    if finished:
        parts.append("checked off today: " + "; ".join(t for _, t in finished))
    if added:
        parts.append("added to lists today: " + "; ".join(f"{t} ({k})" for k, t in added))
    open_todo = [i["text"] for i in data.get("to-do", []) if not i.get("done")]
    if open_todo:
        parts.append(f"still on the to-do list: {len(open_todo)} (" + "; ".join(open_todo[:5]) + ")")
    try:
        new_facts = [f["content"] for f in rt.memory.facts() if str(f.get("created_at", "")).startswith(today.isoformat())]
        if new_facts:
            parts.append("new things learned about them today: " + "; ".join(new_facts[:5]))
    except Exception:
        pass
    try:
        from room_agent.computer import research

        r = research.latest()
        if r and r.get("at", 0) >= start:
            parts.append(f"researched: {r['question']}")
    except Exception:
        pass
    try:
        from room_agent import triggers

        waiting = triggers._load()
        if waiting:
            parts.append("reminders still waiting: " + "; ".join(f"{triggers.EVENTS[i['event']]}: {i['text']}" for i in waiting))
    except Exception:
        pass
    try:
        from room_agent.integrations import provider

        if provider("google").connection_status() == "connected":
            from room_agent.integrations.capabilities import calendar_get_events

            parts.append("tomorrow's calendar: " + calendar_get_events({"when": "tomorrow"}).split(":", 1)[-1].strip()[:400])
    except Exception:
        pass
    if not parts:
        return "OK: nothing recorded for today yet (no actions, list changes or new memories). Say so briefly."
    return "OK: today so far (" + today.strftime("%A %B %d") + "): " + ". ".join(parts) + "."


tool("daily_review", "A recap of their day: what got done, what's still open, what's coming tomorrow ('how was my day?', "
     "'recap my day').", NO_ARGS, _review, group="lists", changes_state=False,
     reflex=[], examples=["how was my day", "recap my day", "what did I get done today"])
