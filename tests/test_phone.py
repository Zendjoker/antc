"""Phone mode end to end, with nothing real: the actual server on a local port, a simulated Twilio (signed webhooks +
the ConversationRelay WebSocket), simulated iPhone Shortcuts, a scripted model, and the simulated Google.

Run:  .venv\\Scripts\\python -m tests.test_phone
"""

import asyncio
import datetime
import io
import json
import logging
import os
import re
import socket
import threading
import time

from tests.harness import setup_env

PORT = (lambda s: (s.bind(("127.0.0.1", 0)), s.getsockname()[1], s.close())[1])(socket.socket())
TMP = setup_env(PHONE_MODE="1", PUBLIC_URL=f"http://127.0.0.1:{PORT}", PHONE_PORT=PORT, PHONE_TOKEN="phone-secret-123456",
                TWILIO_ACCOUNT_SID="AC_test", TWILIO_AUTH_TOKEN="twilio-auth-secret-xyz", TWILIO_NUMBER="+14155550100",
                MY_PHONE="+14155550199", VIP_SENDERS="andrew", JARVIS_VAULT="memory",
                GOOGLE_CLIENT_ID="test-client.apps.googleusercontent.com", GOOGLE_CLIENT_SECRET="GOCSPX-test-secret-123")
LOG = io.StringIO()
logging.basicConfig(level=logging.INFO, stream=LOG, format="%(message)s")

import aiohttp  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.actions.context import env  # noqa: E402
from room_agent.actions.events import events  # noqa: E402
from room_agent.phone import server, state  # noqa: E402
from room_agent.phone.twilio import signature  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

t = Checker()
convo = Conversation()  # (scripted model; anything sent to the room speaker shows up in convo.spoken)
URL = config.PUBLIC_URL
AUTH = {"Authorization": f"Bearer {config.PHONE_TOKEN}"}
SEEN_EVENTS = []
events.on("driving.*", lambda e: SEEN_EVENTS.append(e["name"]))


class FakeTwilio:
    def __init__(self):
        self.calls, self.webhooks = [], []

    def call(self, to, from_, url):
        self.calls.append({"to": to, "from": from_, "url": url})
        return "CA123"

    def set_voice_webhook(self, number, url):
        self.webhooks.append((number, url))
        return "PN1"


TW = FakeTwilio()
server.twilio = lambda: TW
server.start()
time.sleep(0.5)


def sign(path, params=None, ws=False):
    base = URL.replace("http://", "ws://") if ws else URL
    return signature(base + path, params or {}, config.TWILIO_AUTH_TOKEN)


async def post(path, json_body=None, form=None, headers=None):
    async with aiohttp.ClientSession() as s:
        async with s.post(URL + path, json=json_body, data=form, headers=headers or {}) as r:
            return r.status, await r.text()


def run(coro):
    return asyncio.run(coro)


async def ticket(outbound=False):
    """What Twilio does before every relay connection: the signed voice webhook for this call (your carrier-verified
    number calling in, or Jarvis's call reaching you), whose TwiML carries the call's one-time ticket. -> the ticket"""
    form = ({"From": config.TWILIO_NUMBER, "To": config.MY_PHONE, "Direction": "outbound-api", "CallSid": "CA1"}
            if outbound else {"From": config.MY_PHONE, "To": config.TWILIO_NUMBER, "Direction": "inbound",
                              "CallSid": "CA1", "StirVerstat": "TN-Validation-Passed-A"})
    async with aiohttp.ClientSession() as s:
        async with s.post(URL + "/twilio/voice", data=form,
                          headers={"X-Twilio-Signature": sign("/twilio/voice", form)}) as r:
            return re.search(r'name="nonce" value="([^"]+)"', await r.text()).group(1)


async def call(setup, prompts, signed=True):
    """Be Twilio's ConversationRelay for one call: send setup + prompts, collect what Jarvis says."""
    headers = {"X-Twilio-Signature": sign("/twilio/relay", ws=True)} if signed else {}
    said, closed = [], False
    params = {**(setup.get("customParameters") or {}),
              "nonce": await ticket(outbound=not str(setup.get("direction", "inbound")).startswith("inbound"))}
    setup = {**setup, "customParameters": params}
    async with aiohttp.ClientSession() as s:
        try:
            async with s.ws_connect(URL.replace("http://", "ws://") + "/twilio/relay", headers=headers) as ws:
                await ws.send_json({"type": "setup", "callSid": "CA1", **setup})
                for p in prompts:
                    await ws.send_json({"type": "prompt", "voicePrompt": p, "last": True})
                    reply = ""
                    while True:
                        msg = await asyncio.wait_for(ws.receive(), 15)
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            closed = True
                            break
                        data = json.loads(msg.data)
                        reply += data["token"]
                        if data["last"]:
                            break
                    said.append(reply.strip())
                    if closed:
                        break
                if not prompts:
                    msg = await asyncio.wait_for(ws.receive(), 5)
                    closed = msg.type != aiohttp.WSMsgType.TEXT
        except aiohttp.WSServerHandshakeError as e:
            return {"refused": e.status}
    return {"said": said, "closed": closed}


# ---------------------------------------------------------------- iPhone Shortcuts
print("iPhone Shortcuts -> driving:")
async def get(path):
    async with aiohttp.ClientSession() as s:
        async with s.get(URL + path) as r:
            return r.status, await r.json()


t.check("health check answers (so you can test the public URL)", run(get("/phone/health")) == (200, {"ok": True}))
status, _ = run(post("/phone/driving", {"driving": True}))
t.check("no token -> refused, still not driving", status == 401 and not state.is_driving())
status, _ = run(post("/phone/driving", {"driving": True}, headers={"Authorization": "Bearer wrong"}))
t.check("wrong token -> refused", status == 401 and not state.is_driving())
status, body = run(post("/phone/driving", {"driving": True, "source": "CarPlay"}, headers=AUTH))
t.check("CarPlay connected -> driving", status == 200 and state.is_driving() and "driving.started" in SEEN_EVENTS, body)
from room_agent.prompt import runtime_context  # noqa: E402

t.check("Jarvis knows you're driving (short, no screens)", "right now they're driving" in runtime_context("hey"))
status, _ = run(post("/phone/driving", {"driving": "false"}, headers=AUTH))
t.check("CarPlay disconnected -> not driving", status == 200 and not state.is_driving() and "driving.stopped" in SEEN_EVENTS)

print("iPhone -> location:")
from room_agent.tools import location  # noqa: E402

config.LOCATION_SOURCE = "auto"
location._name = lambda loc: {"city": "San Jose", "region": "California", "country": "US"}
location._windows = lambda: {"lat": 37.7, "lon": -122.5, "accuracy_m": 2000, "source": "Windows location (network)",
                             "city": "Daly City", "region": "California"}
status, _ = run(post("/phone/location", {"lat": 37.33, "lon": -121.89}, headers=AUTH))
t.check("the phone's GPS wins while fresh (you're out, the PC is home)", status == 200
        and location.describe(location.locate()) == "San Jose, California" and "iPhone" in location.locate()["source"])
status, _ = run(post("/phone/location", {"lat": 1, "lon": 2}))
t.check("...location without the token is refused", status == 401)

# ---------------------------------------------------------------- Twilio webhooks
print("Twilio: who gets an answer:")
form = {"From": config.MY_PHONE, "To": config.TWILIO_NUMBER, "Direction": "inbound", "CallSid": "CA1",
        "StirVerstat": "TN-Validation-Passed-A"}  # (the carrier vouches for your number: see test_security_hardening)
status, _ = run(post("/twilio/voice", form=form))
t.check("unsigned request -> 403", status == 403)
stranger = {**form, "From": "+12125550000"}
status, body = run(post("/twilio/voice", form=stranger, headers={"X-Twilio-Signature": sign("/twilio/voice", stranger)}))
t.check("a stranger calling Jarvis's number -> rejected", status == 200 and "<Reject/>" in body, body)
status, body = run(post("/twilio/voice", form=form, headers={"X-Twilio-Signature": sign("/twilio/voice", form)}))
t.check("you calling -> connected to Jarvis (ConversationRelay)", "<ConversationRelay" in body
        and f"ws://127.0.0.1:{PORT}/twilio/relay" in body and "welcomeGreeting=" in body, body)

print("Twilio: the call itself:")
r = run(call({"from": config.MY_PHONE, "to": config.TWILIO_NUMBER, "direction": "inbound"}, ["hi"], signed=False))
t.check("unsigned call connection -> refused", r.get("refused") == 403, r)
r = run(call({"from": "+12125550000", "to": config.TWILIO_NUMBER, "direction": "inbound"}, []))
t.check("a call that isn't you -> closed", r.get("closed") is True, r)
convo.spoken.clear()
convo.model.scripts = [{"tools": [("get_time", {})]}, {"text": "It's just after three. Anything else?"}]
r = run(call({"from": config.MY_PHONE, "to": config.TWILIO_NUMBER, "direction": "inbound"}, ["what time is it?"]))
t.check("you: 'what time is it?' -> Jarvis answers on the phone (with its tools)", r["said"] == ["It's just after three. Anything else?"], r)
t.check("...and nothing came out of the room speaker", convo.said() == [], convo.spoken)
t.check("...the model knew it was on a phone call", "you're on a phone call with them" in json.dumps(convo.requests))
convo.model.scripts = [{"text": "Sure — here's the thing."}]
r = run(call({"from": config.MY_PHONE, "to": config.TWILIO_NUMBER, "direction": "inbound"}, ["tell me"]))
t.check("phone replies get the same clean-up (no dashes)", r["said"] == ["Sure, here's the thing."], r)

# ---------------------------------------------------------------- the watcher: only important things, while driving
print("while driving: calls only for important things:")
from tests.fake_google import FakeGoogle, Mailbox  # noqa: E402
from room_agent.integrations import provider  # noqa: E402
from room_agent.phone.watcher import Watcher  # noqa: E402

FG = FakeGoogle()
G = provider("google")
G.http = FG
G.connect(levels={"gmail": ["read"], "calendar": ["read"]}, open_browser=FG.open_browser, timeout=5)
box = FG.mailboxes.setdefault("adam@gmail.test", Mailbox("adam@gmail.test"))
LA = ZoneInfo("America/Los_Angeles")
NOW = datetime.datetime.now(LA)
PLACED = []
w = Watcher(lambda key, greeting, item: PLACED.append((key, greeting, item)))
w.start()
box.add("Old Friend <old@x.test>", "Before you left", "hi", NOW - datetime.timedelta(hours=2), unread=True, labels=("INBOX", "IMPORTANT"))
w.tick()
t.check("not driving -> no calls, not even a look", PLACED == [] and FG.calls == [] or not [c for c in FG.calls if "messages" in c[1]])
state.set_driving(True)
time.sleep(0.2)
box.add("Deals <deals@shop.test>", "SALE", "50% off", NOW, unread=True)
box.add("Lisa <lisa@work.test>", "lunch?", "lunch tomorrow?", NOW, unread=True)
w.tick()
t.check("ordinary email while driving -> no call", PLACED == [], PLACED)
A = box.add("Andrew Lee <andrew@client.test>", "Budget Q4", "Need the budget today please.", NOW, unread=True)
w.tick()
t.check("email from a VIP -> one call, saying who and what about", len(PLACED) == 1 and "Andrew Lee" in PLACED[0][1]
        and "Budget Q4" in PLACED[0][1] and PLACED[0][2]["id"] == A, PLACED)
w.tick()
t.check("...never twice for the same email", len(PLACED) == 1)
box.add("Boss <boss@corp.test>", "Urgent", "Call me", NOW, unread=True, labels=("INBOX", "IMPORTANT"))
w.tick()
t.check("another important email right after -> waits (cool-down)", len(PLACED) == 1)
w.last_call -= config.CALL_COOLDOWN_MIN * 60
w.tick()
t.check("...and calls once the cool-down is over (Gmail-important counts too)", len(PLACED) == 2 and "Boss" in PLACED[1][1])
t.check("the old email from before you left doesn't count", not any("Old Friend" in p[1] for p in PLACED))
w.last_call = 0
FG.add_event("adam@gmail.test", "Standup", NOW + datetime.timedelta(minutes=10), NOW + datetime.timedelta(minutes=40))
w.tick()
t.check("a meeting starting in 10 minutes -> call", len(PLACED) == 3 and "Standup starts in" in PLACED[2][1], PLACED[-1:])
w.last_call = 0
eid = FG.add_event("adam@gmail.test", "Dentist", NOW + datetime.timedelta(hours=5), NOW + datetime.timedelta(hours=6))
w.tick()
FG.calendars["adam@gmail.test"]["events"][eid]["start"]["dateTime"] = (NOW + datetime.timedelta(hours=7)).isoformat()
FG.calendars["adam@gmail.test"]["events"][eid]["end"]["dateTime"] = (NOW + datetime.timedelta(hours=8)).isoformat()
w.last_call = 0
w.tick()
t.check("a meeting that moved -> call", len(PLACED) == 4 and "Dentist moved to" in PLACED[3][1], PLACED[-1:])
from room_agent.tools.timers import _announce  # noqa: E402

_announce("alarm.ringing", {"label": "pick up kids", "kind": "alarm", "message": "Time to pick up the kids!"})
t.check("an alarm going off at home while you drive -> call right away (no cool-down)", len(PLACED) == 5
        and "pick up the kids" in PLACED[4][1], PLACED[-1:])
state.set_driving(False)
_announce("alarm.ringing", {"label": "x", "kind": "alarm", "message": "home alarm"})
t.check("...but not when you're home", len(PLACED) == 5)

print("Jarvis calls you, and you talk it through:")
state.set_driving(True)
key, greeting, item = PLACED[0]
server.place_call(key, greeting, item, client=TW)
rid = TW.calls[-1]["url"].split("reason=")[1]
t.check("the call goes to your number from Jarvis's", TW.calls[-1]["to"] == config.MY_PHONE
        and TW.calls[-1]["from"] == config.TWILIO_NUMBER)
form = {"From": config.TWILIO_NUMBER, "To": config.MY_PHONE, "Direction": "outbound-api", "CallSid": "CA2"}
path = f"/twilio/voice?reason={rid}"
status, body = run(post(path, form=form, headers={"X-Twilio-Signature": sign(path, form)}))
t.check("you answer -> Jarvis opens with why it called", "You got an email from Andrew Lee" in body, body)
env.forget_items()
convo.model.scripts = [{"tools": [("gmail_get_message", {"message_id": "last"})]},
                       {"text": "Andrew says he needs the budget today."}]
r = run(call({"from": config.TWILIO_NUMBER, "to": config.MY_PHONE, "direction": "outbound-api",
              "customParameters": {"reason": rid}}, ["read it"]))
t.check("'read it' -> reads THAT email (it knows what the call is about)", r["said"] == ["Andrew says he needs the budget today."]
        and "Need the budget today" in json.dumps(convo.requests[-1]["messages"]), r)
state.set_driving(False)

print("hanging up:")


async def call_until_end(prompt):
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(URL.replace("http://", "ws://") + "/twilio/relay",
                                headers={"X-Twilio-Signature": sign("/twilio/relay", ws=True)}) as ws:
            await ws.send_json({"type": "setup", "callSid": "CA1", "from": config.MY_PHONE, "direction": "inbound",
                                "customParameters": {"nonce": await ticket()}})
            await ws.send_json({"type": "prompt", "voicePrompt": prompt, "last": True})
            got = []
            for _ in range(10):
                msg = await asyncio.wait_for(ws.receive(), 15)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                got.append(json.loads(msg.data))
                if got[-1]["type"] == "end":
                    break
            return got


convo.model.scripts = [{"tools": [("end_call", {})]}, {"text": "Alright, talk soon!"}]
got = run(call_until_end("okay you can hang up"))
said = "".join(m.get("token", "") for m in got if m["type"] == "text").strip()
t.check("'you can hang up' -> Jarvis says goodbye, then ends the call", said == "Alright, talk soon!"
        and got[-1]["type"] == "end", got)
t.check("the hang-up option only exists during a call", "end_call" not in {x["name"] for x in
                                                                          __import__("room_agent.tools.registry", fromlist=["x"]).active_tools()})

print("Jarvis's own tunnel:")
from room_agent.phone import tunnel  # noqa: E402


class FakeProc:
    def __init__(self, *a, **k):
        self.stderr = iter(["starting...\n", "INF |  https://brave-test-words.trycloudflare.com  |\n"])

    def poll(self):
        return None

    def terminate(self):
        pass


url = tunnel.start(PORT, timeout=5, popen=FakeProc)
t.check("starts cloudflared and reads its address", url == "https://brave-test-words.trycloudflare.com", url)

print("one brain at a time:")
rt.brain.acquire()
done = {}
th = threading.Thread(target=lambda: done.update(r=run(call({"from": config.MY_PHONE, "direction": "inbound"}, ["hey"]))))
convo.model.scripts = [{"text": "Hey!"}]
th.start()
time.sleep(1.0)
t.check("while the room is mid-turn, the phone waits", "r" not in done)
rt.brain.release()
th.join(15)
t.check("...then answers", done.get("r", {}).get("said") == ["Hey!"], done)

print("setup:")
from room_agent.phone import setup  # noqa: E402

setup.ENV = __import__("pathlib").Path(TMP) / ".env"
setup.ENV.write_text("OPENAI_API_KEY=x\n", encoding="utf-8")
config.PHONE_TOKEN = ""
out = io.StringIO()
import contextlib  # noqa: E402

with contextlib.redirect_stdout(out):
    setup.setup()
envtext = setup.ENV.read_text(encoding="utf-8")
t.check("--setup-phone makes a token and saves it (in .env, not the repo)", "PHONE_TOKEN=" in envtext and config.PHONE_TOKEN
        and config.PHONE_TOKEN in envtext)
t.check("...points Jarvis's Twilio number at Jarvis", TW.webhooks[-1] == (config.TWILIO_NUMBER, f"{URL}/twilio/voice"))
t.check("...and prints the exact iPhone Shortcut steps", f"{URL}/phone/driving" in out.getvalue()
        and "Bearer " + config.PHONE_TOKEN in out.getvalue())

print("secrets stay out of the logs:")
logs = LOG.getvalue()
t.check("no Twilio auth token, phone token or call ids in the logs", "twilio-auth-secret-xyz" not in logs
        and "phone-secret-123456" not in logs and config.PHONE_TOKEN not in logs and rid not in logs)
t.done("PHONE TESTS")
