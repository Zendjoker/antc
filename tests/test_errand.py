"""Errand calls (phone/errand.py): Jarvis phones a restaurant for you in a restricted mode. Offline: the real phone
server on a local port, a simulated Twilio (signed webhook + ConversationRelay WebSocket), a fake model.

The model runs the conversation; a fake model plays scripted replies (status + booking + words), so each test sees
what CODE does with them: the hard limits (bookings outside the brief, leaks, echo, goodbye, limits).

Run:  .venv\\Scripts\\python -m tests.test_errand
"""

import asyncio
import json
import socket
import time

from tests.harness import setup_env

PORT = (lambda s: (s.bind(("127.0.0.1", 0)), s.getsockname()[1], s.close())[1])(socket.socket())
setup_env(PHONE_MODE="1", PUBLIC_URL=f"http://127.0.0.1:{PORT}", PHONE_PORT=PORT, PHONE_TOKEN="phone-secret-123456",
          TWILIO_ACCOUNT_SID="AC_test", TWILIO_AUTH_TOKEN="twilio-auth-secret-xyz", TWILIO_NUMBER="+14155550100",
          MY_PHONE="+14155550199")

import aiohttp  # noqa: E402

from room_agent import config  # noqa: E402
from room_agent.phone import errand, server, texts  # noqa: E402
from room_agent.phone.twilio import signature  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
URL = config.PUBLIC_URL
SMS = []
texts.text_me = lambda message, attach="": SMS.append(message) or "OK: texted"
errand.OPEN_WAIT_S = 60      # (tests answer first; the "nobody spoke" start is tested on its own)
errand.CLOSE_WAIT_S = 0.05   # (it waits for their goodbye before hanging up; short in tests)
errand.WORDS_PER_S = 1000    # (lines "finish playing" instantly in tests)


class FakeTwilio:
    def __init__(self):
        self.calls = []

    def call(self, to, from_, url):
        self.calls.append({"to": to, "from": from_, "url": url})
        return "CA1"

    def set_voice_webhook(self, number, url):
        raise AssertionError("an errand must never change the number's settings")


TW = FakeTwilio()
server.twilio = lambda: TW
server.start()
time.sleep(0.5)
QUEUE = []  # the model's replies, in order
SEEN = []   # what the model was given each time: (transcript length, the note code added)


def fake_think(e, transcript):
    SEEN.append((len(transcript), getattr(e, "_note", "")))
    return QUEUE.pop(0) if QUEUE else {"status": "talking", "say": "Mm-hm, got it."}


errand.THINK = fake_think
FINISHED = []  # errand ids, each time an outcome is texted + saved
_finish = errand.finish
errand.finish = lambda e: FINISHED.append(e.id) or _finish(e)


def brief(**k):
    return errand.Errand(**{"business": "Luigi's", "party_size": 4, "date": "Friday", "time_from": "19:00",
                            "time_to": "20:00", "name": "Adam", **k})


def sign(path, params=None, ws=False):
    base = URL.replace("http://", "ws://") if ws else URL
    return signature(base + path, params or {}, config.TWILIO_AUTH_TOKEN)


async def call(reason, prompts):
    """Be Twilio: the voice webhook, then the relay: setup + what 'the restaurant' says. -> (twiml, lines, ended)"""
    form = {"Direction": "outbound-api", "To": config.MY_PHONE, "From": config.TWILIO_NUMBER}
    path = f"/twilio/voice?reason={reason}"
    async with aiohttp.ClientSession() as s:
        async with s.post(URL + path, data=form, headers={"X-Twilio-Signature": sign(path, form)}) as r:
            twiml = await r.text()
        said, ended = [], False
        async with s.ws_connect(URL.replace("http://", "ws://") + "/twilio/relay",
                                headers={"X-Twilio-Signature": sign("/twilio/relay", ws=True)}) as ws:
            await ws.send_json({"type": "setup", "callSid": "CA1", "direction": "outbound-api", "to": config.MY_PHONE,
                                "from": config.TWILIO_NUMBER, "customParameters": {"reason": reason}})
            for p in prompts:
                if ended:
                    break
                await ws.send_json({"type": "prompt", "voicePrompt": p, "last": True})
                reply = ""
                while True:
                    msg = await asyncio.wait_for(ws.receive(), 10)
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        ended = True
                        break
                    data = json.loads(msg.data)
                    if data.get("type") == "end":
                        ended = True
                        continue
                    reply += data["token"]
                    if data["last"]:
                        break
                said.append(reply.strip())
            deadline = time.time() + 5  # (keep listening: a goodbye may come before the call ends)
            while not ended and time.time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.receive(), max(0.1, deadline - time.time()))
                except asyncio.TimeoutError:
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    ended = True
                    break
                data = json.loads(msg.data)
                if data.get("type") == "end":
                    ended = True
                elif data.get("token", "").strip():
                    said.append(data["token"].strip())
    return twiml, said, ended


def run_errand(e, prompts):
    errand.start(e)
    reason = TW.calls[-1]["url"].split("reason=")[1]
    return asyncio.run(call(reason, prompts))


# =============================================================================================== 1. the rules
print("\n1. Hard limits enforced by code")
e = brief()
t.check("a booking that fits the brief is accepted", errand.fits(e, {"time": "19:30", "party_size": 4, "date": "Friday"})[0])
t.check("outside the time range -> not accepted", not errand.fits(e, {"time": "21:00", "party_size": 4})[0])
t.check("both ends of the range count (8:00 for 7-8)", errand.fits(e, {"time": "20:00", "party_size": 4})[0])
t.check("a different party size / another day -> not accepted", not errand.fits(e, {"time": "19:30", "party_size": 6})[0]
        and not errand.fits(e, {"time": "19:30", "party_size": 4, "date": "Saturday"})[0]
        and not errand.fits(e, {"time": "19:30", "party_size": 4, "date": "tomorrow"})[0])
for bad in ("Sure, his number is 415 555 0199.", "You can email adam@example.com", "My card number is 4111 1111 1111 1111",
            "We can pay the deposit now"):
    t.check(f"never said: {bad[:40]!r}", not errand.safe_to_say(e, bad))
t.check("times as said / transcribed: 'can't 7PM, but 8PM' -> 20:00; '830', 'eight thirty', 'half past seven'; "
        "'a table for 8 people' and a phone number aren't times",
        errand.offered_times(e, "We can't 7PM, but we can do a 8PM.") == ["20:00"]
        and errand.offered_times(e, "I have 830. Do you think it's good?") == ["20:30"]
        and errand.offered_times(e, "how about eight thirty") == ["20:30"]
        and errand.offered_times(e, "half past seven works") == ["19:30"]
        and errand.offered_times(e, "We have a table for 8 people") == []
        and errand.offered_times(e, "call 415 555 0199") == [])
for ok_line in ("Oh, 7:30 is perfect!", "Perfect, 7:30 for 4 people under Adam.", "Great, see you Friday at 8!",
                "Hmm, 9 is a bit late for us - anything closer to 8?", "Great, I'll check with Adam if 9 works.",
                "Perfect, a table for 4 then."):
    t.check(f"its words may say: {ok_line!r}", not errand.agrees_outside(e, ok_line), errand.agrees_outside(e, ok_line))
for bad_line in ("Great, 9 works!", "Perfect, we'll take 8:30.", "Sounds good, see you Saturday!",
                 "Perfect, eight thirty it is."):
    t.check(f"its words may NOT agree to: {bad_line!r}", errand.agrees_outside(e, bad_line))
t.check("'calling for my boss, Adam Azzouz' in the plain opening (used only if the model can't be reached)",
        "calling for my boss, Adam Azzouz" in brief(name="Adam Azzouz", relation="boss").opening())
t.check("the model is given the brief and who it's calling for; no script ('Right now' directives are gone)",
        "your boss: boss, Adam Azzouz" in errand._brief(brief(name="Adam Azzouz", relation="boss"))
        and "Right now" not in errand.SYSTEM and "accepting" in errand.SYSTEM)

# =============================================================================================== 2. calls
print("\n2. Calls through the real phone server (simulated Twilio)")
SMS.clear()
QUEUE[:] = [
    {"status": "talking", "say": "Hi! I'm calling for my boss, Adam Azzouz - I'm an AI assistant. Any chance of a table "
                                 "for 4 this Friday, around 7 or 8?"},
    {"status": "accepting", "booking": {"time": "19:30", "party_size": 4, "date": "Friday"}, "say": "Oh, 7:30 is perfect!"},
    {"status": "booked", "booking": {"time": "19:30", "party_size": 4, "date": "Friday", "name": "Adam Azzouz"},
     "say": "Perfect, 7:30 for 4 under Adam Azzouz. Thank you so much!"},
    {"status": "goodbye", "say": "Bye now!"}]
e = brief(name="Adam Azzouz", relation="boss")
twiml, said, ended = run_errand(e, ["Luigi's, hi! How are you doing?", "We can do 7:30.", "Great, you're booked for 7:30.",
                                    "Okay, bye!"])
t.check("the call goes to THEIR OWN phone (test mode); the number's settings untouched",
        TW.calls[-1]["to"] == config.MY_PHONE and TW.calls[-1]["from"] == config.TWILIO_NUMBER)
t.check("no scripted greeting: it waits for the restaurant to answer (no welcomeGreeting); a natural female voice; "
        "echo-resistant listening", "welcomeGreeting" not in twiml and 'ttsProvider="ElevenLabs"' in twiml
        and 'voice="cgSgspJ2msm6clMCkdW9-flash_v2_5-1.0_0.45_0.8"' in twiml and "Azzouz" in twiml
        and 'speechModel="nova-3-general"' in twiml
        and 'interruptible="speech"' in twiml and 'interruptSensitivity="low"' in twiml, twiml)
t.check("the model's words are what's said (it leads the conversation; its first reply may be 3 sentences)",
        "Adam Azzouz" in said[0] and "7:30 is perfect" in said[1], said)
t.check("booked when they confirmed (checked: inside the brief); their 'bye' gets a bye; then the call ends",
        e.status == "booked" and e.outcome["booking"]["time"] == "19:30" and e.outcome["booking"]["party_size"] == 4
        and "Bye now" in said[3] and ended and e.over, (e.status, e.outcome, said, ended))
t.check("the outcome is texted once (calendar NOT changed) and saved", FINISHED.count(e.id) == 1 and "Booked at Luigi's" in SMS[-1]
        and json.loads(config.ERRANDS_FILE.read_text())[-1]["status"] == "booked", SMS)

SEEN.clear()
QUEUE[:] = [{"status": "talking", "say": "Hi! Any chance of a table for 4 this Friday, around 7 or 8?"},
            {"status": "accepting", "booking": {"time": "21:00", "party_size": 4}, "say": "Sure, 9 works great!"},
            {"status": "talking", "say": "Hmm, 9 is a little late for us - anything closer to 8?"}]
e = brief()
twiml, said, ended = run_errand(e, ["Hi, Luigi's.", "We only have 9PM."])
t.check("the model agreeing to something outside the brief (9 pm): never said; it's told why and answers again",
        "9 works" not in " ".join(said) and "closer to 8" in said[1] and "Code check" in SEEN[-1][1]
        and "9 is outside 7 and 8 pm" in SEEN[-1][1], (said, SEEN[-1:]))

QUEUE[:] = [{"status": "talking", "say": "Hi! Any chance of a table for 4 this Friday?"},
            {"status": "booked", "booking": {"time": "21:00", "party_size": 4}, "say": "Great, see you at 9!"},
            {"status": "booked", "booking": {"time": "21:00", "party_size": 4}, "say": "Great, see you at 9!"}]
e = brief()
twiml, said, ended = run_errand(e, ["Hi, Luigi's.", "Yeah we can squeeze you in at 9, you're booked."])
t.check("...insisting twice: still never said; it'll check with Adam; 'needs your decision', nothing booked",
        e.status == "needs_you" and "see you at 9" not in " ".join(said) and "check with Adam" in said[1],
        (e.status, said))

QUEUE[:] = [{"status": "talking", "say": "Hi! A table for 4 this Friday?"},
            {"status": "talking", "say": "Perfect, 7:30 for four please."},
            {"status": "booked", "booking": {"party_size": 4}, "say": "Thank you so much!"}]
e = brief()
twiml, said, ended = run_errand(e, ["Hi, Luigi's.", "Sure, how about 7:30?", "You're all set, see you Friday."])
t.check("'booked' without a time in the decision (seen live): the time they offered is used, if it fits",
        e.status == "booked" and e.outcome["booking"]["time"] == "19:30", (e.status, e.outcome, said))

QUEUE[:] = [{"status": "talking", "say": "Hi! Any chance of a table for 4 this Friday?"},
            {"status": "talking", "say": "Great, 9 works for us! Thanks so much."}]
e = brief()
twiml, said, ended = run_errand(e, ["Hi, Luigi's.", "We have 9."])
t.check("words that agree to 9 pm while the decision said 'talking': stopped before they're said",
        "9 works" not in " ".join(said) and "check with Adam" in said[1], said)

QUEUE[:] = [{"status": "talking", "say": "Hi!"}, {"status": "talking", "say": "Sure, his number is 415 555 0199."}]
e = brief()
twiml, said, ended = run_errand(e, ["Hi!", "Can I get a phone number for the booking?"])
t.check("a leak in the model's words is blocked before it's spoken", "0199" not in " ".join(said)
        and "can't share" in said[1], said)

QUEUE[:] = [{"status": "talking", "say": "Hi! A table for 4 this Friday, around 7 or 8?"},
            {"status": "needs_you", "say": "Aw, okay - I'll check with Adam and call you right back. Thanks!"},
            {"status": "accepting", "booking": {"time": "20:00", "party_size": 4}, "say": "Oh, 8 is perfect!"},
            {"status": "booked", "booking": {"time": "20:00", "party_size": 4}, "say": "Amazing, thank you!"},
            {"status": "goodbye", "say": "Bye!"}]
e = brief()
twiml, said, ended = run_errand(e, ["Hi", "Nope, only 9.", "Oh wait, actually we can do 8!", "Great, you're all set.",
                                    "Bye!"])
t.check("it never hangs up on them: after its wrap-up they kept talking, the conversation went on, 8 was booked; "
        "one text, with the final outcome", e.status == "booked" and e.outcome["booking"]["time"] == "20:00" and ended
        and FINISHED.count(e.id) == 1 and "Booked" in SMS[-1], (e.status, said, SMS[-2:]))

QUEUE[:] = [{"status": "talking", "say": "Hi! A table for 4 this Friday?"},
            {"status": "goodbye", "say": "Oh, sure!"},
            {"status": "talking", "say": "It's under Adam."}]
e = brief()
twiml, said, ended = run_errand(e, ["Hi", "Thanks - what name is it under?", "Got it."])
t.check("the model wanting to end the call while they're asking something: the call goes on", len(said) >= 3 and "under Adam" in said[2], (e.status, said))

QUEUE[:] = [{"status": "talking", "say": "Hi! A table for 4 this Friday?"},
            {"status": "needs_you", "say": "Okay, I'll check with Adam and call you back. Thanks!"}]
e = brief()
twiml, said, ended = run_errand(e, ["Hi", "We only have 9."])
t.check("no answer to its wrap-up: it still says a friendly bye, then the call ends (never silence)",
        e.status == "needs_you" and "bye" in said[-1].lower() and ended, (said, ended))

QUEUE[:] = [{"status": "wrong_number", "say": "Oh, I'm so sorry - wrong number! Have a good night."},
            {"status": "goodbye", "say": "Bye!"}]
e = brief()
twiml, said, ended = run_errand(e, ["Hello? No, this is Mike's Garage.", "No worries, bye."])
t.check("a wrong number: an apology, recorded as failed (wrong number); the call ends", e.status == "failed"
        and "wrong number" in e.outcome["why"] and ended, (e.status, e.outcome, said))

errand.MAX_TURNS = 3
QUEUE.clear()
e = brief()
twiml, said, ended = run_errand(e, ["hello?"] * 5)
errand.MAX_TURNS = 20
t.check("a call going nowhere ends after MAX_TURNS, politely, nothing booked", e.status == "no_deal" and ended, e.status)


def boom(e, tr):
    raise RuntimeError("model down")


errand.THINK = boom
e = brief()
twiml, said, ended = run_errand(e, ["Hi, Luigi's!", "So what can I do for you?"])
errand.THINK = fake_think
t.check("the model failing: a plain line instead (never silence); then 'failed' recorded",
        said[0] == e.opening() and e.status == "failed", (said, e.status))

e = brief()
errand.start(e)
asyncio.run(call(TW.calls[-1]["url"].split("reason=")[1], ["Hello?"]))
time.sleep(0.5)
t.check("they hang up before an outcome: recorded (not left 'calling'); the call is over",
        e.status in ("no_deal", "failed") and e.over, e.status)

form = {"Direction": "outbound-api", "To": "+15550000000", "From": config.TWILIO_NUMBER}


async def other_number():
    async with aiohttp.ClientSession() as s:
        path = "/twilio/voice?reason=x"
        async with s.post(URL + path, data=form, headers={"X-Twilio-Signature": sign(path, form)}) as r:
            return await r.text()


t.check("a call to any other number is rejected by the server (only their own phone in test mode)",
        "<Reject/>" in asyncio.run(other_number()))

# =============================================================================================== 3. nobody speaks
print("\n3. If nobody speaks first, the model starts the call itself")
errand.OPEN_WAIT_S = 0.2
SEEN.clear()
QUEUE[:] = [{"status": "talking", "say": "Hi there! Is this Luigi's?"}]
sent = []
e = brief()
s = errand.ErrandSession(e, lambda text, last: sent.append(text))
time.sleep(0.6)
errand.OPEN_WAIT_S = 60
t.check("after a few seconds of silence the model is asked (nothing said yet) and its hello is spoken",
        SEEN and SEEN[0][0] == 0 and "Is this Luigi's?" in " ".join(sent), (SEEN, sent))
t.check("...it's told the call just connected", "nobody has spoken yet" in errand._messages(e, [])[-1]["content"])

# =============================================================================================== 4. streaming
print("\n4. The model's words are streamed sentence by sentence; its decision is checked before any word")
from types import SimpleNamespace as NS  # noqa: E402

import room_agent.llm.client as anthropic_mod  # noqa: E402
import room_agent.llm.openai_backend as oai  # noqa: E402

ASKED = []


def fake_stream(*pieces):
    def create(**kw):
        ASKED.append(kw)
        out = [NS(choices=[NS(delta=NS(content=p))], usage=None) for p in pieces]
        return iter(out + [NS(choices=[], usage=NS(prompt_tokens=300, completion_tokens=40))])
    client = NS(with_options=lambda **k: NS(chat=NS(completions=NS(create=create))))
    oai.openai_client = lambda: client


errand.THINK = None
sent = []
e = brief()
e.transcript[:] = [("jarvis", "Hi, is this Luigi's?"), ("them", "Yes."), ("jarvis", "A table for 4 this Friday?")]
s = errand.ErrandSession(e, lambda text, last: sent.append((text, last)))
s.opened = True
fake_stream('{"status": "talking"}\n', "Got it, ", "four people. ", "Do you have ", "anything around 7?")
s.answer("Hi, how many people?")
words = [x for x, last in sent if x.strip()]
t.check("header first, then the words go out sentence by sentence as they arrive",
        words == ["Got it, four people. ", "Do you have anything around 7? "] and sent[-1] == ("", True), sent)
t.check("...the errand model is used (ERRAND_MODEL, minimal thinking), and usage is recorded",
        ASKED[-1]["model"] == config.ERRAND_MODEL == "gpt-5" and ASKED[-1].get("reasoning_effort") == "minimal"
        and e.cost_usd > 0, (ASKED[-1]["model"], e.cost_usd))
sent.clear()
fake_stream('{"status": "talking"}\n', "Oh nice. ", "That sounds lovely. ", "I mean it, really. ", "Anyway, Friday?")
s.answer("We just redid the patio!")
words = [x for x, last in sent if x.strip()]
t.check("at most two short sentences per reply (the rest isn't said)", words == ["Oh nice. ", "That sounds lovely. "], words)
sent.clear()
fake_stream('{"status": "talking"}\n', "Sure. ", "Their number is 415 555 0123. ", "Anything else?")
s.answer("Can I get a phone number?")
words = " ".join(x for x, last in sent)
t.check("a sentence that would leak a number is stopped mid-stream; nothing after it is said",
        "0123" not in words and "can't share" in words and "Anything else" not in words, words)
sent.clear()
fake_stream('{"status": "accepting", "booking": {"time": "21:00", "party_size": 4}}\n', "9 works, ", "thank you!")
QUEUE[:] = []
s.answer("We have 9.")
words = " ".join(x for x, last in sent)
t.check("a streamed decision to accept 9 pm: not a word of it is said (the model is asked again)",
        "9 works" not in words, words)
t.check("the decision is split off however the model lays it out (own line, same line, missing); never spoken",
        errand.split_header('{"status": "talking", "booking": {"time": "19:00"}} Sorry, 7 or 7:30?')[:2]
        == ({"status": "talking", "booking": {"time": "19:00"}}, "Sorry, 7 or 7:30?")
        and errand.split_header('{"status": "booked"}\nGreat!')[:2] == ({"status": "booked"}, "Great!")
        and errand.split_header("Just words.")[:2] == ({"status": "talking"}, "Just words.")
        and errand.split_header('{"status": "talk', final=True)[1] == "")
sent.clear()
fake_stream('{"status":"talking","booking":{"date":"Friday","time":"19:00","party_size":4}} ', "Sorry, which ",
            "time works better?")
s.answer("Can you hear me?")
words = " ".join(x for x, last in sent)
t.check("a decision on the same line as the words (seen on a live call) is NEVER read out", "{" not in words
        and "status" not in words and "which time works better?" in words, words)
t.check("...and a stray data fragment inside the words is removed", "{" not in errand._natural(
    'Sure {"status":"talking"} thing.') and "status" not in errand._natural('Sure "status": "talking", thing.'))


class FakeClaudeStream:
    def __init__(self, pieces):
        self.text_stream = iter(pieces)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return NS(usage=NS(input_tokens=500, output_tokens=30))


CLAUDE = []
anthropic_mod.client = lambda: NS(messages=NS(stream=lambda **kw: CLAUDE.append(kw) or FakeClaudeStream(
    ['{"status": "talking"}\n', "Sure, take ", "your time!"])))
config.ERRAND_MODEL = "claude-sonnet-5-5"
sent.clear()
before = e.cost_usd
s.answer("Hold on one second.")
config.ERRAND_MODEL = "gpt-5"
words = " ".join(x for x, last in sent)
t.check("ERRAND_MODEL=claude-*: Claude runs the call (system prompt apart, turns alternate, starting with them)",
        "Sure, take your time!" in words and CLAUDE and "Brief:" in CLAUDE[-1]["system"]
        and CLAUDE[-1]["messages"][0]["role"] == "user"
        and all(a["role"] != b["role"] for a, b in zip(CLAUDE[-1]["messages"], CLAUDE[-1]["messages"][1:]))
        and e.cost_usd > before, (words, CLAUDE[-1]["messages"] if CLAUDE else None))

print("\n5. Echo")
t.check("its own words picked up by their microphone are recognized as an echo, their own words aren't",
        errand.echo_of("Hello? Hi there. Is this Luigi", "Hi there - is this Luigi's Trattoria?")
        and not errand.echo_of("Yes, Luigi's, how can I help?", "Hi there - is this Luigi's Trattoria?")
        and not errand.echo_of("So what can I do for you?", brief().opening())
        and not errand.echo_of("Hi, Luigi's here!", "Hi there - is this Luigi's Trattoria?"))
errand.THINK = fake_think
SEEN.clear()
QUEUE[:] = [{"status": "talking", "say": "Hi there - is this Luigi's Trattoria?"}, {"status": "talking", "say": "Great!"}]
sent = []
e = brief()
s = errand.ErrandSession(e, lambda text, last: sent.append(text))
s.answer("Hello?")
s.answer("Hello? Hi there. Is this Luigi")
t.check("an echo of its own line is ignored (the model isn't even asked)", len(SEEN) == 1 and e.turns == 1, (SEEN, e.turns))
t.done("ERRAND CALL TESTS")
