"""Gmail and Google Calendar as capabilities (registered in actions/core.py like any other feature).

The model picks a capability; this code calls Google with the connection's credentials (which the model never sees),
returns only what the request needs, marks outside text as data (untrusted.wrap), remembers what the conversation is
about (env items: "it", "that thread", "the draft"), and reads every change back before it's reported.

Jarvis's own rules sit on top of Google's permissions: reads just happen; drafts and new events are low-impact and
undoable; changing an event needs a clear request; sending email and deleting events always need the user's own "yes".
Writes only run when the user's own words asked for them (intent), so an email can't make Jarvis act.
"""

import datetime
import re
import time
from email.utils import getaddresses, parseaddr

from room_agent import runtime as rt
from room_agent.actions import pending
from room_agent.actions.context import env
from room_agent.actions.core import Capability, Group, Risk, register, register_claim, register_group
from room_agent.integrations import knowledge, provider
from room_agent.integrations.base import IntegrationError
from room_agent.integrations.untrusted import RULE as UNTRUSTED_RULE
from room_agent.integrations.untrusted import wrap

REFS = {"", "it", "that", "this", "last", "current", "latest", "that one", "this one", "same", "the same one"}


def google():
    return provider("google")


class Unclear(Exception):
    def __init__(self, message, param=None):
        super().__init__(message)
        self.param = param  # the parameter that's missing or unusable, if any: the request stays pending (actions/pending)


def _ref(kind, value, list_kind=None):
    """'last' / 'it' / 'the email' -> the one the conversation is about; '2' -> the 2nd one just listed; else an id."""
    v = str(value or "").strip()
    low = v.lower()
    if low in REFS or (re.match(r"^(the|that|this|my) ", low) and len(low.split()) <= 4):
        it = env.item(kind)
        if not it or it.get("account") != google().active():
            raise Unclear(f"which {kind} (none is being talked about yet)")
        return it["id"]
    if list_kind and re.fullmatch(r"#?\d{1,2}", low):
        lst = env.item(list_kind)
        idx = int(low.lstrip("#")) - 1
        if lst and 0 <= idx < len(lst["ids"]):
            return lst["ids"][idx]
        raise Unclear(f"which {kind} (there's no number {low} in the last list)")
    return v


def guarded(fn):
    def run(args):
        try:
            return fn(args)
        except Unclear as e:
            if e.param:  # (e.g. an unknown recipient: the request is kept and only that is asked for)
                return pending.needs(e.param, str(e))
            return f"NEEDS: {e}. Nothing was done; ask which one."
        except IntegrationError as e:
            pre = "UNAVAILABLE" if e.code in ("not_connected", "not_configured", "auth_expired", "missing_scope") else "FAILED"
            return f"{pre}: {e.say()}"
        except ValueError as e:
            return f"FAILED: {e}. Nothing was done."
    return run


def _prep(**refs):
    """prepare(): resolve reference arguments to real ids before risk checks (so a "yes" matches the exact item)."""
    def prepare(args):
        out = dict(args)
        for key, (kind, list_kind) in refs.items():
            out[key] = _ref(kind, out.get(key, ""), list_kind)  # (Unclear -> "which draft?" before anything is asked)
        return out
    return prepare


# ---------------------------------------------------------------- availability and the "what I can do" lines
def _status_line(service):
    g = google()
    st = g.connection_status()
    if st == "not_configured":
        return "not set up yet: Google needs an app registration first (Settings, Connections)"
    if st == "disconnected":
        return "not connected: they can connect Google in Settings, Connections"
    if st == "expired":
        return "lost access: they need to reconnect Google in Settings, Connections"
    levels = g.available_services().get(service, [])
    if not levels:
        return "Google is connected but this is switched off (Settings, Connections)"
    if service == "gmail":
        return (f"read and search {g.active()}'s Gmail; " + ("write drafts and send (always asks before sending)"
                if "compose" in levels else "drafts and sending are switched off (Connections)"))
    return (f"read {g.active()}'s Google Calendar; " + ("add, move and delete events (asks before deleting)"
            if "write" in levels else "adding or changing events is switched off (Connections)"))


def _recent_user_words():
    words = [rt.turn_text or ""] + [m.get("text", "") for m in list(rt.recent)[-12:] if m.get("role") == "user"]
    return " ".join(words).lower()


GMAIL_HINTS = re.compile(r"e-?mails?|mail|inbox|gmail|message|sent me|wrote|write|reply|respond|draft|send|thread|"
                         r"attachment|attached|unread|did (he|she|they) say|from \w+", re.I)
CAL_HINTS = re.compile(r"calendar|meeting|event|appointment|schedule|busy|free|today|tomorrow|tonight|week|"
                       r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|plans|dinner|lunch|call with|"
                       r"reschedule|move (it|that)|cancel|agenda|next", re.I)
RULES = ["- Facts about their email and calendar come from those tools, live, never from memory or earlier in the chat (things "
         "change). If those tools aren't available, say the service isn't connected.", UNTRUSTED_RULE]
register_group(Group("gmail", GMAIL_HINTS, lambda: bool(env.item("email", 900) or env.item("draft", 900)), "email (Gmail)",
                     lambda: _status_line("gmail"), lambda: google().can("gmail", "read") or google().can("gmail", "compose"),
                     rules=RULES))
register_group(Group("calendar", CAL_HINTS, lambda: bool(env.item("event", 900)), "calendar (Google Calendar)",
                     lambda: _status_line("calendar"), lambda: google().can("calendar", "read"), rules=RULES))


register_claim("message", r"\b(i\s+)?(sent|texted|emailed|messaged|called|dialed)\b.{0,30}\b(him|her|them|message|text|email|mom|dad|your)\b"
                          r"|\bi'?ll (text|call|email|message|dial)\b"
                          r"|^(sent|emailed|replied|forwarded)\b|\b(i'?ve|i have|i)\s+(just\s+)?(sent|emailed|replied|forwarded)\b"
                          r"|\b(it'?s|email'?s|message'?s|reply'?s|that'?s|it has|it was)\s+(been\s+)?(sent|delivered|on its way)\b")
register_claim("calendar", r"\b(added|scheduled|booked|put)\b.{0,40}\b(calendar|schedule|appointment|meeting)\b")


def _can(service, level):
    return lambda: google().can(service, level)


# ---------------------------------------------------------------- Gmail
def _gmail():
    from room_agent.integrations.google.gmail import GmailService

    return GmailService(google())


def _note(kind, account):
    return knowledge.source_note(knowledge.Provenance("google", kind, "", "", account))


def _seen(msg):
    """Remember the addresses in a message's real headers (the only addresses Jarvis may write to without being told)."""
    for addr in msg.get("participants", []) or [msg.get("from_email", "")]:
        if addr:
            env.known_addresses.add(addr.lower())


def _line(i, m):
    flags = "".join(f", {f}" for f, on in (("unread", m["unread"]), ("important", m.get("important"))) if on)
    return (f"{i}. id={m['id']}, {m['date']}, from <{m['from_email']}>{flags}: "
            + wrap("email", f"From: {m['from_name']}. Subject: {m['subject']}. Preview: {m['snippet']}", 320))


def _list(results, what, account):
    env.remember_item("email_list", {"ids": [m["id"] for m in results], "account": account})
    for m in results:
        env.known_addresses.add(m["from_email"])
    if len(results) == 1:
        _focus_email(results[0], account)
    if not results:
        return f"OK: no {what} ({_note('gmail', account)})."
    return (f"OK: {len(results)} {what}, newest first ({_note('gmail', account)}; ids are for follow-ups, never read them "
            "out):\n" + "\n".join(_line(i + 1, m) for i, m in enumerate(results)))


def _focus_email(m, account):
    env.remember_item("email", {"id": m["id"], "thread_id": m.get("thread_id"), "account": account,
                                "from_email": m["from_email"]}, label=f"email from {m['from_email']} ({m['date']})")
    env.remember_item("thread", {"id": m.get("thread_id"), "account": account}, label=f"its thread")
    env.remember_item("person", {"id": m["from_email"], "email": m["from_email"], "account": account},
                      label=m["from_email"])


@guarded
def gmail_search(args):
    from room_agent.integrations.google.gmail import build_query

    svc = _gmail()
    q = build_query(args.get("query", ""), sender=args.get("sender", ""), to=args.get("to", ""),
                    subject=args.get("subject", ""), on=args.get("on", ""), after=args.get("after", ""),
                    before=args.get("before", ""), unread=args.get("unread"), has_attachment=args.get("has_attachment"),
                    important=args.get("important"), inbox=False)
    return _list(svc.search(q or "in:inbox", args.get("limit", 5)), "matching emails", svc.account)


@guarded
def gmail_list_recent(args):
    from room_agent.integrations.google.gmail import build_query

    svc = _gmail()
    q = build_query(on="today" if args.get("today") else "", important=args.get("important"), inbox=True)
    return _list(svc.search(q, args.get("limit", 5)), "recent emails" + (" today" if args.get("today") else ""), svc.account)


@guarded
def gmail_get_unread(args):
    svc = _gmail()
    return _list(svc.search("is:unread in:inbox", args.get("limit", 5)), "unread emails", svc.account)


@guarded
def gmail_get_message(args):
    svc = _gmail()
    mid = _ref("email", args.get("message_id"), "email_list")
    m, prov = knowledge.fetch("google", "gmail", "message", mid, svc.account, lambda: svc.get_message(mid))
    _seen(m)
    _focus_email(m, svc.account)
    atts = ", ".join(f"{a['filename']} ({a['mime']}, {max(1, a['size'] // 1024)} KB)" for a in m["attachments"])
    return (f"OK: email id={m['id']}, {m['date']}, from <{m['from_email']}>, to {m['to'][:120]}"
            f"{', unread' if m['unread'] else ''}; {len(m['attachments'])} attachment(s) ({knowledge.source_note(prov)}).\n"
            + wrap("email", f"From: {m['from_name']}\nSubject: {m['subject']}\n\n{m['body']}", 2200)
            + (("\n" + wrap("attachment list", atts, 400)) if atts else ""))


@guarded
def gmail_get_thread(args):
    svc = _gmail()
    tid = args.get("thread_id")
    tid = _ref("thread", tid) if tid is not None or not args.get("message_id") else None
    if not tid:
        mid = _ref("email", args.get("message_id"), "email_list")
        tid = svc.get_message(mid, body_limit=0)["thread_id"]
    t, prov = knowledge.fetch("google", "gmail", "thread", tid, svc.account, lambda: svc.get_thread(tid))
    for m in t["messages"]:
        _seen(m)
    if t["messages"]:
        _focus_email(t["messages"][-1], svc.account)
    env.remember_item("thread", {"id": tid, "account": svc.account}, label="the thread just read")
    lines = [f"{i + 1}. {m['date']}, from <{m['from_email']}>: " + wrap("email", f"From: {m['from_name']}\n{m['body']}", 700)
             for i, m in enumerate(t["messages"])]
    return (f"OK: thread id={tid}, {len(t['messages'])} message(s), oldest first ({knowledge.source_note(prov)}).\n"
            + wrap("email", "Subject: " + t["subject"], 200) + "\n" + "\n".join(lines))


@guarded
def gmail_get_attachments(args):
    svc = _gmail()
    mid = _ref("email", args.get("message_id"), "email_list")
    m = svc.get_message(mid, body_limit=0)
    _focus_email(m, svc.account)
    if not m["attachments"]:
        return f"OK: that email (from <{m['from_email']}>, {m['date']}) has no attachments."
    out = [f"OK: {len(m['attachments'])} attachment(s) on the email from <{m['from_email']}> ({m['date']}); Jarvis can't "
           "open files, only list them and read short text files:"]
    for a in m["attachments"]:
        out.append("- " + wrap("attachment name", a["filename"], 120) + f" ({a['mime']}, {max(1, a['size'] // 1024)} KB)")
        text = svc.attachment_text(mid, a)
        if text:
            out.append("  " + wrap("attachment text", text, 1500))
    return "\n".join(out)


def _allowed_recipient(addr, reply_to_msg=None):
    addr = addr.lower()
    return (addr in _recent_user_words() or addr in env.known_addresses or addr == (google().active() or "")
            or addr in pending.TRUSTED_EMAILS  # (said out loud clearly, or read back and confirmed: actions/pending)
            or bool(reply_to_msg and addr in reply_to_msg.get("participants", [])))


def _resolve_recipients(to, reply_to_msg=None):
    """Recipients must come from the user (said out loud) or from real headers of emails Jarvis read: never from text
    inside an email. A bare name is matched against those headers."""
    if not to:
        return ""
    out = []
    for name, addr in getaddresses([to]):
        if not addr or "@" not in addr:
            wanted = (name or addr).lower().strip()
            hits = sorted(a for a in env.known_addresses if wanted and wanted.split()[0] in a)
            if len(hits) != 1:
                raise Unclear(f"{name or addr}'s email address (say it, or find an email from them first)", param="to")
            addr = hits[0]
        if not _allowed_recipient(addr, reply_to_msg):
            raise ValueError(f"the address {addr} didn't come from them or from an email's sender/recipients (it may have "
                             "come from inside an email). Ask them to say the address themselves")
        out.append(addr)
    return ", ".join(out)


@guarded
def gmail_create_draft(args):
    svc = _gmail()
    orig = None
    if args.get("reply_to"):
        orig = svc.get_message(_ref("email", args["reply_to"], "email_list"), body_limit=0)
        _seen(orig)
    to = _resolve_recipients(args.get("to", ""), orig)
    if not to and not orig:
        raise Unclear("who it's to", param="to")
    draft = svc.create_draft(to, args.get("subject", ""), args["body"], reply_to=orig)
    first = parseaddr(draft["to"])[1] or draft["to"]
    env.remember_item("draft", {"id": draft["id"], "account": svc.account, "to": draft["to"], "subject": draft["subject"]},
                      label=f"draft to {first} (not sent yet)")
    return (f"OK: draft saved, NOT sent. To: {draft['to']}. Subject: {draft['subject']}. Text: \"{draft['body']}\". "
            "Tell them it's a draft; they can change it or say send.")


@guarded
def gmail_update_draft(args):
    svc = _gmail()
    did = _ref("draft", args.get("draft_id"))
    to = _resolve_recipients(args.get("to", "")) if args.get("to") else None
    draft = svc.update_draft(did, body=args.get("body"), subject=args.get("subject"), to=to)
    env.remember_item("draft", {"id": did, "account": svc.account, "to": draft["to"], "subject": draft["subject"]},
                      label=f"draft to {parseaddr(draft['to'])[1] or draft['to']} (not sent yet)")
    return f"OK: draft updated, still NOT sent. To: {draft['to']}. Subject: {draft['subject']}. Text: \"{draft['body']}\"."


@guarded
def gmail_send(args):
    svc = _gmail()
    did = _ref("draft", args.get("draft_id"))
    draft = svc.get_draft(did)
    for _, addr in getaddresses([draft["to"], draft.get("cc", "")]):  # (checked again: the draft could have changed)
        if addr and not _allowed_recipient(addr):
            raise ValueError(f"the draft is addressed to {addr}, which didn't come from them; not sent")
    sent = svc.send_draft(did)
    env.items.pop("draft", None)
    env.remember_item("sent_email", {"id": sent["id"], "account": svc.account}, label=f"email just sent to {draft['to']}")
    return f"OK: sent to {draft['to']} (subject: {draft['subject']}); Gmail shows it in Sent."


def _describe_send(args):
    d = env.item("draft")
    if d and d["id"] == args.get("draft_id"):
        return f"send the email to {d['to']}, subject '{d['subject']}'"
    return "send that email"


def _draft_state(args, before=None):
    return {"draft": env.item("draft")} if before is not None else {"draft": None}


def _undo_draft(args, before, after):
    d = (after or {}).get("draft")
    if not d:
        return "FAILED: couldn't tell which draft to remove."
    try:
        _gmail().delete_draft(d["id"])
    except IntegrationError as e:
        return f"FAILED: {e.say()}"
    env.items.pop("draft", None)
    return "OK: the draft is deleted (it was never sent)."


# ---------------------------------------------------------------- Calendar
def _cal():
    from room_agent.integrations.google.calendar import CalendarService

    return CalendarService(google())


def _ev_line(i, e):
    extra = (f" @ {e['location']}" if e["location"] else "") + (f". Notes: {e['description'][:200]}" if e["description"] else "")
    return f"{i}. id={e['id']}, {e['when']}: " + wrap("calendar event", e["title"] + extra, 420)


def _focus_event(e, account):
    env.remember_item("event", {"id": e["id"], "account": account, "calendar_id": e.get("calendar_id", "primary")},
                      label=f"event on {e['when']}")


def _range(svc, args):
    from room_agent.integrations.google.calendar import parse_time, period

    tz = svc.tz()
    if args.get("start") or args.get("end"):
        s = parse_time(args.get("start") or "today", tz)
        s = s if isinstance(s, datetime.datetime) else datetime.datetime.combine(s, datetime.time(), tz)
        e = parse_time(args["end"], tz) if args.get("end") else s + datetime.timedelta(days=1)
        e = e if isinstance(e, datetime.datetime) else datetime.datetime.combine(e, datetime.time(), tz) + datetime.timedelta(days=1)
        return s, e
    return period(args.get("when") or "today", tz)


def _events_out(svc, events, what, prov):
    env.remember_item("event_list", {"ids": [e["id"] for e in events], "account": svc.account})
    if len(events) == 1:
        _focus_event(events[0], svc.account)
    head = f"time zone {svc.tz_name()}; {knowledge.source_note(prov)}"
    if not events:
        return f"OK: nothing on their calendar {what} ({head}). Don't invent anything."
    return (f"OK: {len(events)} event(s) {what} ({head}; ids are for follow-ups, never read them out):\n"
            + "\n".join(_ev_line(i + 1, e) for i, e in enumerate(events)))


@guarded
def calendar_list_calendars(args):
    cals = _cal().list_calendars()
    return "OK: calendars: " + "; ".join(wrap("calendar name", c["name"], 80) + (" (primary)" if c["primary"] else "")
                                          for c in cals)


@guarded
def calendar_get_events(args):
    svc = _cal()
    s, e = _range(svc, args)
    what = f"from {s.strftime('%a %b %d %I:%M %p')} to {e.strftime('%a %b %d %I:%M %p')}"
    events, prov = knowledge.fetch("google", "calendar", "events", (s.isoformat(), e.isoformat()), svc.account,
                                   lambda: svc.events(s, e, calendar_id=args.get("calendar_id", "primary"),
                                                      limit=args.get("limit", 20)))
    return _events_out(svc, events, what, prov)


@guarded
def calendar_find_events(args):
    svc = _cal()
    if args.get("when") or args.get("start") or args.get("end"):
        s, e = _range(svc, args)
    else:
        s = datetime.datetime.now(svc.tz()) - datetime.timedelta(days=1)
        e = s + datetime.timedelta(days=60)
    events, prov = knowledge.fetch("google", "calendar", "find", (args["query"], s.isoformat(), e.isoformat()), svc.account,
                                   lambda: svc.events(s, e, query=args["query"], limit=args.get("limit", 10)))
    return _events_out(svc, events, f"matching '{args['query']}'", prov)


@guarded
def calendar_get_event(args):
    svc = _cal()
    eid = _ref("event", args.get("event_id"), "event_list")
    ev, prov = knowledge.fetch("google", "calendar", "event", eid, svc.account, lambda: svc.get_event(eid))
    _focus_event(ev, svc.account)
    att = f" Attendees: {', '.join(ev['attendees'])}." if ev["attendees"] else ""
    return (f"OK: {ev['when']} (time zone {ev['tz']}; {knowledge.source_note(prov)}): "
            + wrap("calendar event", ev["title"] + (f" @ {ev['location']}" if ev["location"] else "") + att
                   + (f"\nNotes: {ev['description']}" if ev["description"] else ""), 1500))


@guarded
def calendar_create_event(args):
    from room_agent.integrations.google.calendar import parse_time

    svc = _cal()
    tz = svc.tz()
    start = parse_time(args["start"], tz)
    if args.get("end"):
        end = parse_time(args["end"], tz, base=start if isinstance(start, datetime.datetime) else None)
    elif isinstance(start, datetime.datetime):
        end = start + datetime.timedelta(minutes=int(args.get("duration_minutes") or 60))
    else:
        end = start + datetime.timedelta(days=1)
    if isinstance(start, datetime.datetime) and start < datetime.datetime.now(tz) - datetime.timedelta(hours=1):
        raise ValueError(f"{start.strftime('%a %b %d %I:%M %p')} is in the past; check the date with them")
    ev = svc.create_event(args["title"], start, end, args.get("location", ""), args.get("description", ""))
    _focus_event(ev, svc.account)
    return f"OK: added to their calendar: '{ev['title']}', {ev['when']} (time zone {ev['tz']}); read back from Google."


@guarded
def calendar_update_event(args):
    from room_agent.integrations.google.calendar import parse_time

    svc = _cal()
    eid = _ref("event", args.get("event_id"), "event_list")
    cur = svc.get_event(eid)
    tz = svc.tz()
    changes = {k: args.get(k) for k in ("title", "location", "description") if args.get(k) is not None}
    if args.get("start"):
        changes["start"] = parse_time(args["start"], tz, base=cur["start"])
    if args.get("end"):
        changes["end"] = parse_time(args["end"], tz, base=cur["end"])
    for k in ("start", "end"):
        if k in changes and not isinstance(changes[k], datetime.datetime):
            changes[k] = datetime.datetime.combine(changes[k], cur[k].timetz())
    ev = svc.update_event(eid, **changes)
    _focus_event(ev, svc.account)
    return f"OK: updated (read back from Google): {ev['when']}: " + wrap("calendar event", ev["title"] + (
        f" @ {ev['location']}" if ev["location"] else ""), 300)


@guarded
def calendar_delete_event(args):
    svc = _cal()
    eid = _ref("event", args.get("event_id"), "event_list")
    ev = svc.get_event(eid)
    svc.delete_event(eid)
    env.items.pop("event", None)
    return f"OK: deleted the event on {ev['when']}; Google no longer shows it."


def _event_state(args, before=None):
    eid = args.get("event_id")
    if before is None and eid:
        try:
            ev = _cal().get_event(eid)
            return {k: ev[k] for k in ("id", "title", "start", "end", "location", "description")}
        except IntegrationError:
            return None
    it = env.item("event")
    return {"id": it["id"]} if it else None


def _undo_update(args, before, after):
    try:
        _cal().update_event(before["id"], title=before["title"], start=before["start"], end=before["end"],
                            location=before["location"], description=before["description"])
    except IntegrationError as e:
        return f"FAILED: {e.say()}"
    return "OK: the event is back the way it was (read back from Google)."


def _created_state(args, before=None):
    if before is None:
        return {"none": True}
    it = env.item("event")
    return {"id": it["id"]} if it else None


def _undo_create(args, before, after):
    try:
        _cal().delete_event(after["id"])
    except IntegrationError as e:
        return f"FAILED: {e.say()}"
    env.items.pop("event", None)
    return "OK: removed the event I just added."


def _describe_event(verb):
    def describe(args):
        it = env.item("event")
        return f"{verb} the {it['label']}" if it and it["id"] == args.get("event_id") else f"{verb} that event"
    return describe


# ---------------------------------------------------------------- register
S = lambda props, required=(): {"type": "object", "properties": props, "required": list(required)}
STR, INT, BOOL = {"type": "string"}, {"type": "integer", "minimum": 1, "maximum": 10}, {"type": "boolean"}
MSG_REF = {"type": "string", "description": "An email id from a list, its number in the last list ('2'), or 'last' for "
                                            "the email being talked about."}
WHEN = {"type": "string", "description": "today / tomorrow / this week / next week / weekend / a weekday, optionally "
                                         "with morning/afternoon/evening, or an ISO date. Default today."}
TIME = {"type": "string", "description": "ISO datetime in the calendar's time zone (2026-10-09T19:00), or plain words "
                                         "('friday 7pm', 'tomorrow at 3'). A bare date = all day."}

DRAFT_INTENT = re.compile(r"\b(draft|write|reply|respond|answer|compose|e-?mail (him|her|them)|message|tell (him|her|them)|"
                          r"let (him|her|them) know|saying|send (an? |him an? |her an? |them an? )?e-?mail|e-?mail to)\b"
                          r"|^\W*(can you |could you |please )?e-?mail\b", re.I)  # ("email Adam about...": an order)
# A new email needs who, a subject and the text (a reply already has its recipient and subject). The x- hints are for
# collecting what's missing over several turns (actions/pending.py); models only see plain JSON Schema.
DRAFT = {"type": "object", "properties": {
    "to": {"type": "string", "format": "email",
           "description": "The recipient's email address as they said it, or a contact's name. Never invent an address. "
                          "Needed for a new email (a reply already has it).",
           "x-ask": "Who's it going to? What's their email address?",
           "x-cues": [r"(?:send|address|email|mail|write)\s+it\s+to", r"(?:the\s+)?(?:recipient|email address|address)",
                      r"it'?s\s+(?:going\s+)?to"]},
    "subject": {"type": "string", "description": "The subject line (needed for a new email; a reply already has it)", "x-ask": "What's the subject?",
                "x-cues": [r"(?:the\s+)?subject(?:\s+line)?", r"(?:call|title)\s+it"]},
    "body": {"type": "string", "description": "What the email says", "x-text": True, "x-ask": "And what should I say?",
             "x-cues": [r"tell\s+(?:him|her|them)(?:\s+that)?", r"let\s+(?:him|her|them)\s+know(?:\s+that)?",
                        r"(?:the\s+)?(?:message|body|email)\s+(?:is|should be|says?)"],
             "x-answer-cues": [r"(?:just\s+)?(?:say|write)(?:\s+that)?", r"saying(?:\s+that)?"]},
    "reply_to": MSG_REF},
    "required": ["to", "subject", "body"], "x-optional-when": {"to": "reply_to", "subject": "reply_to"}}
EDIT_INTENT = re.compile(r"\b(shorter|longer|change|edit|rewrite|reword|make it|add|remove|fix|instead|also|update|tone|"
                         r"polite|formal|casual|less|more|subject|nicer|friendlier)\b", re.I)
SEND_INTENT = re.compile(r"\bsend\b", re.I)
CREATE_INTENT = re.compile(r"\b(schedule|add|create|book|put|set up|plan|calendar|make (an?|the) (event|appointment|"
                           r"meeting))\b", re.I)
UPDATE_INTENT = re.compile(r"\b(move|change|reschedule|push|pull|shift|update|rename|edit|make it|set|add|instead|earlier|"
                           r"later|extend|shorten|put)\b", re.I)
DELETE_INTENT = re.compile(r"\b(delete|cancel|remove|clear|drop|get rid of)\b", re.I)

gmail_read, gmail_compose = _can("gmail", "read"), _can("gmail", "compose")
cal_read, cal_write = _can("calendar", "read"), _can("calendar", "write")
COMMON = dict(private=True, claim=["access"])

register(Capability("gmail_search", "Search their Gmail: by sender, recipient, subject, words, date, unread, attachment.",
                    S({"query": STR, "sender": STR, "to": STR, "subject": STR,
                       "on": {"type": "string", "description": "a day: today / yesterday / YYYY-MM-DD"},
                       "after": STR, "before": STR, "unread": BOOL, "has_attachment": BOOL, "important": BOOL, "limit": INT}),
                    gmail_search, group="gmail", available=gmail_read, changes_state=False,
                    examples=["find the email Andrew sent me yesterday", "any emails about the invoice?"], **COMMON))
register(Capability("gmail_list_recent", "Their latest emails (optionally only today's, or only important ones).",
                    S({"limit": INT, "today": BOOL, "important": BOOL}), gmail_list_recent, group="gmail",
                    available=gmail_read, changes_state=False, examples=["what emails did I get today?"], **COMMON))
register(Capability("gmail_get_unread", "Their unread emails.", S({"limit": INT}), gmail_get_unread, group="gmail",
                    available=gmail_read, changes_state=False, **COMMON))
register(Capability("gmail_get_message", "Read one email (what did he say?).", S({"message_id": MSG_REF}),
                    gmail_get_message, group="gmail", available=gmail_read, changes_state=False, **COMMON))
register(Capability("gmail_get_thread", "Read a whole email conversation (to summarize a thread).",
                    S({"thread_id": {"type": "string", "description": "a thread id, or 'last'"}, "message_id": MSG_REF}),
                    gmail_get_thread, group="gmail", available=gmail_read, changes_state=False, **COMMON))
register(Capability("gmail_get_attachments", "What's attached to an email (names, types, sizes; short text files).",
                    S({"message_id": MSG_REF}), gmail_get_attachments, group="gmail", available=gmail_read,
                    changes_state=False, **COMMON))
register(Capability("gmail_create_draft", "Write a draft (NOT sent): a reply ('reply_to': 'last') or a new email ('send an "
                    "email to ...' starts here too). 'Draft a reply' never means send. Pass only what they actually said.",
                    DRAFT,
                    gmail_create_draft, group="gmail", available=gmail_compose, intent=DRAFT_INTENT,
                    observe=_draft_state, undo=_undo_draft, event="email.drafted", private=True, claim=["access"]))
register(Capability("gmail_update_draft", "Change the current draft (make it shorter, change the subject...). Still not sent.",
                    S({"draft_id": {"type": "string", "description": "'current' for the draft being worked on"},
                       "body": STR, "subject": STR, "to": STR}), gmail_update_draft, group="gmail",
                    available=gmail_compose, intent=EDIT_INTENT, prepare=_prep(draft_id=("draft", None)),
                    private=True, claim=["access"]))
register(Capability("gmail_send", "Send the current draft. Only when they say send; Jarvis always asks them to confirm "
                    "first.", S({"draft_id": {"type": "string", "description": "'current' for the draft being worked on"}}),
                    gmail_send, group="gmail", available=gmail_compose, risk=Risk.SENSITIVE, intent=SEND_INTENT,
                    confirm_keys=["draft_id"], prepare=_prep(draft_id=("draft", None)), describe=_describe_send,
                    event="email.sent", private=True, claim=["message", "access"]))
register(Capability("calendar_list_calendars", "Their calendars.", S({}), calendar_list_calendars, group="calendar",
                    available=cal_read, changes_state=False, **COMMON))
register(Capability("calendar_get_events", "What's on their calendar in a period (what do I have tomorrow? next meeting: "
                    "when='upcoming', limit=1).",
                    S({"when": WHEN, "start": TIME, "end": TIME, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
                    calendar_get_events, group="calendar", available=cal_read, changes_state=False,
                    examples=["what do I have tomorrow?", "anything Friday afternoon?"], **COMMON))
register(Capability("calendar_find_events", "Find events by words (find my meeting with John); next 60 days by default.",
                    S({"query": STR, "when": WHEN, "start": TIME, "end": TIME, "limit": INT}, ["query"]),
                    calendar_find_events, group="calendar", available=cal_read, changes_state=False, **COMMON))
register(Capability("calendar_get_event", "One event's details.", S({"event_id": {"type": "string", "description":
                    "an event id, its number in the last list, or 'last'"}}), calendar_get_event, group="calendar",
                    available=cal_read, changes_state=False, **COMMON))
register(Capability("calendar_create_event", "Add an event to their calendar (schedule dinner Friday at 7). No invitations "
                    "are sent.", S({"title": {**STR, "x-ask": "What should I call it?", "x-cues": [r"(?:the\s+)?(?:title|name)",
                    r"call\s+it"]}, "start": {**TIME, "x-ask": "When should it be?", "x-cues": [r"(?:the\s+)?(?:time|date|day)",
                    r"(?:move|put)\s+it\s+(?:to|on|at)"]}, "end": TIME, "duration_minutes": {"type": "integer",
                    "minimum": 5, "maximum": 1440}, "location": STR, "description": STR}, ["title", "start"]),
                    calendar_create_event, group="calendar", available=cal_write, intent=CREATE_INTENT,
                    observe=_created_state, undo=_undo_create, event="calendar.created", private=True,
                    claim=["calendar", "access"]))
register(Capability("calendar_update_event", "Change an event: time (move that meeting to 3), title, place, notes.",
                    S({"event_id": {"type": "string", "description": "an event id or 'last'"}, "title": STR, "start": TIME,
                       "end": TIME, "location": STR, "description": STR,
                       "confidence": {"type": "number", "minimum": 0, "maximum": 1}}, ["event_id"]),
                    calendar_update_event, group="calendar", available=cal_write, risk=Risk.CONFIRM, min_confidence=0.7,
                    intent=UPDATE_INTENT, prepare=_prep(event_id=("event", "event_list")), observe=_event_state,
                    undo=_undo_update, undo_is_symmetric=False, describe=_describe_event("change"),
                    event="calendar.updated", private=True, claim=["calendar", "access"]))
register(Capability("calendar_delete_event", "Delete an event. Jarvis always asks them to confirm first.",
                    S({"event_id": {"type": "string", "description": "an event id or 'last'"}}, ["event_id"]),
                    calendar_delete_event, group="calendar", available=cal_write, risk=Risk.SENSITIVE,
                    intent=DELETE_INTENT, confirm_keys=["event_id"], prepare=_prep(event_id=("event", "event_list")),
                    describe=_describe_event("delete"), event="calendar.deleted", private=True,
                    claim=["calendar", "access"]))
