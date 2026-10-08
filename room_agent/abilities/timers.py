"""Timers, alarms and reminders (implementation: tools/timers.py)."""

import re
import time

from room_agent import config
from room_agent import runtime as rt
from room_agent.abilities._kit import CONFIDENCE, FOLLOW_UP_S, NO_ARGS, params, tool
from room_agent.actions.core import Group, Risk, register_claim, register_context, register_group, register_line


def _live():
    from room_agent.tools.timers import list_timers

    return (not list_timers().startswith("OK: no timers") or bool(rt.ringing)
            or bool(rt.last_ring and time.time() - rt.last_ring["at"] < FOLLOW_UP_S))


register_group(Group(
    "timers", re.compile(r"timer|alarm|remind|wake|snooze|countdown|ring|cancel|minute|second|hour|o'?clock|\d|tomorrow|"
                         r"tonight|morning|later|stop it|turn it off", re.I), _live,
    title="countdown timers", summary=f"{config.TIMER_MIN_S} second to {config.TIMER_MAX_S // 86400} days, no other limit; "
                                      "rings until the user speaks",
    rules=["- A timer can be any length from 1 second up. \"Set a timer for 5 seconds\" or \"10 seconds\" or \"remind me in 30 "
           "seconds\" is just a countdown timer: call set_timer right away. Anything phrased as an alarm, reminder or wake-up "
           "\"in N seconds/minutes/hours\" is also set_timer; set_alarm is only for a clock time like 7:30. A label is "
           "optional, so never ask for one and never refuse. Never claim a minimum length or any limit the tool doesn't "
           "state, even if earlier messages in this conversation said so (they were wrong).",
           "- For a clock time (\"wake me at 7:30\", \"remind me at 5 to call mom\", \"every morning at 8\"), use set_alarm "
           "with a 24-hour time, and daily=true if they say every day. Work out the time and date from the current time in "
           "the runtime context. Those survive restarts.",
           "- When you set a timer or alarm, give it a natural `message` to say when it rings, like a friend would (\"Hey, "
           "your pasta's ready!\"). For wake-up calls, set a message like \"Hey, time to wake up!\". Every timer and alarm "
           "keeps ringing until they say something, so when you confirm one, mention they can just say anything to stop "
           "it. Use ring_once only if they ask for a single ping."]))
register_line("alarms and reminders at a clock time", "optional date or every day; rings until the user speaks")
register_line("snoozing an alarm", "a new timer with the same label and message")
register_claim("timer", r"\btimer\b.{0,30}\b(is\s+)?(set|started|running|going|on|ticking)\b|\b(set|started)\b.{0,30}\btimer\b"
                        r"|\btimer'?s\s+(set|on|running|going|ticking)\b|\b(cancel+ed|stopped|killed)\b.{0,30}\btimers?\b"
                        r"|\bi'?ll (remind|ping|alert|buzz|wake) you\b|\bi will (remind|ping|alert|wake) you\b"
                        r"|\b(alarm|reminder)(?:'s|\s+is)?\s+(?:all\s+)?(set|added|created|scheduled|saved)\b"
                        r"|\b(set|scheduled)\b.{0,30}\b(reminder|alarm)\b")


def _context(user_text):
    from room_agent.tools.timers import list_timers

    lines = ["- timers_and_alarms_now (live from code; this overrides anything said earlier in the conversation): "
             + list_timers().removeprefix("OK: ") + ". A request to set one now is a NEW one: call the tool, even if "
             "something similar was set before or is listed here; never say it's already set."]
    if _just_set():  # (only right after one was set: the only time a correction is possible; saves tokens otherwise)
        lines.append("- If they're CORRECTING the timer/alarm just set (\"no, I said 3\", \"I meant 7:30\"), call set_timer / "
                     "set_alarm once with the corrected time: the code replaces the old one, so don't cancel it yourself.")
    if rt.last_ring and time.time() - rt.last_ring["at"] < 300:
        r = rt.last_ring
        how = "rang and was stopped when they spoke" if r.get("stopped") else "rang"
        nxt = _next_of(r["label"])
        if nxt:
            how += (f"; the '{r['label']}' listed above is its NEXT occurrence ({nxt}), a different one that is not "
                    "ringing; leave it alone unless they explicitly ask to cancel that one")
        lines.append(f"- just_rang: the {r['kind']} '{r['label']}' ({r['message']!r}) {how}. It is FINISHED: not running, not "
                     "counting down, nothing left to stop or cancel, so never offer to. "
                     + ("Their message is a reaction to it: reply naturally and briefly, never <silent>. " if r.get("stopped") else "")
                     + "If they ask to snooze it or be reminded again later, call set_timer for the time they say with the same "
                     "label and message.")
    return lines


register_context(_context, order=30)


# ---------------------------------------------------------------- checks and undo
def _said_set(result):
    """Spoken after a timer / alarm is set, from the item that was really created (verified), not from the request."""
    from room_agent.tools import timers

    new = set((result.state_after or {}).get("ids", [])) - set((result.state_before or {}).get("ids", []))
    with timers._lock:
        items = [timers._items[i] for i in new if i in timers._items]
    if len(items) != 1:
        return None
    item = items[0]
    if item["kind"] == "timer":
        what = "" if item["label"] == "timer" else f" for {item['label']}"
        return f"Okay, {timers._left(item['due'] - time.time())}{what}, starting now."
    return f"Alarm's set for {timers._when(item['due'])}" + (", every day." if item.get("daily") else ".")


def _ids(args, before=None):
    from room_agent.tools import timers

    with timers._lock:
        return {"ids": sorted(timers._items)}


def _undo(args, before, after):
    from room_agent.tools import timers

    new = [i for i in after["ids"] if i not in before["ids"]]
    if not new:
        return "FAILED: couldn't tell which timer or alarm was just set, so nothing was cancelled."
    labels = [timers.cancel_id(i) for i in new]
    if not all(labels):
        return "FAILED: it already rang or was cancelled, so there's nothing to undo."
    return f"OK: cancelled the {', '.join(labels)} {'timer or alarm' if len(labels) == 1 else 'timers and alarms'} I just set."


def _set(args, before, after):
    """A new id appeared (a corrected one replaces the old, so the count can stay the same)."""
    return bool(set(after["ids"]) - set(before["ids"]))


# "no, I said 3" / "I meant 7:30" / "make it 10 minutes": they're fixing the one just set, not asking for another
CORRECTING = re.compile(r"^\s*(?:(?:no|nope|nah|sorry|wait|oops|actually)\b[\s,.!]*)*"
                        r"(?:i\s+(?:said|meant|asked\s+for)|make\s+(?:it|that)|change\s+(?:it|that)\s+to|it\s+should\s+be)\b"
                        r"|^\s*(?:no|nope|nah)\b[\s,.!]+(?:it'?s\s+|it\s+was\s+)?\d", re.I)
CORRECT_WITHIN_S = 180


def _just_set():
    from room_agent.actions.context import env

    last = env.last_successful_action
    return last is not None and last.capability in ("set_timer", "set_alarm") and time.time() - last.at <= CORRECT_WITHIN_S


def _corrected_ids():
    """The timer/alarm ids the previous request created, when this turn corrects it (a few minutes, a set that worked)."""
    from room_agent.actions.context import env

    last = env.last_successful_action
    if (not CORRECTING.search(rt.turn_text or "") or last is None or last.capability not in ("set_timer", "set_alarm")
            or time.time() - last.at > CORRECT_WITHIN_S or last.at >= rt.turn.started):  # (set earlier in THIS turn: not a fix)
        return []
    return sorted(set((last.state_after or {}).get("ids", [])) - set((last.state_before or {}).get("ids", [])))


def _replacing(set_it):
    """Set the corrected one first; only once it's really set, cancel the one it replaces (never left with none)."""
    def run(args):
        from room_agent.tools import timers

        old = _corrected_ids()
        out = set_it(args)
        if out.startswith("OK") and old:
            gone = [label for label in (timers.cancel_id(i) for i in old) if label]
            if gone:
                out += f" (Replaced the {', '.join(gone)} they just corrected: only the new one is running.)"
        return out
    return run


def _set_timer(args):
    from room_agent.tools.timers import set_timer

    return set_timer(int(args["seconds"]), args.get("label", ""), args.get("message", ""), bool(args.get("ring_once")))


def _set_alarm(args):
    from room_agent.tools.timers import set_alarm

    return set_alarm(args["time"], args.get("label", ""), args.get("message", ""), bool(args.get("ring_once")),
                     bool(args.get("daily")), args.get("date", ""))


def _list(args):
    from room_agent.tools.timers import list_timers

    return list_timers()


def _next_of(label):
    """When the alarm that just rang is a daily one, its next occurrence (re-armed for tomorrow): 'tomorrow at 7:00 AM'."""
    from room_agent.tools import timers

    with timers._lock:
        due = [i["due"] for i in timers._items.values() if i["label"] == label]
    return f"at {timers._when(min(due))}" if due else ""


# words that really ask to cancel something (vs. "stop" / "okay I'm up", which only stop the ringing)
EXPLICIT_CANCEL = re.compile(r"\b(cancel|delete|remove|clear|get rid of|turn off|disable|tomorrow|tonight|every|daily|all|"
                             r"for good|the (other|next)|at \d|\d\s*(am|pm)|\d+:\d\d)\b", re.I)


def _cancel(args):
    from room_agent.tools.timers import cancel_timer

    r, said = rt.last_ring, rt.turn_text or ""
    if (r and r.get("stopped") and time.time() - r["at"] < 120 and not EXPLICIT_CANCEL.search(said)
            and (args["label"].lower().strip() in ("all", "it", "that", "alarm", "timer") or args["label"].lower() in r["label"].lower()
                 or r["label"].lower() in args["label"].lower())):
        nxt = _next_of(r["label"])
        return (f"OK: nothing to stop: the {r['kind']} '{r['label']}' already stopped ringing when they spoke, and nothing "
                "else was cancelled." + (f" Its next one ({nxt}) is still set." if nxt else ""))
    return cancel_timer(args["label"])


tool("set_timer", "Anything that should go off after a length of time from now, whatever they call it: a timer, an alarm, "
     "a wake-up or a reminder (\"5 seconds\", \"alarm in 30 seconds\", \"wake me in 10 minutes\"). Any length from 1 second "
     "up. Set it immediately, then confirm; don't ask for a label first. When it rings it says your `message`.",
     params({"seconds": {"type": "integer", "minimum": config.TIMER_MIN_S, "maximum": config.TIMER_MAX_S,
                         "description": "Whole seconds, at least 1. 5 seconds = 5, 2 minutes = 120."},
             "label": {"type": "string", "description": "Optional: what it's for, e.g. 'pasta' or 'call mom'. Leave out if "
                                                        "they didn't say."},
             "message": {"type": "string", "description": "What you say out loud when it rings, in your own casual words, "
                                                          "e.g. 'Hey, the pasta's ready!' or 'Hey, time to wake up!'"},
             "ring_once": {"type": "boolean", "description": "Only if they want a single ping. By default it keeps ringing "
                                                             "every few seconds until they say something."}}, ["seconds"]),
     _replacing(_set_timer), group="timers", claim="timer", event="timer.set", observe=_ids, verify=_set, undo=_undo,
     reflex_say=_said_set)
tool("set_alarm", "Set an alarm or reminder for a clock time, e.g. 'wake me at 7:30' or 'remind me at 5pm to call mom'. For a "
     "length of time from now ('alarm in 10 seconds'), use set_timer instead. Rings at the next time it's that time, unless "
     "a date is given. Survives restarts. When it rings it says your `message`. Use the current time in the runtime context "
     "to get the time and date right.",
     params({"time": {"type": "string", "description": "24-hour HH:MM, e.g. '07:30' or '17:00'"},
             "date": {"type": "string", "description": "Only if they name a day other than the next one: YYYY-MM-DD"},
             "label": {"type": "string", "description": "Optional: what it's for, e.g. 'wake up' or 'call mom'"},
             "message": {"type": "string", "description": "What you say out loud when it rings, in your own casual words"},
             "ring_once": {"type": "boolean", "description": "Only if they want a single ping. By default it keeps ringing "
                                                             "until they say something"},
             "daily": {"type": "boolean", "description": "True if they want it every day"}}, ["time"]),
     _replacing(_set_alarm), group="timers", claim="timer", event="alarm.set", observe=_ids, verify=_set, undo=_undo,
     reflex_say=_said_set)
tool("list_timers", "List running timers and set alarms with time remaining.", NO_ARGS, _list, group="timers",
     changes_state=False, claim="timer")
tool("cancel_timer", "Cancel a running timer or an alarm by its label (or 'all').",
     params({"label": {"type": "string", "description": "The label of the timer or alarm, or 'all'"},
             "confidence": CONFIDENCE}, ["label"]),
     _cancel, group="timers", claim="timer", event="timer.cancelled", risk=Risk.CONFIRM, min_confidence=0.7)
