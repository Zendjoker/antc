"""Errand calls (phone/errand.py): Jarvis phones a restaurant for you in a restricted mode. Offline: the real phone
server on a local port, a simulated Twilio (signed webhook + ConversationRelay WebSocket), a scripted model.

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


def brief(**k):
    return errand.Errand(**{"business": "Luigi's", "party_size": 4, "date": "Friday", "time_from": "19:00",
                            "time_to": "20:00", "name": "Adam", **k})


def script(*replies):
    it = iter(replies)
    errand.THINK = lambda e, transcript: next(it)


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
                try:  # (the "end" message follows the last line when the call is over)
                    msg = await asyncio.wait_for(ws.receive(), 0.4)
                    ended = ended or msg.type != aiohttp.WSMsgType.TEXT or json.loads(msg.data).get("type") == "end"
                except asyncio.TimeoutError:
                    pass
                if ended:
                    break
            try:
                msg = await asyncio.wait_for(ws.receive(), 2)
                ended = ended or msg.type != aiohttp.WSMsgType.TEXT or json.loads(msg.data).get("type") == "end"
            except asyncio.TimeoutError:
                pass
    return twiml, said, ended


def run_errand(e, prompts):
    errand.start(e)
    reason = TW.calls[-1]["url"].split("reason=")[1]
    return asyncio.run(call(reason, prompts))


# =============================================================================================== 1. the rules
print("\n1. Rules enforced by code")
e = brief()
t.check("the opening says it's an AI assistant calling on their behalf, with the brief only",
        "AI assistant" in e.opening() and "Adam" in e.opening() and "4" in e.opening() and "Friday" in e.opening())
t.check("a booking that fits the brief is accepted", errand.fits(e, {"time": "19:30", "party_size": 4, "date": "Friday"})[0])
t.check("outside the time window -> not accepted", not errand.fits(e, {"time": "21:00", "party_size": 4})[0])
t.check("'7:30 pm' style times are understood", errand.fits(e, {"time": "7:30 pm", "party_size": 4})[0])
t.check("a different party size -> not accepted", not errand.fits(e, {"time": "19:30", "party_size": 6})[0])
t.check("another day -> not accepted", not errand.fits(e, {"time": "19:30", "party_size": 4, "date": "Saturday"})[0])
for bad in ("Sure, his number is 415 555 0199.", "You can email adam@example.com", "My card number is 4111 1111 1111 1111",
            "We can pay the deposit now"):
    t.check(f"never said: {bad[:40]!r}", not errand.safe_to_say(e, bad))
t.check("a normal line is fine", errand.safe_to_say(e, "Great, 7:30 for 4 people under Adam, thank you!"))
t.check("times are spoken naturally ('between 7 and 8 pm')", "between 7 and 8 pm" in e.opening(), e.opening())

# =============================================================================================== 2. calls
print("\n2. Calls through the real phone server (simulated Twilio)")
script({"say": "Perfect, 7:30 for four under Adam. Thank you!", "status": "booked",
        "booking": {"date": "Friday", "time": "19:30", "party_size": 4, "name": "Adam", "reference": "A12"}})
e = brief()
twiml, said, ended = run_errand(e, ["Yes, we have 7:30 for four, under what name? Okay, Adam, booked, reference A12."])
t.check("the call goes to THEIR OWN phone (test mode), from Jarvis's number; the number's settings untouched",
        TW.calls[-1]["to"] == config.MY_PHONE and TW.calls[-1]["from"] == config.TWILIO_NUMBER)
t.check("Twilio is told to open with the AI-assistant introduction", "AI assistant calling on behalf of Adam" in twiml, twiml)
t.check("a booking inside the brief: confirmed, the call ends", e.status == "booked" and ended and said
        and "7:30" in said[-1], (e.status, said, ended))
t.check("the outcome is texted to them (calendar NOT changed)", SMS and "Booked at Luigi's" in SMS[-1]
        and "Not added to your calendar" in SMS[-1], SMS[-1:])
t.check("...and saved (errands.json)", json.loads(config.ERRANDS_FILE.read_text())[-1]["status"] == "booked")

script({"say": "Sure, 9pm works, see you then!", "status": "booked",
        "booking": {"date": "Friday", "time": "21:00", "party_size": 4}})
e = brief()
twiml, said, ended = run_errand(e, ["We only have 9pm, is that okay?"])
t.check("the model tried to accept 9pm (outside 7-8): code refused, said it'll check with them, nothing booked",
        e.status == "needs_you" and "check with Adam" in said[-1] and "9pm works" not in " ".join(said) and ended,
        (e.status, said))
t.check("...they're told it needs their decision", "needs your decision" in SMS[-1] and "Nothing was booked" in SMS[-1],
        SMS[-2:])

script({"say": "Of course, his number is 415 555 0199.", "status": "talking"},
       {"say": "Great, 7:00 for four. Thanks!", "status": "booked", "booking": {"time": "19:00", "party_size": 4}})
e = brief()
twiml, said, ended = run_errand(e, ["Can I have a phone number for the booking?", "Okay, 7 o'clock for four, done."])
t.check("asked for a phone number: the model's leak was blocked before it was spoken; the call continued",
        "0199" not in " ".join(said) and "can't share that" in said[0] and e.status == "booked", (said, e.status))

script({}, {"say": "No problem, thank you anyway!", "status": "declined"})
e = brief()
twiml, said, ended = run_errand(e, ["Sorry, we're fully booked.", "No, nothing at all on Friday."])
t.check("an empty model reply -> 'could you say that again', never the privacy line; then a decline ends the call",
        said[0] == "Sorry, could you say that again?" and e.status == "declined" and ended, (said, e.status))

script({"say": "Under Adam, please.", "status": "booked", "booking": {"time": "19:30", "party_size": 4}},
       {"say": "Thank you!", "status": "booked", "booking": {"time": "19:30", "party_size": 4}})
e = brief()
twiml, said, ended = run_errand(e, ["7:30 works. What name should I put it under?", "Okay Adam, you're booked."])
t.check("'booked' is accepted only after THEY confirm (a question about the name isn't a confirmation)",
        said[0] == "Under Adam, please." and len(said) == 2 and e.status == "booked", (said, e.status))

t.check("offered times are read by code: 'can't 7PM, but we can do 8PM' -> 20:00 offered, 19:00 not",
        errand.offered_times(brief(), "We can't 7PM, but we can do a 8PM.") == ["20:00"])
script({"say": "My client asked between 19:00 and 20:00; I'll check and call back.", "status": "needs_you"},
       {"say": "Thank you!", "status": "booked", "booking": {"party_size": 4}})
e = brief()
twiml, said, ended = run_errand(e, ["We can't do 7PM, but we can do 8PM. Does that sound good?", "Great, you're booked."])
t.check("they offer 8 pm (the window's end) and the model hesitates: code accepts it in plain words, then it's booked "
        "at 20:00", said[0].startswith("Yes, 8 pm works for 4") and e.status == "booked"
        and e.outcome["booking"]["time"] == "20:00", (said, e.status, e.outcome))

script(*[{"say": "Could you repeat that?", "status": "talking"}] * 30)
errand.MAX_TURNS = 3
e = brief()
twiml, said, ended = run_errand(e, ["hello?"] * 5)
errand.MAX_TURNS = 16
t.check("a call going nowhere ends after MAX_TURNS, politely, nothing booked", e.status == "no_deal" and ended
        and "call back" in said[-1], (e.status, said[-1:]))


def boom(e, tr):
    raise RuntimeError("model down")


errand.THINK = boom
e = brief()
twiml, said, ended = run_errand(e, ["Hello, Luigi's?"])
t.check("the model failing: it apologizes, ends the call, records 'failed'", e.status == "failed" and ended)

script({"say": "Hmm, let me think.", "status": "talking"})
e = brief()
errand.start(e)
asyncio.run(call(TW.calls[-1]["url"].split("reason=")[1], ["Hold on"]))
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
t.done("ERRAND CALL TESTS")
