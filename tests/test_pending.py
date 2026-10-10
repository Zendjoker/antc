"""Multi-turn action collection (actions/pending.py) and spoken email addresses (emails.py), offline: a simulated Google,
a scripted model (no cost), the real executor, checks and conversation turn code.

    .venv\\Scripts\\python -m tests.test_pending

Proves: a request missing information becomes ONE PendingAction that later turns fill in and correct (never restarted);
code answers what it can without a model call; addresses come only from the user (never guessed; low confidence is read
back); "never mind" cancels without running anything; sending still needs the user's own yes; and the same machinery
works for Calendar (generic, nothing Gmail-specific in the conversation logic).
"""

import datetime
import json
import logging
import os
from types import SimpleNamespace as NS

from tests.harness import Checker, Conversation, setup_env

TMP = setup_env(JARVIS_VAULT="memory", GOOGLE_CLIENT_ID="test-client.apps.googleusercontent.com",
                GOOGLE_CLIENT_SECRET="GOCSPX-test-secret-123")
LOGS = []


class Capture(logging.Handler):
    def emit(self, record):
        LOGS.append(self.format(record))


logging.basicConfig(level=logging.INFO)
logging.getLogger().addHandler(Capture(logging.DEBUG))

from room_agent import emails  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import pending  # noqa: E402
from room_agent.actions.context import env  # noqa: E402
from room_agent.integrations import provider  # noqa: E402
from tests.fake_google import FakeGoogle, Mailbox  # noqa: E402

t = Checker()
check = t.check

# ---------------------------------------------------------------- 1. spoken email addresses (pure)
print("Spoken email addresses:")


def best(text, expecting=False):
    c = emails.find(text, expecting)
    return (c[0]["email"], c[0]["confidence"]) if c else (None, 0)


check("'adam at gmail dot com' -> adam@gmail.com, confident", best("send it to adam at gmail dot com")[0] == "adam@gmail.com"
      and best("send it to adam at gmail dot com")[1] >= emails.ACCEPT)
check("'adam dot azzouz at gmail dot com' -> adam.azzouz@gmail.com",
      best("adam dot azzouz at gmail dot com", True)[0] == "adam.azzouz@gmail.com")
check("underscore / dash / hyphen", best("sam underscore lee at outlook dot com", True)[0] == "sam_lee@outlook.com"
      and best("mary dash jo at yahoo dot fr", True)[0] == "mary-jo@yahoo.fr"
      and best("ann hyphen marie at example dot org", True)[0] == "ann-marie@example.org")
check("already written by speech recognition", best("adam@gmail.com")[0] == "adam@gmail.com"
      and best("Adam at Gmail.com")[0] == "adam@gmail.com")
check("'adam at gmail' (no .com) is only a guess: read back, never used silently",
      emails.CONFIRM <= best("adam at gmail", True)[1] < emails.ACCEPT and best("adam at gmail")[0] is None)
check("spelled letters are always read back", emails.CONFIRM <= best("j o h n at gmail dot com", True)[1] < emails.ACCEPT)
check("malformed 'adam at dot com' -> no address", best("adam at dot com", True)[0] is None)
ORDINARY = ["I looked at google dot com this morning", "meet me at the cafe at 5", "I'm at home dot dot dot",
            "the score was 3 at the half", "I was at work dot com all day", "we're at the point where it's done",
            "look at that dot on the screen", "she's at school, then at practice"]
check("ordinary talk with 'at' and 'dot' never becomes an address", not any(emails.strong(s) for s in ORDINARY),
      [(s, emails.find(s)) for s in ORDINARY if emails.strong(s)])
check("read back the way it's said", emails.spoken_form("adam.azzouz@gmail.com") == "adam dot azzouz at gmail dot com")

# ---------------------------------------------------------------- setup: a connected Google with drafts and calendar changes
FG = FakeGoogle()
G = provider("google")
G.http = FG
G.connect(levels={"gmail": ["read", "compose"], "calendar": ["read", "write"]}, open_browser=FG.open_browser, timeout=5)
box = FG.mailboxes.setdefault("adam@gmail.test", Mailbox("adam@gmail.test"))
rt.writer = NS(observe=lambda *a, **k: None, conversation_ended=lambda *a: None, forgot=lambda *a: None)
convo = Conversation(engine=None)


def say(text, calls=None, reply="Okay.", scripts=None):
    res, system, tools = convo.say(text, calls, reply, scripts=scripts)
    return res, system, list(convo.requests), convo.said()


def sent():
    return [m for m in box.messages.values() if "SENT" in m["labelIds"] and m["id"].startswith("dm")]


def last_draft():
    d = list(box.drafts.values())[-1]
    h = {x["name"]: x["value"] for x in d["message"]["payload"]["headers"]}
    return h, json.dumps(d)


# ---------------------------------------------------------------- 2. collecting an email over several turns
print("Send an email, piece by piece:")
res, system, reqs, spoken = say("Send an email to adam at gmail dot com.", [("gmail_create_draft", {"to": "adam@gmail.com"})],
                                "What's the subject?")
p = rt.pending
check("recipient resolved from the spoken address; nothing drafted", isinstance(p, pending.PendingAction)
      and p.collected.get("to") == "adam@gmail.com" and not box.drafts, (p, res))
check("asks only for what's missing (subject, then text)", res[0].startswith("NEEDS") and p.missing == ["subject", "body"]
      and p.asking == "subject" and "What's the subject?" in res[0], res)
check("the spoken address was logged with candidate, confidence and resolution",
      any("RAW TRANSCRIPT" in l and "adam@gmail.com" in l and "CONFIDENCE 0.9" in l and "RESOLUTION accepted" in l for l in LOGS))
first = p
res, system, reqs, spoken = say("Subject is Test.")
check("'Subject is Test.' -> the SAME pending action, subject set", rt.pending is first and first.collected.get("subject") == "Test")
check("...answered in code (no model call), asking for the text next", not reqs and spoken and spoken[-1].endswith(
      "And what should I say?") and first.asking == "body", (reqs, spoken))
res, system, reqs, spoken = say("Tell him I'll call tomorrow.", scripts=[{"tools": [("gmail_create_draft", {})]},
                                                                         {"text": "Drafted it. Want me to send it?"}])
h, raw = last_draft() if box.drafts else ({}, "")
check("'Tell him...' -> body; complete, so the model runs it with everything collected",
      res and res[0].startswith("OK: draft saved, NOT sent") and h.get("To") == "adam@gmail.com" and h.get("Subject") == "Test"
      and "I'll call tomorrow." in raw, (res, h))
check("the model was told everything is collected (and the values)", reqs and "everything gmail_create_draft needs is collected"
      in json.dumps(reqs[0]["messages"][-1]), reqs[0]["messages"][-1] if reqs else None)
check("pending action finished once it ran", rt.pending is None)
res, *_ = say("Send it.", [("gmail_send", {"draft_id": "current"})], "Send it to adam@gmail.com, subject Test?")
check("sending still asks first (existing permission rule); nothing sent", res[0].startswith("NEEDS_CONFIRMATION") and not sent(), res)
res, *_ = say("Yes.", [("gmail_send", {"draft_id": "current"})], "Sent.")
check("'yes' -> sent to the resolved address (their yes runs exactly that send, in code)",
      (not res or res[0].startswith("OK: sent to adam@gmail.com")) and len(sent()) == 1, res)

# ---------------------------------------------------------------- 3. corrections and cancelling
print("Corrections and cancelling:")
box.drafts.clear()
say("Send an email to adam at gmail dot com.", [("gmail_create_draft", {"to": "adam@gmail.com"})], "What's the subject?")
first = rt.pending
res, system, reqs, spoken = say("Actually send it to sam at gmail dot com.")
check("'Actually send it to sam at gmail dot com.' -> recipient changes, same request, nothing restarted",
      rt.pending is first and first.collected["to"] == "sam@gmail.com" and first.asking == "subject" and not reqs, (first, reqs))
say("Change the subject to Friday.")
check("'Change the subject to Friday.' -> subject changes", first.collected.get("subject") == "Friday" and first.asking == "body")
say("Tell him I'll be late. Sorry about that.", scripts=[{"text": "unused"}])  # (complete: the model would run it next)
check("two-sentence body collected", first.collected.get("body") == "I'll be late. Sorry about that.", first.collected)
rt.pending.missing, rt.pending.asking = ["body"], "body"  # (pretend it isn't run yet, to edit it)
first.collected.pop("subject")
say("Remove that last sentence.")
check("'Remove that last sentence.' -> edits the body only", first.collected.get("body") == "I'll be late.", first.collected)
res, system, reqs, spoken = say("No, say I'll be there at 3 PM.", [("gmail_create_draft", {"body": "I'll be there at 3 PM."})],
                                "Got it. What's the subject?")
check("fuzzy edit ('No, say 3 PM') goes to the model WITH the pending context; the call merges",
      reqs and "pending_request (a pending action" in json.dumps(reqs[0]["messages"]) and rt.pending is first
      and first.collected["body"] == "I'll be there at 3 PM." and first.collected["to"] == "sam@gmail.com"
      and res[0].startswith("NEEDS") and first.missing == ["subject"], (res, first))
drafts_before = len(box.drafts)
res, system, reqs, spoken = say("Never mind.")
check("'Never mind.' -> pending action cancelled, nothing run, no model call", rt.pending is None and not reqs
      and len(box.drafts) == drafts_before and spoken and any(w in spoken[-1] for w in ("cancel", "forget", "dropped", "won't")),
      (reqs, spoken))
for phrase in ("Cancel that.", "Forget it."):
    say("Send an email to sam at gmail dot com.", [("gmail_create_draft", {"to": "sam@gmail.com"})], "What's the subject?")
    say(phrase)
    check(f"{phrase!r} cancels too", rt.pending is None and len(box.drafts) == drafts_before)

# ---------------------------------------------------------------- 4. unclear and malformed addresses: never guessed
print("Unclear addresses are never guessed:")
res, *_ = say("Send an email to bob at gmail.", [("gmail_create_draft", {"to": "bob@gmail.com"})],
              "I heard bob at gmail dot com. Is that right?")
check("'bob at gmail' (missing .com) -> read back for confirmation, not used yet", rt.pending and rt.pending.candidate
      and "to" not in rt.pending.collected and "Is that right?" in res[0], (res, rt.pending))
check("speech recognition is told an address is expected", pending.stt_hint() and "email address" in pending.stt_hint())
res, system, reqs, spoken = say("No.")
check("'No.' -> asks for the address again, nothing stored", "to" not in rt.pending.collected and not reqs
      and "address" in spoken[-1], spoken)
res, system, reqs, spoken = say("bob at dot com")
check("malformed address -> asks again, nothing guessed", "to" not in rt.pending.collected and "didn't catch" in spoken[-1]
      and not reqs, spoken)
say("bob at gmail")
res, system, reqs, spoken = say("Yes, that's right.")
check("read back, then 'yes' -> accepted", rt.pending.collected.get("to") == "bob@gmail.com" and rt.pending.asking == "subject"
      and "subject" in spoken[-1].lower(), (rt.pending, spoken))
check("...logged as confirmed by the user", any("RESOLUTION confirmed by the user" in l for l in LOGS))
say("Never mind.")
res, *_ = say("Email Adam about the trip.", [("gmail_create_draft", {"to": "adam.smith@gmail.com", "subject": "Trip"})],
              "What's Adam's email address?")
check("a model-invented address is refused (not something they said) -> FAILED by name, nothing drafted",
      res[0].startswith("FAILED") and "adam.smith@gmail.com" in res[0] and rt.pending and "to" not in rt.pending.collected
      and rt.pending.collected.get("subject") == "Trip", (res, rt.pending))
say("Never mind.")
res, *_ = say("Send an email to Adam.", [("gmail_create_draft", {"to": "Adam"})], "What's Adam's email address?")
check("a name with no known address -> asks for it, by name", rt.pending and "to" not in rt.pending.collected
      and "What's Adam's email address?" in res[0], (res, rt.pending))
res, system, reqs, spoken = say("It's adam dot azzouz at gmail dot com.")
check("...then the spoken address fills it", rt.pending.collected.get("to") == "adam.azzouz@gmail.com" and not reqs, rt.pending)
say("Subject is the trip.")
res, system, reqs, spoken = say("What's the weather like?", [("get_time", {})], "Not sure. And what should I say?")
check("a question in between goes to the model; the pending action stays", rt.pending and rt.pending.asking == "body"
      and reqs, rt.pending)
res, system, reqs, spoken = say("What time is it?", [("get_time", {})], "It's noon. And what should I say?")
check("a question while the text is asked for is NOT taken as the text (the model decides)", reqs and rt.pending
      and "body" not in rt.pending.collected and rt.pending.asking == "body", rt.pending)
res, system, reqs, spoken = say("I'll be at home dot later, call me.", scripts=[{"tools": [("gmail_create_draft", {})]},
                                                                               {"text": "Drafted."}])
check("text with 'at' / 'dot' in the body is kept word for word (never turned into an address)",
      box.drafts and "I'll be at home dot later, call me." in last_draft()[1], last_draft()[1][-400:] if box.drafts else None)
check("no OAuth tokens or secrets in any log line", not any(tok in l for l in LOGS for tok in FG.issued)
      and not any("GOCSPX" in l for l in LOGS))

# ---------------------------------------------------------------- 5. the same machinery for Calendar (generic)
print("Calendar (same code, different capability):")
LA = datetime.datetime.now(datetime.timezone.utc).astimezone()
res, *_ = say("Schedule a meeting with Sarah.", [("calendar_create_event", {"title": "Meeting with Sarah"})],
              "When should it be?")
p = rt.pending
check("'Schedule a meeting with Sarah.' -> pending, missing only the time (from the schema)",
      p and p.capability == "calendar_create_event" and p.missing == ["start"] and "When should it be?" in res[0], (res, p))
check("no address expected: no speech hint", pending.stt_hint() is None)
res, system, reqs, spoken = say("Friday at 3.", scripts=[{"tools": [("calendar_create_event", {})]}, {"text": "Done, Friday at 3."}])
made = [e for e in FG.calendars["adam@gmail.test"]["events"].values() if e["summary"] == "Meeting with Sarah"]
check("'Friday at 3.' -> fills the time; the model runs it with everything collected", res and res[0].startswith(
      "OK: added to their calendar") and made and "T15:00" in made[0]["start"]["dateTime"], (res, made))
from room_agent import truth  # noqa: E402

g = truth.ClaimGuard(lambda: 0, lambda: False)
g.tool_result("calendar_create_event", "OK: added to their calendar: 'Meeting with Sarah', Fri Oct 9, 3:00 PM")
check("'All set, your meeting is on Friday at 3' is spoken after the event was created (not a smart-home claim)",
      not g.unverified("All set—your meeting with Sarah is on Friday at 3 PM.")
      and truth.ClaimGuard(lambda: 0, lambda: False).unverified("I turned the lights on."), g.unverified("All set, it's on Friday."))
say("Schedule lunch with Tom.", [("calendar_create_event", {"title": "Lunch with Tom"})], "When should it be?")
say("Forget it.")
check("calendar request cancelled too, nothing created", rt.pending is None and not [
    e for e in FG.calendars["adam@gmail.test"]["events"].values() if e["summary"] == "Lunch with Tom"])

# ---------------------------------------------------------------- 6. expiry
print("Expiry:")
say("Schedule lunch with Tom.", [("calendar_create_event", {"title": "Lunch with Tom"})], "When should it be?")
rt.pending.touched_at -= pending.COLLECT_SECONDS + 1
check("an old pending action expires", pending.current() is None)

t.done("PENDING ACTION TESTS")
