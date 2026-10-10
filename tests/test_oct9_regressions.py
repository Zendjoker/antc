"""Regressions from the October 9 voice test, offline: a simulated Google, a scripted model (no cost), the real executor,
pending-request code, echo classifier and claim guard. Nothing is spoken, recorded, sent or put on a real calendar.

    .venv\\Scripts\\python -m tests.test_oct9_regressions

1. Advice is never an action: "What should I tell them?" matched the email tool's "tell them" and started a draft.
2. A no ends the request: "No, no draft. Just tell me..." was read as an edit to the draft, so it stayed open; now it is
   dropped with its speech hint and collected arguments, can't be resumed, and isn't called again on unrelated turns.
3. A correction isn't echo: "Actually, move the calls to Thursday." shared "actually", "calls" and "Thursday" with the
   plan Jarvis was reading, so it was thrown away as its own voice.
4. Dates and calendar state: "tomorrow" / "Wednesday" are real dates in the calendar's time zone, and a plan talked
   through is never reported as events that exist ("Wednesday's wide open" needs a read, "blocked gym Monday" a create).
"""

import datetime
import threading
import time
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from tests.harness import Checker, Conversation, setup_env

TMP = setup_env(JARVIS_VAULT="memory", GOOGLE_CLIENT_ID="test-client.apps.googleusercontent.com",
                GOOGLE_CLIENT_SECRET="GOCSPX-test-secret-123",
                LLM_SMART="openai")  # ("should I..." is routed to the smart model: keep it on the scripted one)

from room_agent import runtime as rt  # noqa: E402
from room_agent import truth  # noqa: E402
from room_agent.actions import core, pending, tasks  # noqa: E402
from room_agent.audio import speech_check as sc  # noqa: E402
from room_agent.conversation import policy  # noqa: E402
from room_agent.integrations import provider  # noqa: E402
from room_agent.integrations.google import calendar as gcal  # noqa: E402
from tests.fake_google import FakeGoogle, Mailbox  # noqa: E402

t = Checker()
check = t.check
core.ensure_loaded()

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


def last_user_message(reqs):
    msgs = (reqs[0] if reqs else {}).get("messages", [])
    return next((m["content"] for m in reversed(msgs) if m["role"] == "user" and isinstance(m["content"], str)), "")


# ---------------------------------------------------------------- 1. advice is not an action
print("Advice is answered in words:")
ADVICE = ["What should I tell them?", "That's what should I tell him?", "So what do I tell them?",
          "Should I build a website for a restaurant or run Instagram ads for them?  Be honest.",
          "How should I respond to him?", "No, no draft. Just tell me what should I tell them."]
NOT_ADVICE = ["Tell them I'll be late.", "Email Sam that I'm running late.", "Send an email to adam at gmail dot com.",
              "What should I tell them? Draft it to sam at gmail dot com.", "Should I turn on the light?",
              "What time should I leave?"]
check("advice questions are recognised", all(policy.asks_advice(s) for s in ADVICE),
      [s for s in ADVICE if not policy.asks_advice(s)])
check("orders and plain questions are not advice", not any(policy.asks_advice(s) for s in NOT_ADVICE),
      [s for s in NOT_ADVICE if policy.asks_advice(s)])
check("turn type is ADVICE (before 'clarification', even right after Jarvis asked something)",
      (setattr(rt, "last_reply", "Who's this going to?"), policy.classify("That's what should I tell him?").kind)[1] == policy.ADVICE)
rt.last_reply = ""
drafts_before = len(box.drafts)
res, _, offered = convo.say("What should I tell them?",
                            [("gmail_create_draft", {"subject": "Websites", "body": "Lead with money."})],
                            "Lead with money and control.")
spoken = convo.said()
check("an advice turn isn't even offered the email / calendar write tools (no wasted tool round)",
      offered and "gmail_create_draft" not in offered and "calendar_create_event" not in offered, sorted(offered)[:20])
check("Oct 9 13:15: the email tool is refused for an advice question", res and res[0].startswith(
      "FAILED: not done: they asked what to say or do"), res)
check("...no draft, no request left open to collect a recipient", len(box.drafts) == drafts_before and rt.pending is None,
      (rt.pending, len(box.drafts)))
check("...and the answer is spoken in words", "Lead with money and control." in " ".join(spoken), spoken)

# ---------------------------------------------------------------- 2. a no ends the request, completely
print("An explicit no drops the request:")
res, *_ = say("Write an email for me.", [("gmail_create_draft", {})], "Who's it going to?")
p = rt.pending
check("setup: a draft request is collecting the recipient, with the address hint on",
      p is not None and p.capability == "gmail_create_draft" and p.asking == "to" and pending.stt_hint() is not None, (p, res))
res, system, reqs, spoken = say("No, no draft. Just tell me what should I tell them.", reply="Lead with money.")
check("'No, no draft. Just tell me...' cancels the request", rt.pending is None and p.status == "cancelled", p.status)
check("...the speech-recognition hint for an address is gone", pending.stt_hint() is None)
check("...nothing collected is staged for a later call", not pending._staged)
check("...the model is told it was turned down (and answers the rest)", "turned down the pending gmail create draft"
      in last_user_message(reqs) and "Lead with money." in " ".join(spoken), last_user_message(reqs)[-300:])
check("...no pending_request line is shown to the model any more", pending.context_line() is None)
check("...and it's remembered as declined", "gmail_create_draft" in rt.declined)
drafts_before = len(box.drafts)
say("What's the weather like?", reply="Sunny.")
check("an unrelated turn doesn't bring it back", rt.pending is None)
res, *_ = say("Okay, cool.", [("gmail_create_draft", {"body": "Lead with money."})], "Alright.")
check("the model calling it again on an unrelated turn is refused, nothing collected or drafted",
      res and "turned this down" in res[0] and rt.pending is None and len(box.drafts) == drafts_before, (res, rt.pending))
res, *_ = say("Send an email to adam at gmail dot com.", [("gmail_create_draft", {"to": "adam@gmail.com"})], "Subject?")
check("a plain new ask for an email still works afterwards", res and res[0].startswith("NEEDS") and rt.pending is not None, res)
res, system, reqs, spoken = say("Okay, forget about it.  I have $500 and two weeks.", reply="Good budget.")
check("'Okay, forget about it. <something new>' drops the request and answers the new part",
      rt.pending is None and "Good budget." in " ".join(spoken), (rt.pending, spoken))
check("...and that no is remembered", "gmail_create_draft" in rt.declined)
drafts_before = len(box.drafts)
say("Send an email to adam at gmail dot com.", [("gmail_create_draft", {"to": "adam@gmail.com"})], "Subject?")
check("a plain new ask right after a no lifts that no", "gmail_create_draft" not in rt.declined and rt.pending is not None)
say("Subject is the trip.")
res, *_ = say("I'll be home later, call me.", scripts=[{"tools": [("gmail_create_draft", {})]}, {"text": "Drafted."}])
check("...so its next answer (no email words in it) completes the draft instead of being refused",
      len(box.drafts) == drafts_before + 1 and not any("turned this down" in r for r in res), res)
pending.cancel("test reset")
rt.declined.clear()

print("A declined step can't be resumed:")
task = tasks.new("email them", [{"tool": "gmail_create_draft", "args": {}}], kind="plan")
task["steps"][0]["state"], task["state"] = "WAITING", "WAITING"
tasks.decline_waiting("gmail_create_draft")
check("its waiting step is cancelled for good", task["steps"][0]["state"] == "CANCELED" and task["steps"][0].get("declined"),
      task["steps"][0])
try:
    tasks.resume(task)
except Exception as e:  # noqa: BLE001 (only the step's state matters here)
    print("   (resume raised:", e, ")")
check("'resume' leaves the declined step cancelled", task["steps"][0]["state"] == "CANCELED", task["steps"][0])

# ---------------------------------------------------------------- 3. a correction is not echo
print("Corrections over Jarvis's own voice:")


class Delay:
    delay = 0.4


class Engine:
    interrupted = threading.Event()
    delay = Delay()


rt.engine = Engine()
rt.stt_confidence = {"logprob": -0.25, "no_speech": 0.05}
PLAN = ("Alright, here's a clean plan that actually fits. Demo sites: Tuesday and Thursday mornings, two-hour blocks each. "
        "Calls: Tuesday and Thursday afternoons, three quick windows to reach owners.")


def heard_while_speaking(text, agent=PLAN):
    rt.recent_speech.clear()
    rt.turn_speech.clear()
    rt.turn_speech.append(agent)
    rt.tts_end = time.time() + 2.0  # the agent is still talking
    return sc.classify_audio(text, time.time())


for text in ["Actually, move the calls to Thursday.", "Not Thursday, Wednesday.", "Move the calls to Thursday mornings."]:
    label, why = heard_while_speaking(text)
    check(f"{text!r} over the plan -> USER", label == sc.USER, (label, why))
DRAFT_Q = "You're asking me to draft what to tell restaurant owners, right? I'll write you an outreach pitch. Who's this going to?"
label, why = heard_while_speaking("No, no draft. Just tell me what should I tell them.", DRAFT_Q)
check("'No, no draft...' over the draft question -> USER", label == sc.USER, (label, why))
label, why = heard_while_speaking("What's the weather tomorrow?")
check("an ordinary interruption -> USER", label == sc.USER, (label, why))
label, why = heard_while_speaking("Tuesday and Thursday afternoons, three quick windows.")
check("its own sentence leaking into the mic -> AGENT_ECHO", label == sc.AGENT_ECHO, (label, why))
ECHOY = "Actually, you could switch the calls to Thursday afternoon if that works."
label, why = heard_while_speaking("actually you could switch the cause to thursday afternoon", ECHOY)
check("its own 'actually...' sentence with one misheard word -> still AGENT_ECHO", label == sc.AGENT_ECHO, (label, why))
label, why = heard_while_speaking("quick windows")
check("a short fragment of its own words -> AGENT_ECHO", label == sc.AGENT_ECHO, (label, why))
words = sc.norm_words("Actually, move the calls to Thursday.")
check("barge-in check: the correction is not rejected as echo", not (sc.is_own_speech(words) and not sc.is_correction(words)))
words = sc.norm_words("Tuesday and Thursday afternoons, three quick windows.")
check("barge-in check: the real echo still is", sc.is_own_speech(words) and not sc.is_correction(words))
rt.engine = None
rt.turn_speech.clear()

# ---------------------------------------------------------------- 4. dates and calendar state
print("Relative dates and calendar state:")
for zone in ("America/Los_Angeles", "Asia/Tokyo", "Pacific/Kiritimati"):
    tz = ZoneInfo(zone)
    today = datetime.datetime.now(tz).date()
    got = gcal.parse_time("tomorrow 8am", tz)
    check(f"'tomorrow 8am' is the calendar's tomorrow ({zone})", got.date() == today + datetime.timedelta(days=1)
          and (got.hour, got.minute) == (8, 0) and got.tzinfo is tz, got)
    wed = gcal.parse_time("wednesday", tz)
    check(f"'wednesday' is the next Wednesday ({zone})", wed.weekday() == 2 and 1 <= (wed - today).days <= 7, wed)
    s, e = gcal.period("wednesday", tz)
    check(f"'what's my Wednesday' reads that whole day ({zone})", s.date() == wed and (e - s) == datetime.timedelta(days=1))
FRI = datetime.date(2026, 10, 9)  # (fixed dates: this must not depend on the day the suite runs)
check("on a Friday, 'Friday' means next Friday (as the days line tells the model), not today",
      gcal._day("friday", FRI) == datetime.date(2026, 10, 16) and gcal._day("next friday", FRI) == datetime.date(2026, 10, 16))
check("...'today' / 'tomorrow' / 'Wednesday' / 'Monday' from that Friday", gcal._day("today", FRI) == FRI
      and gcal._day("tomorrow", FRI) == datetime.date(2026, 10, 10) and gcal._day("wednesday", FRI) == datetime.date(2026, 10, 14)
      and gcal._day("monday", FRI) == datetime.date(2026, 10, 12))
LA, TOKYO = ZoneInfo("America/Los_Angeles"), ZoneInfo("Asia/Tokyo")
FRI_2PM = datetime.datetime(2026, 10, 9, 14, 0, tzinfo=LA)        # a Friday afternoon
WED_10AM = datetime.datetime(2026, 10, 7, 10, 0, tzinfo=LA)       # this week's Friday still ahead
SAT_10AM = datetime.datetime(2026, 10, 10, 10, 0, tzinfo=LA)      # this week's Friday gone


def at(text, now, tz=LA):
    v = gcal.parse_time(text, tz, now=now)
    return v.strftime("%a %b %d %H:%M") if isinstance(v, datetime.datetime) else str(v)


def asks(text, now, tz=LA, reader=None):
    try:
        (reader or (lambda s: gcal.parse_time(s, tz, now=now)))(text)
    except gcal.AmbiguousTime as e:
        return [str(c) for c in e.choices] or ["(a time)"]
    return None


print("Part of the day and relative-date semantics (fixed 'now'):")
check("'tonight at 8' on Friday 2 PM is 8 PM today (was 8 AM)", at("tonight at 8", FRI_2PM) == "Fri Oct 09 20:00",
      at("tonight at 8", FRI_2PM))
check("'tonight 8pm', 'this evening at 7', 'tomorrow night at 9', 'Friday evening at 7' (from Wednesday)",
      [at("tonight 8pm", FRI_2PM), at("this evening at 7", FRI_2PM), at("tomorrow night at 9", FRI_2PM),
       at("friday evening at 7", WED_10AM)] == ["Fri Oct 09 20:00", "Fri Oct 09 19:00", "Sat Oct 10 21:00",
                                                 "Fri Oct 09 19:00"])
check("'tomorrow morning at 6' is 6 AM (was 6 PM); 'tomorrow at 3' still means 3 PM; '8 in the morning' is 8 AM",
      [at("tomorrow morning at 6", FRI_2PM), at("tomorrow at 3", FRI_2PM), at("tomorrow at 8 in the morning", FRI_2PM)]
      == ["Sat Oct 10 06:00", "Sat Oct 10 15:00", "Sat Oct 10 08:00"])
check("'tonight' with no time asks for one (never an all-day event)", asks("tonight", FRI_2PM) == ["(a time)"])
check("'tonight at 8' is the CALENDAR's tonight (Tokyo is already Saturday)",
      at("tonight at 8", FRI_2PM.astimezone(TOKYO), TOKYO) == "Sat Oct 10 20:00")
check("'Friday 7pm' said on a Friday is next Friday (test_integrations' spec)", at("friday 7pm", FRI_2PM) == "Fri Oct 16 19:00")
check("'this Friday' said on a Friday is asked: today or the 16th", asks("this friday 7pm", FRI_2PM) == ["2026-10-09", "2026-10-16"])
check("'this Friday' from Wednesday is that week's Friday, no question", at("this friday 7pm", WED_10AM) == "Fri Oct 09 19:00")
check("'next Friday' from Wednesday is asked: the 9th or the 16th", asks("next friday 7pm", WED_10AM) == ["2026-10-09", "2026-10-16"])
check("'next Friday' on a Friday or a Saturday is the 16th, no question",
      at("next friday 7pm", FRI_2PM) == at("next friday 7pm", SAT_10AM) == "Fri Oct 16 19:00")
check("reading follows the same rules ('this Friday' on a Friday asked; 'tonight' is 5 PM to midnight today)",
      asks("this friday", FRI_2PM, reader=lambda s: gcal.period(s, LA, now=FRI_2PM)) == ["2026-10-09", "2026-10-16"]
      and [x.strftime("%a %d %H:%M") for x in gcal.period("tonight", LA, now=FRI_2PM)] == ["Fri 09 17:00", "Sat 10 00:00"])

print("An ambiguous day is asked about, and the answer completes the same request:")
today_name = datetime.datetime.now(LA).strftime("%A").lower()  # ("this <today>" is ambiguous whatever day the suite runs)
def cal_events():  # (the fake calendar exists once it has been used)
    return FG.calendars.get("adam@gmail.test", {}).get("events", {})


before = len(cal_events())
res, *_ = say(f"Schedule dinner this {today_name} at 9pm.",
              [("calendar_create_event", {"title": "Dinner check", "start": f"this {today_name} 9pm"})], "Which one?")
p = rt.pending
check("nothing is created; the model is told both dates and to ask", res and res[0].startswith("NEEDS:") and "ask which, never "
      "guess" in res[0] and len(cal_events()) == before, res)
check("...the request stays open with what they said (title kept, no time guessed, nothing asked in code)",
      p is not None and p.capability == "calendar_create_event" and p.collected.get("title") == "Dinner check"
      and "start" not in p.collected and p.asking is None, p and (p.collected, p.asking))
later = (datetime.datetime.now(LA).date() + datetime.timedelta(days=7)).isoformat()
res, *_ = say("The one next week.", [("calendar_create_event", {"start": f"{later}T21:00"})], "Done.")
made = [e for e in cal_events().values() if e["summary"] == "Dinner check"]
check("their answer completes it (no second yes/no), on the date they chose, and the request is closed",
      res and res[0].startswith("OK: added") and made and made[0]["start"]["dateTime"].startswith(later + "T21:00")
      and rt.pending is None, (res, made))

from room_agent import prompt  # noqa: E402

friday = datetime.datetime(2026, 10, 9, 13, 17)
rt.last_reply = ""
lines = prompt._days("Set it up for tomorrow.", friday)
check("the model is given real dates when days come up (Fri Oct 9: tomorrow is Sat Oct 10, Wednesday is Oct 14)",
      lines and "tomorrow Sat Oct 10" in lines[0] and "Wed Oct 14" in lines[0], lines)
check("...and nothing extra on turns that don't mention days", prompt._days("turn the light off", friday) == [])
tz = ZoneInfo("America/Los_Angeles")
res, *_ = say("Put gym on my calendar tomorrow at 8am.", [("calendar_create_event", {"title": "Gym", "start": "tomorrow 8am"})],
              "Gym's on your calendar for tomorrow at 8.")
made = [e for e in FG.calendars["adam@gmail.test"]["events"].values() if e["summary"] == "Gym"]
want = (datetime.datetime.now(tz).date() + datetime.timedelta(days=1)).isoformat()
check("the created event is on the calendar's tomorrow at 8:00", res and res[0].startswith("OK: added") and made
      and made[0]["start"]["dateTime"].startswith(want + "T08:00"), (res, made))


def guard():
    return truth.ClaimGuard(lambda: 0, lambda: False)


g = guard()
check("a plan talked through is not a claim", not g.unverified("Demo sites: Tuesday and Thursday mornings, two-hour blocks each."))
check("'I've blocked gym Monday at 8' is held when nothing was created", g.unverified("I've blocked gym Monday at 8."))
check("'Those are on your calendar' is held when nothing was created", g.unverified("Those are on your calendar now."))
g.tool_result("calendar_create_event", "OK: added to their calendar: 'Gym', Mon Oct 12, 8:00 AM")
check("...and spoken once the event really was created", not g.unverified("I've blocked gym Monday at 8."))
g = guard()
check("Oct 9 13:16: 'Wednesday's wide open' is held without a calendar read",
      g.unverified("Wednesday's wide open, nothing on your calendar that day."))
g.tool_result("calendar_get_events", "OK: nothing on their calendar from Wed Oct 14 12:00 AM to Thu Oct 15 12:00 AM (time zone "
                                     "America/Los_Angeles; live). Don't invent anything.")
check("...and allowed after a read that found nothing (an empty read is still a verified read)",
      not g.unverified("Wednesday's wide open, nothing on your calendar that day."))
g = guard()
g.tool_result("set_light", "OK: nothing set matched")
check("an action that changed nothing still doesn't confirm 'done'", g.unverified("Done."))

# ---------------------------------------------------------------- 5. usage questions get measured numbers
print("Usage numbers come from code:")
from room_agent.llm.budget import budget, usage_lines  # noqa: E402

budget.start_turn()
budget.record("openai", "gpt-5-mini", fresh_in=1000, cached_in=500, out=200)
budget.start_turn()  # (the next turn: the one above is now "the last turn")
lines = usage_lines("How many tokens did that use?")
check("'how many tokens did that use?' gets the last turn's measured tokens", any(
      "1500 tokens in (500 of them cached), 200 out" in l for l in lines), lines)
check("...and is told what isn't tracked (never an estimate)", any("usage_not_tracked" in l and "Never estimate" in l
                                                                    for l in lines))
check("no usage lines on ordinary turns", usage_lines("set the volume to 20") == [] and usage_lines(
      "spend some time on the demo") == [])

# ---------------------------------------------------------------- 6. writing isn't researched first
print("Writing tasks are written, not researched:")
from room_agent.actions import executor  # noqa: E402

WRITE = ["Write me a two-sentence outreach pitch for restaurant owners.", "Can you write a one-line opener for the call?",
         "Give me a short caption for the demo site."]
LOOKUP = ["Research how restaurants get customers online.", "Write a pitch with the latest stats on online ordering.",
          "What's the best website builder for restaurants?"]
check("writing requests are recognised", all(policy.writing_task(s) for s in WRITE),
      [s for s in WRITE if not policy.writing_task(s)])
check("requests for outside information are not", not any(policy.writing_task(s) for s in LOOKUP),
      [s for s in LOOKUP if policy.writing_task(s)])
cap = core.get("research_web")
if cap is not None:
    rt.new_turn(WRITE[0])
    rt.turn_text = WRITE[0]
    refusal = executor._not_asked(cap, False)
    check("research_web is refused for a writing task", refusal and refusal.startswith("FAILED: not run: they asked you to "
                                                                                       "write"), refusal)
    rt.new_turn(LOOKUP[0])
    rt.turn_text = LOOKUP[0]
    check("...and still runs for 'research ...'", executor._not_asked(cap, False) is None)
else:
    check("research_web is registered", False, "not registered in this build")

# ---------------------------------------------------------------- 7. doubt about the work is read as such
print("Discouragement about the plan:")
from room_agent import social  # noqa: E402

DOUBTING = "Honestly, I feel like this whole business idea is stupid  and I'm wasting my time."
social.reset()
rt.new_turn(DOUBTING)
strategy = social.on_user_turn(DOUBTING)
check("Oct 9 13:17: read as doubting their work (not 'casual talk')", "doubting their work" in strategy.why
      and any("don't reassure them they aren't stupid" in n for n in strategy.notes), (strategy.why, strategy.notes))
check("...so the turn type is emotional, not casual", policy.classify(DOUBTING).kind == policy.EMOTIONAL,
      policy.classify(DOUBTING).kind)
social.reset()
rt.new_turn("you're so stupid")
check("insulting Jarvis is still read as annoyance, not doubt", "doubting their work" not in social.on_user_turn(
      "you're so stupid").why)
social.reset()

# ---------------------------------------------------------------- 9. a statement offer isn't repeated
print("Offers aren't repeated:")
rt.recent[:] = [{"role": "user", "text": "So let's plan my week.", "time": ""},
                {"role": "assistant", "text": "Here's a plan that fits. If you want, I can drop these on your calendar with "
                                              "rough times.", "time": ""}]
lines = prompt._already_said("What's my Wednesday look like?")
check("Oct 9 13:16: 'If you want, I can drop these on your calendar' counts as an offer not taken up",
      any("you_already_offered" in l and "drop these on your calendar" in l for l in lines), lines)
check("...but a yes is still a yes", prompt._already_said("Yes, do it.") == [])
rt.recent[:] = []

# ================================================================ the 14:34-14:45 live test
import queue  # noqa: E402

from room_agent import cognition, speech  # noqa: E402
from room_agent.actions.context import env  # noqa: E402
from room_agent.audio import styles, voices  # noqa: E402
from room_agent.audio.speaker import say as speaker_say  # noqa: E402
from room_agent.cognition import goal as cgoal  # noqa: E402
from room_agent.speech import director  # noqa: E402
from room_agent.tools import voice as vtools  # noqa: E402

print("Mood fades when the topic moves on:")
social.reset()
rt.new_turn("You know what? Forget the business stuff for a second. I'm just exhausted.")
social.on_user_turn("You know what? Forget the business stuff for a second. I'm just exhausted.")
low_then = social.state.snapshot()["values"]["low"]
NEXT = "imagine I'm starting a small business, I have $500 available."
rt.new_turn(NEXT)
strategy = social.on_user_turn(NEXT)
snap = social.state.snapshot()
check("'I'm just exhausted' is read as low...", low_then >= 0.35, low_then)
check("...but one unrelated turn later it no longer steers the reply (no 'low energy', not an emotional turn)",
      snap["values"]["low"] < 0.3 and "low energy" not in strategy.why and policy.classify(NEXT).kind != policy.EMOTIONAL,
      (snap["values"]["low"], strategy.why))
social.reset()
rt.new_turn("I'm so tired, long day.")
social.on_user_turn("I'm so tired, long day.")
rt.new_turn("Yeah, really exhausted today.")
social.on_user_turn("Yeah, really exhausted today.")
check("a mood they keep expressing still counts", social.state.snapshot()["values"]["low"] >= 0.35)
social.reset()

print("One steady voice; their style wins over any mood:")
from room_agent import config as _config  # noqa: E402
_level = _config.SPEECH_EXPRESSIVENESS
_config.SPEECH_EXPRESSIVENESS = "steady"  # (the Oct 9 behaviour is the "steady" level; the default may differ)
_tts, _speak_q = rt.tts_enabled, rt.speak_q  # (restored below: nothing plays this queue in a test)
rt.tts_enabled = True
_supported = styles.supported
styles.supported = lambda: True  # (the configured ElevenLabs model, not a test of it)
EMO = social.ResponseStrategy(mode="emotional", response_energy="low", supportiveness="high", confidence=0.9)


def performed(sentence="I hear you."):
    p = director.direct(sentence, EMO, {"values": {"low": 0.6}})
    return speech.steady(p, voices.current.style)


check("the mood-driven delivery itself is unchanged (it still exists as a reading)",
      director.direct("I hear you.", EMO, {"values": {"low": 0.6}}).pace < 1.0)
vtools.set_speaking_style("normal")
p = performed()
check("normal: plain baseline, even with a low mood (was 'soft x0.95')", p.direction == [] and p.pace == 1.0
      and p.energy == "normal" and p.reaction == "", (p.direction, p.pace, p.energy))
rt.new_turn("be serious")
rt.turn_no += 1
out = vtools.set_speaking_style("serious")
p = performed()
check("their 'serious' wins over a low mood (no softening, no slower pace)", out.startswith("OK")
      and p.direction == speech.style_words("serious") and p.pace == 1.0, (out, p.direction, p.pace))
rt.new_turn("series a little bit friendly.")
rt.turn_no += 1
out = vtools.set_speaking_style("serious", "warm")
p = performed()
check("Oct 9 14:36: 'serious, a little bit friendly' keeps both parts (serious + a little warm)",
      voices.current.style == "serious+warm" and p.direction == ["serious", "calm tone", "warmly"]
      and "serious with a little warm" in out, (voices.current.style, p.direction, out))
urgent = speech.steady(director.SpeechPerformance("Get out now.", direction=["clear", "firm"]), "serious+warm")
check("only a warning keeps its clear, firm delivery", urgent.direction == ["clear", "firm"])
rt.speak_q = queue.Queue()
rt.engine = None
rt.new_turn("hi")
rt.turn.delivery = social.VoiceDelivery(style="soft", pace=0.95)  # (what the social layer used to impose)
rt.turn_style = "excited"  # (a tag the model picked for itself)
speaker_say("Okay.")
item = rt.speak_q.get_nowait()
check("speaker: no mood delivery and no self-picked tag reach the voice; only their style",
      item.delivery is None and item.style == "serious+warm" and item.performance.direction == ["serious", "calm tone", "warmly"],
      (item.delivery, item.style, item.performance.direction))
_config.SPEECH_EXPRESSIVENESS = _level

print("They don't like how it sounds: back to the verified previous voice, no questions:")
rt.new_turn("Can you change, please, to normal? Because it sounds so weird.")
rt.turn_no += 1
note = vtools.handle_complaint("Can you change, please, to normal? Because it sounds so weird.")
check("Oct 9 14:37: right after a change, 'it sounds so weird' restores the style before it (in code)",
      voices.current.style == "serious" and "Already done" in note and "Don't ask how it sounds" in note,
      (voices.current.style, note))
rt.turn_no += 5
SEDUCE = "why are you talking to me like you're seducing me but i'm talking about serious stuff"
vtools.set_speaking_style("normal")
rt.voice_change = None
note = vtools.handle_complaint(SEDUCE)
check("Oct 9 14:43: with no recent change, the plain normal voice stays and the model is told not to ask about tone",
      voices.current.style == "normal" and "already the plain normal one" in note and "Don't ask" in note, note)
check("'that sounds bad' about their news is not a complaint about the voice",
      vtools.handle_complaint("Oh no, that sounds bad. My flight got cancelled.") == "")
_set_voice, _table = vtools.set_voice, voices.table
voices.table = lambda: {"Brian": ("id-brian", "deep"), "Sarah": ("id-sarah", "American woman, soft")}
switched = []
vtools.set_voice = lambda name: (switched.append(name), f"OK: switched to {name}")[1]
rt.voice_change = (rt.turn_no, "voice", "id-brian")
rt.turn_no += 1
note = vtools.handle_complaint("I don't like that voice.")
check("a voice they just switched to and dislike -> switched back to the previous one, verified by set_voice",
      switched == ["Brian"] and "back to Brian" in note, (switched, note))
vtools.set_voice, voices.table, styles.supported = _set_voice, _table, _supported
rt.tts_enabled, rt.speak_q = _tts, _speak_q
check("feedback questions after a change are dropped ('How's that sound now?')",
      social.habits.scrub("How's that sound now?") == "" and social.habits.scrub("Is that better?") == "")

print("No tool offers on casual remarks:")
for remark, offer in [("Okay, I think I'm going to go to the gym tomorrow.",
                       "I can drop “Gym” on your calendar for tomorrow with a rough time, or leave it open."),
                      ("Oh, yeah, I have to go out, I think, with my friend today.",
                       "Do you want me to put “hang out with friend” on your calendar for today, or you just saying it out loud?"),
                      ("Oh, yeah, forget to eat something. I'll go cook something.",
                       "Want me to add a \"cook\" timer or drop \"eat\" on your calendar?")]:
    rt.new_turn(remark)
    rt.turn.policy = policy.classify(remark)
    check(f"Oct 9 14:44: {remark[:40]!r} -> the calendar/timer offer is not spoken", social.scrub(offer) == "",
          rt.turn.policy.kind)
rt.new_turn("Put gym on my calendar tomorrow at 8")
rt.turn.policy = policy.classify("Put gym on my calendar tomorrow at 8")
check("...but a real request still gets its question", social.scrub("Want me to add it to your calendar?") != "")

print("An old goal doesn't steer a new topic:")
g = cgoal.Goal(user_request="why are you talking to me like that", objective="tone", level=cognition.DELIBERATE)
g.set(cgoal.BLOCKED, "asked them something")
cognition._state["goal"] = g
rt.turn_no += 1
g.last_turn = rt.turn_no - 1
rt.new_turn("Why you cut off?")
check("Oct 9 14:43: 'Why you cut off?' right after Jarvis asked about tone is not an answer to that goal",
      cognition.begin_turn("Why you cut off?")["goal"] is None)
g.set(cgoal.BLOCKED, "asked them something")
cognition._state["goal"] = g
rt.turn_no += 1
g.last_turn = rt.turn_no - 1
rt.new_turn("Serious.")
check("...a direct answer on the very next turn still continues it", cognition.begin_turn("Serious.")["goal"] is g)
g.set(cgoal.BLOCKED, "asked them something")
rt.turn_no += 3
rt.new_turn("Okay.")
check("...but not several turns later", cognition.begin_turn("Okay.")["goal"] is None)
cognition._state["goal"] = None

print("A reply that was cut off is explained honestly:")
rt.last_cut = (rt.turn_no - 1, "Let me dial back and keep it straight")
lines = prompt._cut_off("Why you cut off? You were just talking.")
check("the next turn is told it stopped because speech was heard, not by choice",
      lines and "not because you chose to" in lines[0] and "keep it straight" in lines[0], lines)
rt.turn_no += 3
check("...and only right after", prompt._cut_off("anything") == [])
rt.last_cut = None

print("Calendar: create, move, clarify, honest claims:")
pending.cancel("test reset")
rt.declined.clear()
before = len(cal_events())
res, *_ = say("Can you put my calls to the restaurants to Thursday?",
              [("calendar_create_event", {"title": "Calls to restaurants", "start": "thursday"})], "What time?")
p = rt.pending
check("Oct 9 14:34: a day with no time is not booked as all-day; it's asked, request kept",
      res and res[0].startswith("NEEDS:") and "no time" in res[0] and len(cal_events()) == before and p is not None
      and p.collected.get("title") == "Calls to restaurants", res)
res, *_ = say("At 3 in the afternoon.", [("calendar_create_event", {"start": "thursday 3pm"})], "Done.")
made = [e for e in cal_events().values() if e["summary"] == "Calls to restaurants"]
check("...their answer completes the same request with the title kept, once", res and res[0].startswith("OK: added")
      and len(made) == 1 and "T15:00" in made[0]["start"]["dateTime"], (res, made))
res, *_ = say("Actually move the calls to Friday.",
              [("calendar_create_event", {"title": "Calls to restaurants", "start": "friday 3pm"})], "Moved them.")
made = [e for e in cal_events().values() if e["summary"] == "Calls to restaurants"]
check("Oct 9 14:35: 'move the calls to Friday' after it exists: a create is not run (asked first)",
      res and res[0].startswith(("NEEDS_CONFIRMATION", "FAILED")) and len(made) == 1, (res, made))
res, *_ = say("Yes.", [("calendar_create_event", {"title": "Calls to restaurants", "start": "friday 3pm"})], "Done.")
made = [e for e in cal_events().values() if e["summary"] == "Calls to restaurants"]
check("...and even after a 'yes', a move never becomes a second event: told to update it instead",
      res and res[0].startswith("FAILED: not created") and "calendar_update_event" in res[0] and len(made) == 1, (res, made))
pending.cancel("test reset")
res, *_ = say("Actually move the calls to Friday.",
              [("calendar_update_event", {"event_id": "last", "start": "friday 3pm", "confidence": 0.95})],
              "Moved them to Friday.")
made = [e for e in cal_events().values() if e["summary"] == "Calls to restaurants"]
fri_date = gcal._day("friday", datetime.datetime.now(LA).date()).isoformat()
check("...the update moves the one event (same title, same time, new day)", res and res[0].startswith("OK: updated")
      and len(made) == 1 and made[0]["start"]["dateTime"].startswith(fri_date + "T15:00"), (res, made))
res, *_ = say("Put my birthday dinner on Saturday, all day.",
              [("calendar_create_event", {"title": "Birthday", "start": "saturday"})], "Added.")
check("an all-day event when they say so", res and res[0].startswith("OK: added") and "(all day)" in res[0], res)
g = guard()
g.tool_result("calendar_create_event", "OK: added to their calendar: 'Calls', Fri Oct 16, 3:00 PM")
check("'Moved your calls to Friday' is held when only a create happened", g.unverified("Moved your calls to Friday."))
g.tool_result("calendar_update_event", "OK: updated (read back from Google): Fri Oct 16, 3:00 PM: Calls")
check("...and spoken once an update really happened", not g.unverified("Moved your calls to Friday."))
check("a precise statistic with no source read this turn is held (Oct 9: '67% of people prefer ordering direct [7]')",
      guard().unverified("67% of people prefer ordering direct from the restaurant anyway [7]."))
g = guard()
g.tool_result("research_web", "OK: 5 sources ...")
check("...and allowed after research", not g.unverified("67% of people prefer ordering direct [7]."))

print("Misheard speech never writes to their accounts without a yes:")
pending.cancel("test reset")
env.items.pop("event", None)
TEXT = "oh can you put the dream hour oh yeah can you put it to thursday at 9am"
rt.new_turn(TEXT)
rt.turn_text = TEXT
rt.turn.uncertain = True
before = len(cal_events())
r = executor.execute("calendar_create_event", {"title": "Dream hour", "start": "thursday 9am"})
check("an uncertain transcript -> read back first, nothing created", r.message.startswith("NEEDS_CONFIRMATION")
      and "wasn't sure it heard them right" in r.message and len(cal_events()) == before, r.message)
pending.cancel("test reset")

# ================================================================ the 15:51-15:57 acceptance test
import threading  # noqa: E402

import numpy as np  # noqa: E402

from room_agent.actions.core import Capability, register  # noqa: E402
from room_agent.audio import engine as aengine  # noqa: E402
from room_agent.audio import stt  # noqa: E402
from room_agent.conversation import session  # noqa: E402
from room_agent.text import is_stop_request  # noqa: E402

print("Playback changes only on an explicit request (15:56: asked whether music was playing, it pressed play):")
RAN = []
LIVE_Q = "Can you tell me whether my computer is currently playing music?"
ASKS = ["Play Spotify.", "Pause the song.", "Resume playback.", "Can you play some music?", "Could you pause it please?",
        "Next song.", "Skip this.", "Stop the music.", "Stop music.", "Okay, play it again.", "Is it playing? If not, play it.",
        "I want to listen to some jazz.", "Let's hear some Drake.", "Keep playing."]
NOT_ASKS = [LIVE_Q, "Is Spotify playing?", "Did you pause my music?", "Why did you turn the music on?",
            "Can you check whether music is playing?", "If I asked you to play music, what would happen?",
            "Don't play anything.", "What's playing? Don't change anything.", "Is the music paused?",
            "What song is this?", "Stop.", "Okay.", "Yes."]
check("affirmative, explicit requests authorize playback", all(policy.asks_to_change_playback(s) for s in ASKS),
      [s for s in ASKS if not policy.asks_to_change_playback(s)])
check("questions, what-ifs, negations and bare words never do", not any(policy.asks_to_change_playback(s) for s in NOT_ASKS),
      [s for s in NOT_ASKS if policy.asks_to_change_playback(s)])
check("the dashboard's media buttons are explicit requests ('pause or play the music', 'next song', 'previous song')",
      all(policy.asks_to_change_playback(w) for w in ("pause or play the music", "next song", "previous song")))
check("a yes to Jarvis's own offer does ('Want me to play it?' -> 'Yes.'), a yes to something else doesn't",
      policy.asks_to_change_playback("Yes.", "It's paused. Want me to play it?")
      and not policy.asks_to_change_playback("Yes.", "Want me to set a timer?")
      and not policy.asks_to_change_playback("Yes, but don't play anything.", "Want me to play it?"))
for name in ("play_pause", "next_track", "previous_track", "play_music"):
    for words in (LIVE_Q, "Did you pause my music?", "Don't play anything."):
        rt.new_turn(words)
        rt.turn_text, rt.last_reply = words, ""
        out = executor._not_asked(core.get(name), False) or ""
        if not out.startswith("FAILED: not done: they didn't ask to change playback"):
            check(f"{name} refused on {words!r} (checked without running it)", False, out)
            break
    else:
        check(f"{name}: refused at the executor on a question, a 'did you', a 'don't' (checked without running it)", True)
rt.new_turn("Pause the song.")
rt.turn_text = "Pause the song."
check("...and allowed on 'Pause the song.'", executor._not_asked(core.get("play_pause"), False) is None)
_media_play = core.get("play_pause").execute
core.get("play_pause").execute = lambda a: (RAN.append(dict(a)), f"OK: {'playing' if a.get('action') == 'play' else 'paused'}: "
                                                                  "Some Song (Spotify).")[1]
_read = core.get("get_current_media").execute
core.get("get_current_media").execute = lambda a: "OK: Some Song (Spotify), paused."
_observe = core.get("play_pause").observe
core.get("play_pause").observe = None  # (no real media session read in a test)
res, _, _ = convo.say(LIVE_Q, scripts=[{"tools": [("get_current_media", {})]},
                                       {"text": "Not playing, it's paused. Playing now.", "tools": [("play_pause", {"action": "play"})]},
                                       {"text": "It's paused."}])
spoken = convo.said()
check("through the model loop (a read, then play in a later round): play is refused, nothing runs",
      RAN == [] and any("didn't ask to change playback" in x for x in res), (RAN, res))
check("...and 'Playing now' is never spoken", not any("Playing now" in s for s in spoken), spoken)
res, _, _ = convo.say("What's playing? Don't change anything.", [("play_pause", {"action": "pause"})], "It's paused.")
check("'What's playing? Don't change anything.' -> even a pause is refused", RAN == [], (RAN, res))
res, _, _ = convo.say("Play some music.", [("play_pause", {"action": "play"})], "Playing.")
check("'Play some music.' still plays", RAN == [{"action": "play"}], (RAN, res))
core.get("play_pause").execute, core.get("get_current_media").execute = _media_play, _read
core.get("play_pause").observe = _observe
g = guard()
g.tool_result("get_current_media", "OK: Premier Gaou (Spotify), paused.")
check("'Playing now - Premier Gaou' after only reading 'paused' is held", g.unverified("Playing now — Premier Gaou, on Spotify."))
check("...'Spotify is playing Premier Gaou' backed by a fresh read is fine (an observation, not a claim of starting it)",
      not guard().unverified("Spotify is playing Premier Gaou.") and not g.unverified("Premier Gaou is playing now."))
g.tool_result("play_pause", "OK: paused: Premier Gaou (Spotify).")
check("...a successful PAUSE never backs 'Playing now'", g.unverified("Playing now — Premier Gaou, on Spotify."))
g.tool_result("play_pause", "OK: playing: Premier Gaou (Spotify).")
check("...a verified successful play does", not g.unverified("Playing now — Premier Gaou, on Spotify."))

print("Typed turns keep the voice conversation awake (15:53:27):")


class SilentEngine:
    interrupted = threading.Event()
    delay = NS(delay=0.4)

    def drain_mic(self):
        pass


_orig = {k: getattr(session, k) for k in ("record_utterance", "speak_phrase", "SLEEP_AFTER_S", "CHECKIN_AFTER_S",
                                          "SLEEP_GRACE_S", "transcribe", "classify_audio", "take_turn",
                                          "filler_if_slow", "stop_thinking")}
EVENTS = []


def quiet_record(mic_q, start_timeout=0, already_heard=False, **kw):
    EVENTS.append(("listen", time.time()))
    if sum(1 for e in EVENTS if e[0] == "listen") == 1:
        rt.last_activity = time.time()  # (a typed command was answered while the mic heard nothing)
        EVENTS.append(("typed", rt.last_activity))
    time.sleep(min(start_timeout, 0.5))
    return None


session.record_utterance, session.SLEEP_AFTER_S, session.CHECKIN_AFTER_S, session.SLEEP_GRACE_S = quiet_record, 0.3, 999, 0.05
session.speak_phrase = lambda kind, history=None: (EVENTS.append((kind, time.time())), (False, False))[1]
session.filler_if_slow = session.stop_thinking = lambda *a, **k: None
rt.engine = SilentEngine()
ended = session.converse(None, [])
typed_at = next(t_ for k, t_ in EVENTS if k == "typed")
slept_at = next(t_ for k, t_ in EVENTS if k == "sleep")
listens_before = sum(1 for k, t_ in EVENTS if k == "listen" and t_ < slept_at)
check("a typed exchange during the silence restarts it: no sleep until a full silence AFTER it", ended == "sleep"
      and slept_at - typed_at >= 0.28 and listens_before >= 2, (EVENTS, slept_at - typed_at))
EVENTS.clear()
session.record_utterance = lambda mic_q, start_timeout=0, already_heard=False, **kw: (
    EVENTS.append(("listen", time.time())), time.sleep(min(start_timeout, 0.5)))[1]
rt.last_activity = 0.0
check("...real silence still ends the conversation (nothing typed)", session.converse(None, []) == "sleep"
      and [k for k, _ in EVENTS].count("sleep") == 1)

print("'Stop' over a reply is handled in code, no model call (15:51-15:52):")
STOPS = ["Can you stop?", "Please stop.", "Okay, stop.", "stop talking", "Enough.", "Jarvis, stop!", "hold on"]
NOT_STOPS = ["Stop the music and play jazz.", "Can you stop the timer?", "Why did you stop?", "Don't stop.",
             "Stop being so formal and tell me the plan."]
check("stop requests are recognised", all(is_stop_request(s) for s in STOPS), [s for s in STOPS if not is_stop_request(s)])
check("...requests that need an answer are not", not any(is_stop_request(s) for s in NOT_STOPS),
      [s for s in NOT_STOPS if is_stop_request(s)])
CALLS, EVENTS = [], []
heard_once = []


def stop_record(mic_q, start_timeout=0, already_heard=False, **kw):
    EVENTS.append(("listen", time.time()))
    if not heard_once:
        heard_once.append(1)
        return b"pcm"
    time.sleep(min(start_timeout, 0.5))
    return None


session.record_utterance = stop_record
session.transcribe = lambda pcm: "Can you stop?"
session.classify_audio = lambda text, started=None: ("USER", "test")
session.take_turn = lambda *a, **k: CALLS.append(a)
history = [{"role": "user", "content": "Explain science."}, {"role": "assistant", "content": "Science is the craft of"}]
session.converse(None, history, heard=True, barged=True)
check("barged + 'Can you stop?' -> acknowledged from the stock phrases, no model call, and kept in the conversation",
      CALLS == [] and ("stopped", ) == tuple(k for k, _ in EVENTS if k == "stopped")
      and history[-2] == {"role": "user", "content": "Can you stop?"} and history[-1]["role"] == "assistant", (CALLS, EVENTS, history[-2:]))
for k, v in _orig.items():
    setattr(session, k, v)
rt.engine = None

print("A short, clear 'stop!' over Jarvis's voice is checked by its words, not thrown away as too short:")


def short_engine(check_result):
    e = aengine.AudioEngine.__new__(aengine.AudioEngine)
    e.barge_stop_check, e._stop_checking, e._q_lock = (lambda pcm: check_result), False, threading.RLock()
    e._history = [(n, (np.zeros(1280, dtype=np.int16), 0.9)) for n in range(1, 8)]
    e.epoch, e.interrupted, e.is_playing = 0, threading.Event(), (lambda: True)
    e.confirmed, e.rejected = [], []
    e._confirm = lambda ep, detail: (e.confirmed.append(detail), ep.update(done=True))
    e._end_episode = lambda ep, reason: (e.rejected.append(reason), ep.update(done=True))
    return e


def short_episode(peak=0.92, frames=2):
    return {"start": 4, "run": 4, "frames": frames, "quiet": 3, "done": False, "pending": False, "tries": 0, "epoch": 0,
            "heard_before": 0.0, "peak": peak}


def settle(e, ep):
    end = time.time() + 2
    while not ep["done"] and time.time() < end:
        time.sleep(0.01)


e, ep = short_engine((True, "stop command 'Stop.'")), short_episode()
started = e._check_short_stop(ep)
settle(e, ep)
check("a short, clear 'Stop.' interrupts", started and e.confirmed == ["stop command 'Stop.'"] and not e.rejected, e.confirmed)
e, ep = short_engine((False, "'mm'")), short_episode()
e._check_short_stop(ep)
settle(e, ep)
check("...a short burst that isn't 'stop' is still rejected as too short", e.rejected and not e.confirmed, e.rejected)
e = short_engine((True, "x"))
check("...and a weak, click-like burst isn't even checked", not e._check_short_stop(short_episode(peak=0.5))
      and not e._check_short_stop(short_episode(frames=1)))
_whisper, _gate = stt.whisper_text, stt.speaker_id.gate
stt.speaker_id.gate = NS(verdict=lambda pcm: ("off", None))
rt.turn_speech.clear()
rt.turn_speech.append("Science is the craft of building better models from messy data.")
stt.whisper_text = lambda pcm, **k: "Stop."
check("verify_stop: 'Stop.' over a reply about science -> a stop command", stt.verify_stop(np.zeros(8000))[0])
rt.turn_speech.append("You can always tell me to stop.")
check("...but not when it's the agent's own word echoing back", not stt.verify_stop(np.zeros(8000))[0])
stt.whisper_text = lambda pcm, **k: "and then the experiment"
check("...nor a short piece of ordinary speech", not stt.verify_stop(np.zeros(8000))[0])
stt.whisper_text, stt.speaker_id.gate = _whisper, _gate
rt.turn_speech.clear()

t.done("OCT 9 REGRESSION TESTS")
