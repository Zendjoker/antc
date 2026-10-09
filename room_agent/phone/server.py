"""The small web server your iPhone and Twilio talk to. It listens on 127.0.0.1:PHONE_PORT; a public HTTPS URL
(PUBLIC_URL, e.g. Tailscale Funnel) forwards to it.

    POST /phone/driving     {"driving": true|false}       iPhone Shortcuts (Authorization: Bearer PHONE_TOKEN)
    POST /phone/location    {"lat": .., "lon": ..}         iPhone Shortcuts, optional (same token)
    POST /twilio/voice      Twilio: a call is starting -> TwiML that connects it to Jarvis (signed by Twilio)
    GET  /twilio/relay      Twilio ConversationRelay WebSocket: your words in, Jarvis's words out (signed by Twilio)
    GET  /phone/health      {"ok": true} (so you can check the public URL reaches Jarvis)

Only MY_PHONE is answered or called. Anything unsigned / without the token is refused.
"""

import asyncio
import hmac
import json
import logging
import secrets
import threading
import time
from xml.sax.saxutils import quoteattr

from aiohttp import WSMsgType, web

from room_agent import config
from room_agent.phone import state
from room_agent.phone.twilio import Twilio, same_number, valid

log = logging.getLogger("room-agent")
PENDING = {}  # call reason id -> {"greeting", "item", "at"}: why Jarvis is calling (read when the call connects)
GREETINGS = ["Hey, what's up?", "Hey! What's going on?", "Hey, I'm here. What do you need?"]


def ready():
    """Is everything needed for calls in place? -> list of what's missing."""
    need = {"PUBLIC_URL": config.PUBLIC_URL, "TWILIO_ACCOUNT_SID": config.TWILIO_ACCOUNT_SID,
            "TWILIO_AUTH_TOKEN": config.TWILIO_AUTH_TOKEN, "TWILIO_NUMBER": config.TWILIO_NUMBER, "MY_PHONE": config.MY_PHONE,
            "PHONE_TOKEN": config.PHONE_TOKEN}
    return [k for k, v in need.items() if not v]


def twilio():
    return Twilio(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)


def place_call(key, greeting, item=None, client=None):
    """Ring your phone; when you answer, Jarvis starts with `greeting` and knows what it's about (`item`)."""
    rid = secrets.token_urlsafe(9)
    PENDING[rid] = {"greeting": greeting, "item": item, "at": time.time(), "key": key}
    for old in [k for k, v in PENDING.items() if time.time() - v["at"] > 600]:
        PENDING.pop(old, None)
    return (client or twilio()).call(config.MY_PHONE, config.TWILIO_NUMBER, f"{config.PUBLIC_URL}/twilio/voice?reason={rid}")


# ---------------------------------------------------------------- checks
def _token_ok(request):
    sent = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(config.PHONE_TOKEN) and hmac.compare_digest(sent, config.PHONE_TOKEN)


def _public(request, ws=False):
    base = config.PUBLIC_URL
    if ws:
        base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    return base + request.path_qs


async def _twilio_signed(request, ws=False):
    params = {} if ws else dict(await request.post())
    return valid(_public(request, ws), params, config.TWILIO_AUTH_TOKEN, request.headers.get("X-Twilio-Signature", "")), params


# ---------------------------------------------------------------- iPhone
async def driving(request):
    if not _token_ok(request):
        return web.json_response({"error": "not allowed"}, status=401)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    on = body.get("driving")
    if isinstance(on, str):
        on = on.strip().lower() in ("true", "yes", "1", "on", "start", "started")
    state.set_driving(bool(on), source=str(body.get("source") or "iPhone")[:40])
    return web.json_response({"ok": True, "driving": state.is_driving()})


async def location(request):
    if not _token_ok(request):
        return web.json_response({"error": "not allowed"}, status=401)
    try:
        body = await request.json()
        lat, lon = float(body["lat"]), float(body["lon"])
    except (ValueError, KeyError, TypeError):
        return web.json_response({"error": "needs lat and lon"}, status=400)
    from room_agent.tools import location as loc

    await asyncio.get_running_loop().run_in_executor(None, loc.set_phone, lat, lon)
    return web.json_response({"ok": True})


async def health(request):
    return web.json_response({"ok": True})


# ---------------------------------------------------------------- Twilio
def _twiml(body):
    return web.Response(text='<?xml version="1.0" encoding="UTF-8"?><Response>' + body + "</Response>",
                        content_type="text/xml")


async def voice(request):
    ok, p = await _twilio_signed(request)
    if not ok:
        log.warning("phone: refused an unsigned call request")
        return web.Response(status=403)
    inbound = p.get("Direction", "inbound").startswith("inbound")
    if not same_number(p.get("From") if inbound else p.get("To"), config.MY_PHONE):
        log.warning("phone: refused a call that isn't from/to your number")
        return _twiml("<Reject/>")
    reason = request.query.get("reason", "")
    greeting = (PENDING.get(reason) or {}).get("greeting") or GREETINGS[int(time.time()) % len(GREETINGS)]
    voice_attrs = ""
    errand_call = ((PENDING.get(reason) or {}).get("item") or {}).get("kind") == "errand"
    provider = config.ERRAND_TTS_PROVIDER if errand_call else config.PHONE_TTS_PROVIDER
    voice_id = config.ERRAND_VOICE if errand_call else config.PHONE_VOICE
    if provider:
        voice_attrs += f" ttsProvider={quoteattr(provider)}"
    if voice_id:
        voice_attrs += f" voice={quoteattr(voice_id)}"
    if errand_call:  # (only real speech interrupts it, not its own voice echoing back or background noise)
        voice_attrs += (' interruptSensitivity="low" welcomeGreetingInterruptible="none" ignoreBackchannel="true"')
    relay = _public(request, ws=True).split("/twilio/")[0] + "/twilio/relay"
    interrupt = "speech" if errand_call else "true"
    return _twiml(f"<Connect><ConversationRelay url={quoteattr(relay)} welcomeGreeting={quoteattr(greeting)}"
                  f' interruptible="{interrupt}"{voice_attrs}>'
                  f'<Parameter name="reason" value={quoteattr(reason)}/></ConversationRelay></Connect>')


async def relay(request):
    ok, _ = await _twilio_signed(request, ws=True)
    if not ok:
        log.warning("phone: refused an unsigned call connection")
        return web.Response(status=403)
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    loop = asyncio.get_running_loop()
    session = None

    def send(text, last):
        asyncio.run_coroutine_threadsafe(ws.send_json({"type": "text", "token": text, "last": last}), loop)

    def hang_up():  # (ConversationRelay plays what was sent, then ends; nothing follows <Connect>, so the call ends)
        asyncio.run_coroutine_threadsafe(ws.send_json({"type": "end", "handoffData": "{\"reason\": \"goodbye\"}"}), loop)

    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            kind = data.get("type")
            if kind == "setup":
                inbound = str(data.get("direction", "inbound")).startswith("inbound")
                if not same_number(data.get("from") if inbound else data.get("to"), config.MY_PHONE):
                    log.warning("phone: closed a call that isn't from/to your number")
                    await ws.close()
                    break
                pending = PENDING.pop((data.get("customParameters") or {}).get("reason", ""), None) or {}
                item = pending.get("item") or {}
                if item.get("kind") == "errand":  # (a call FOR you: restricted session, no tools - phone/errand.py)
                    from room_agent.phone import errand

                    e = errand.ACTIVE.get(item.get("errand_id"))
                    if e is None:
                        log.warning("phone: an errand call connected, but its errand isn't known here; closing")
                        await ws.close()
                        break
                    session = errand.ErrandSession(e, send, hang_up=hang_up)
                else:
                    from room_agent.phone.session import CallSession

                    session = CallSession(send, pending.get("greeting", ""), pending.get("item"), hang_up=hang_up)
                state.call_started("inbound" if inbound else "outbound")
                log.info("phone: call connected (%s)", "they called" if inbound else "Jarvis called")
            elif kind == "prompt" and session and data.get("last", True) and str(data.get("voicePrompt", "")).strip():
                loop.run_in_executor(None, session.answer, data["voicePrompt"])
            elif kind == "interrupt" and session and hasattr(session, "interrupt"):
                session.interrupt()
            elif kind == "error":
                log.warning("phone: Twilio reported a problem: %s", str(data.get("description", ""))[:120])
    finally:
        if session is not None and hasattr(session, "closed"):
            session.closed()  # (an errand call that ended before an outcome is recorded as such)
        state.call_ended()
        log.info("phone: call ended")
    return ws


def make_app():
    app = web.Application()
    app.add_routes([web.post("/phone/driving", driving), web.post("/phone/location", location),
                    web.get("/phone/health", health), web.post("/twilio/voice", voice), web.get("/twilio/relay", relay)])
    return app


# ---------------------------------------------------------------- running inside Jarvis
_watcher = None


def start():
    """Start the server (and the driving watcher) in the background. -> list of what's still missing (empty: all set)."""
    global _watcher
    if config.PHONE_TUNNEL == "cloudflared":  # (Jarvis makes its own public address, and tells Twilio about it)
        from room_agent.phone import tunnel

        url = tunnel.start(config.PHONE_PORT)
        if url:
            config.PUBLIC_URL = url
            log.info("phone: public address %s", url)
            if config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_NUMBER:
                try:
                    twilio().set_voice_webhook(config.TWILIO_NUMBER, f"{url}/twilio/voice")
                    log.info("phone: calls to %s go to this address", config.TWILIO_NUMBER)
                except Exception as e:
                    log.warning("phone: couldn't update Twilio (%s)", e)
    missing = ready()
    if "PHONE_TOKEN" in missing or "PUBLIC_URL" in missing:
        log.warning("phone mode needs %s (run: python main.py --setup-phone)", ", ".join(missing))
        return missing

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        runner = web.AppRunner(make_app(), access_log=None)  # (no access log: URLs can carry call ids)
        loop.run_until_complete(runner.setup())
        loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", config.PHONE_PORT).start())
        loop.run_forever()

    threading.Thread(target=run, daemon=True, name="phone-server").start()
    if not ready():
        from room_agent.phone.watcher import Watcher

        _watcher = Watcher(place_call)
        _watcher.start()
    log.info("phone mode: listening on 127.0.0.1:%d (public: %s)%s", config.PHONE_PORT, config.PUBLIC_URL,
             "" if not missing else f"; calls need {', '.join(missing)}")
    return missing
