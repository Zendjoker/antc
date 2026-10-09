"""Errand calls (phone/errand.py): Jarvis phones a restaurant for you in a restricted mode. Offline: the real phone
server on a local port, a simulated Twilio (signed webhook + ConversationRelay WebSocket), a fake model.

The fake model "says" whatever code asked for (so each test sees the DECISION code made), or plays scripted replies
for the turns where the model decides itself.

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
QUEUE = []  # the model's own proposals for turns where code made no decision


def fake_think(e, transcript):
    d = getattr(e, "_directive", "")
    if d:  # (code decided: the fake "says" the decision, as one sentence)
        return {"status": "talking", "say": d.replace(". ", "; ").rstrip(".") + "."}
    return QUEUE.pop(0) if QUEUE else {"status": "talking", "say": "Mm-hm, got it."}


errand.THINK = fake_think


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
print("\n1. Rules enforced by code")
e = brief()
t.check("a booking that fits the brief is accepted", errand.fits(e, {"time": "19:30", "party_size": 4, "date": "Friday"})[0])
t.check("outside the time range -> not accepted", not errand.fits(e, {"time": "21:00", "party_size": 4})[0])
t.check("both ends of the range count (8:00 for 7-8)", errand.fits(e, {"time": "20:00", "party_size": 4})[0])
t.check("a different party size / another day -> not accepted", not errand.fits(e, {"time": "19:30", "party_size": 6})[0]
        and not errand.fits(e, {"time": "19:30", "party_size": 4, "date": "Saturday"})[0])
for bad in ("Sure, his number is 415 555 0199.", "You can email adam@example.com", "My card number is 4111 1111 1111 1111",
            "We can pay the deposit now"):
    t.check(f"never said: {bad[:40]!r}", not errand.safe_to_say(e, bad))
t.check("offered times: 'can't 7PM, but we can do 8PM' -> 20:00; 'we can do 7:30' -> 19:30; 'a table for 8' -> none",
        errand.offered_times(e, "We can't 7PM, but we can do a 8PM.") == ["20:00"]
        and errand.offered_times(e, "Okay, we can do 7:30.") == ["19:30"]
        and errand.offered_times(e, "We have a table for 8 people") == [])
t.check("another day is noticed ('tomorrow', 'Saturday')", errand.other_day(e, "We can do 7 tomorrow")
        and not errand.other_day(e, "Friday at 7 is fine"))
t.check("'calling for my boss, Adam Azzouz' in the plain fallback opening",
        "calling for my boss, Adam Azzouz" in brief(name="Adam Azzouz", relation="boss").opening())

# =============================================================================================== 2. calls
print("\n2. Calls through the real phone server (simulated Twilio)")
e = brief(name="Adam Azzouz", relation="boss")
twiml, said, ended = run_errand(e, ["Luigi's, hi! How are you doing?", "We can do 7:30.", "Great, you're booked for 7:30.",
                                    "Okay, bye!"])
t.check("the call goes to THEIR OWN phone (test mode); the number's settings untouched",
        TW.calls[-1]["to"] == config.MY_PHONE and TW.calls[-1]["from"] == config.TWILIO_NUMBER)
t.check("no scripted greeting: it waits for the restaurant to answer (no welcomeGreeting); a natural female voice; "
        "echo-resistant listening", "welcomeGreeting" not in twiml and 'voice="en-US-Chirp3-HD-Aoede"' in twiml
        and 'interruptible="speech"' in twiml and 'interruptSensitivity="low"' in twiml, twiml)
t.check("its first words answer how they picked up, then who it's calling for and why, and that it's an AI",
        "react naturally" in said[0] and "your boss, Adam Azzouz" in said[0] and "AI" in said[0] and "Friday" in said[0],
        said[0])
t.check("an offer inside the brief (7:30): code accepts it (the model words it)", "7:30" in said[1]
        and "works perfectly" in said[1], said[1])
t.check("their confirmation: booked, repeated back; their 'bye' gets a goodbye; then the call ends",
        e.status == "booked" and e.outcome["booking"]["time"] == "19:30" and "repeat it back" in said[2]
        and "goodbye" in said[3].lower() and ended, (e.status, said, ended))
t.check("the outcome is texted to them (calendar NOT changed) and saved", SMS and "Booked at Luigi's" in SMS[-1]
        and json.loads(config.ERRANDS_FILE.read_text())[-1]["status"] == "booked", SMS[-1:])

e = brief()
twiml, said, ended = run_errand(e, ["Hello?", "We only have 9PM.", "No, sorry, that's all we have.", "Bye."])
t.check("an offer outside the brief: first it asks for something closer (not a flat no)", "late" in said[1]
        and "closer" in said[1] and "Don't accept" in said[1], said[1])
t.check("...nothing else: it says it'll check with Adam, warmly; 'needs your decision'; nothing booked",
        e.status == "needs_you" and "check with Adam" in said[2] and "Nothing was booked" in SMS[-1], (e.status, said))

QUEUE[:] = [{"status": "booked", "booking": {"time": "21:00", "party_size": 4}, "say": "Sure, 9 works, see you!"}]
e = brief()
twiml, said, ended = run_errand(e, ["Hi, Luigi's.", "Yeah we can squeeze you in, you're booked."])
t.check("the model wanting to accept something outside the brief: refused by code before a word of it is said",
        e.status == "needs_you" and "9 works" not in " ".join(said) and "Don't accept" in " ".join(said), (e.status, said))

QUEUE[:] = [{"status": "talking", "say": "Sure, his number is 415 555 0199."}]
e = brief()
twiml, said, ended = run_errand(e, ["Hi!", "Can I get a phone number for the booking?"])
t.check("a leak in the model's words is blocked before it's spoken", "0199" not in " ".join(said)
        and "can't share" in said[1], said)

e = brief()
twiml, said, ended = run_errand(e, ["Hi", "We only have 9PM.", "Nope, that's it.", "Oh wait, actually we can do 8!",
                                    "Great, you're all set.", "Bye!"])
t.check("it never hangs up on them: after its closing words they kept talking, the conversation reopened, 8 was "
        "accepted and booked", e.status == "booked" and e.outcome["booking"]["time"] == "20:00" and ended, (e.status, said))

e = brief(name="Adam Azzouz", relation="boss")
twiml, said, ended = run_errand(e, ["Hi", "We have 8:30.", "Call your boss and let him know."])
t.check("'call your boss and let him know' -> agrees warmly to check with Adam; it's the host, not its boss",
        e.status == "needs_you" and "check with Adam" in " ".join(said[2:]), (said, e.status))

e = brief()
twiml, said, ended = run_errand(e, ["Hi", "Hold on one second.", "Okay, 7:30 works.", "You're all set."])
t.check("'hold on' -> patient; then 7:30 accepted and booked", "hold" in said[1] and e.status == "booked", (said, e.status))

e = brief()
twiml, said, ended = run_errand(e, ["Hi", "We can do 7 tomorrow.", "No, nothing on Friday, sorry."])
t.check("another day is never accepted: it asks about Friday, then says it'll check", "Friday" in said[1]
        and e.status == "needs_you" and not e.agreed_time, (said, e.status))

e = brief()
twiml, said, ended = run_errand(e, ["Hi", "We only have 9.", "No."])
t.check("no answer to its closing words: it still says a friendly bye, then the call ends (never silence)",
        e.status == "needs_you" and "bye" in said[-1].lower() and ended, (said, ended))

errand.MAX_TURNS = 3
e = brief()
twiml, said, ended = run_errand(e, ["hello?"] * 5)
errand.MAX_TURNS = 16
t.check("a call going nowhere ends after MAX_TURNS, politely, nothing booked", e.status == "no_deal" and ended, e.status)


def boom(e, tr):
    raise RuntimeError("model down")


errand.THINK = boom
e = brief()
twiml, said, ended = run_errand(e, ["Hi, Luigi's!", "So what can I do for you?"])
errand.THINK = fake_think
t.check("the model failing: a plain fallback line instead (never silence); then 'failed' recorded",
        said[0] == e.opening() and e.status == "failed", (said, e.status))

e = brief()
errand.start(e)
asyncio.run(call(TW.calls[-1]["url"].split("reason=")[1], ["Hello?"]))
time.sleep(0.5)
t.check("they hang up before an outcome: recorded (not left 'calling')", e.status in ("no_deal", "failed"), e.status)

form = {"Direction": "outbound-api", "To": "+15550000000", "From": config.TWILIO_NUMBER}


async def other_number():
    async with aiohttp.ClientSession() as s:
        path = "/twilio/voice?reason=x"
        async with s.post(URL + path, data=form, headers={"X-Twilio-Signature": sign(path, form)}) as r:
            return await r.text()


t.check("a call to any other number is rejected by the server (only their own phone in test mode)",
        "<Reject/>" in asyncio.run(other_number()))

# =============================================================================================== 3. nobody speaks
print("\n3. If nobody speaks first, it starts the call itself")
errand.OPEN_WAIT_S = 0.2
sent = []
e = brief()
s = errand.ErrandSession(e, lambda text, last: sent.append(text))
time.sleep(0.6)
errand.OPEN_WAIT_S = 60
t.check("after a few seconds of silence: a hello and 'is this Luigi's?' (not the whole request)",
        any("is Luigi's" in x or "this is Luigi's" in x for x in sent), sent)

# =============================================================================================== 4. streaming
print("\n4. The model's words are streamed sentence by sentence; its decision is checked before any word")
from types import SimpleNamespace as NS  # noqa: E402

import room_agent.llm.openai_backend as oai  # noqa: E402


def fake_stream(*pieces):
    def create(**kw):
        out = [NS(choices=[NS(delta=NS(content=p))], usage=None) for p in pieces]
        return iter(out + [NS(choices=[], usage=NS(prompt_tokens=300, completion_tokens=40))])
    client = NS(with_options=lambda **k: NS(chat=NS(completions=NS(create=create))))
    oai.openai_client = lambda: client


errand.THINK = None
sent = []
e = brief()
s = errand.ErrandSession(e, lambda text, last: sent.append((text, last)))
s.opened = s.introduced = True
fake_stream('{"status": "talking"}\n', "Got it, ", "four people. ", "Do you have ", "anything around 7?")
s.answer("Hi, how many people?")
words = [x for x, last in sent if x.strip()]
t.check("header first, then the words go out sentence by sentence as they arrive",
        words == ["Got it, four people. ", "Do you have anything around 7? "] and sent[-1] == ("", True), sent)
t.check("...usage recorded (the call's cost is tracked)", e.cost_usd > 0)
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

print("\n5. Wrong number")
errand.THINK = fake_think
errand.OPEN_WAIT_S = 0.2
sent = []
e = brief()
s = errand.ErrandSession(e, lambda text, last: sent.append(text), hang_up=lambda: sent.append("<END>"))
time.sleep(0.6)
errand.OPEN_WAIT_S = 60
s.answer("No.")
time.sleep(0.6)
t.check("'is this Luigi's?' - 'No.' -> an apology for the wrong number, no request, the call ends",
        e.status == "failed" and "wrong number" in " ".join(sent) and "table" not in " ".join(sent[1:]), sent)
t.done("ERRAND CALL TESTS")
