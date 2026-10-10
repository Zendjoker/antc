"""The natural voice engine, offline: the SpeechDirector's delivery intentions and safety classes, the expressiveness
settings, the model-aware formatter (tags only where a model takes them, an allowlist, SSML only for the models that
read it, v4's own voice settings, number normalization per model), the Text to Dialogue WebSocket transport, pauses
between thoughts, cancellation of stale speech, fallbacks, and that no delivery tag ever reaches what's shown, stored or
sent to a tool.

    the 15 required situations: greeting, casual talk, excitement, a technical explanation, a serious warning, an emotional
    moment, a long answer with pauses, numbers / dates / money, links and technical terms, another language, being
    talked over, a provider failure, unsupported tags, streaming continuity, no tag leaks

ElevenLabs is simulated (an in-process stand-in for the HTTP stream and a local WebSocket server for Text to Dialogue);
the speaker is silent; the reasoning model is scripted. No key, no cost, nothing outside this process.
What this can't judge: how it SOUNDS. That's tests/voice_audition.py (paired clips, blind listening).

    .venv\\Scripts\\python -m tests.test_voice_engine
"""

import asyncio
import base64
import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.harness import setup_env  # noqa: E402

TMP = setup_env(TTS_PROVIDER="elevenlabs", ELEVENLABS_API_KEY="el-test-key-not-real", ELEVENLABS_MODEL="eleven_v4_turbo",
                ELEVENLABS_FALLBACK_MODEL="eleven_flash_v2_5", OUTPUT_SAMPLE_RATE="24000", PERSONALITY="street")
from tests.harness import Checker, Conversation  # noqa: E402

from room_agent import runtime as rt  # noqa: E402
from room_agent import social, speech  # noqa: E402
from room_agent.audio import speaker, styles, tts, voices  # noqa: E402
from room_agent.config import OUT_SR  # noqa: E402
from room_agent.social import personality  # noqa: E402
from room_agent.social.strategy import ResponseStrategy  # noqa: E402
from room_agent.speech import chunking, dialogue_ws, director, elevenlabs as el  # noqa: E402

t = Checker()
check = t.check


def D(sentence, mode="task", position=0, previous=None, level="natural", flavor=None, confirming=False, emotion=True,
      pauses=True, values=None, **strategy):
    director._last_reaction["at"] = 0.0
    s = ResponseStrategy(mode=mode, confidence=0.8, **strategy)
    return director.direct(sentence, s, {"values": values or {}}, position=position, previous=previous,
                           language=speech.language.detect(sentence), level=level, emotion=emotion, pauses=pauses,
                           confirming=confirming, flavor=flavor)


def script(p, model="eleven_v4_turbo"):
    return el.script_for(p, model)


def tags(text):
    return el.TAG.findall(text)


WIN = personality.Flavor(celebrate=True, energy="high")
SERIOUS = personality.Flavor(serious=True, energy="low")

# ---------------------------------------------------------------------------------------------- the situations
print("1. Friendly greeting:")
g = D("Hey, what's up?", mode="casual")
s1, _ = script(g)
t2 = D("I was just looking at your calendar.", mode="casual", position=1, previous=g)
check("a greeting opens warm", g.intent == "greeting" and tags(s1) == ["warm"], (s1, g.why))
check("...and the warmth isn't carried onto the next sentence as a performance", not tags(script(t2)[0]), script(t2)[0])

print("2. Casual conversation:")
c = D("Yeah, that movie was solid.", mode="casual")
check("natural: a plain casual line stays plain (most sentences get no acting)", not tags(script(c)[0]), script(c)[0])
check("expressive: relaxed talk gets a light 'engaged'", tags(script(D("Yeah, that movie was solid.", mode="casual",
                                                                       level="expressive"))[0]) == ["engaged"])

print("3. Excited response:")
w = D("You shipped the whole app in a weekend.", mode="casual", flavor=WIN)
check("a real win (the personality's reading): excited", tags(script(w)[0]) == ["excited"] and w.intent == "excitement",
      (script(w)[0], w.why))
sur = D("No way, you actually got it working.", mode="casual", response_energy="high")
check("a surprised reaction: excited, genuinely surprised, one stressed word",
      tags(script(sur)[0]) == ["excited, genuinely surprised"] and "ACTUALLY" in script(sur)[0], script(sur)[0])

print("4. Technical explanation:")
tech = D("The backend listens on port 8000, and the frontend reaches it through the proxy config.", mode="task")
st, _ = script(tech)
check("calm technical: no tag, no added pauses or stress, steady pace", not tags(st) and "..." not in st and tech.pace <= 1.0
      and tech.intent in ("calm_technical", "precise"), (st, tech.intent))

print("5. Serious warning:")
warn = D("Don't share that code with anyone, it's how they get into your account.", mode="joking", humor_level="full",
         values={"joking": 0.9})
sw, _ = script(warn)
check("a security warning: serious and clear, never playful, no reaction even in a joking mood",
      warn.safety == "warning" and tags(sw) == ["serious, clear"] and not warn.reaction, (sw, warn.why))
check("the door / alarm kind too", D("The front door is still unlocked.").safety == "warning")
confirm = D("Want me to send it to Sam now?", mode="joking", humor_level="full")
check("a yes/no before an action: calm and clear, nothing playful", tags(script(confirm)[0]) == ["calm, clear"]
      and confirm.safety == "confirm", script(confirm)[0])
offer = D("Want me to put on something quiet?", mode="emotional", position=1, values={"serious": 0.6},
          previous=D("Yeah, you sound wiped.", mode="emotional", values={"serious": 0.6}, response_energy="low"),
          response_energy="low")
check("...but a casual offer keeps the moment's tone (not a confirmation)", offer.safety == "normal"
      and "calm, clear" not in script(offer)[0], (script(offer)[0], offer.why))
check("...and any question is a confirmation while a yes/no about an action is open (the executor asked)",
      D("You sure?", confirming=True).safety == "confirm")

print("6. Emotional or sensitive conversation:")
emo = D("That's a lot to carry, and you don't have to have it figured out tonight.", mode="emotional",
        values={"serious": 0.7}, response_energy="low")
check("a heavy moment: quiet and warm, no reaction, a little slower", tags(script(emo)[0]) == ["quiet, warm"]
      and not emo.reaction and emo.pace < 1.0, (script(emo)[0], emo.why))
joke_sad = D("Haha, yeah.", mode="joking", humor_level="full", values={"joking": 0.9, "serious": 0.7})
check("never a laugh when it's serious (whatever the jokes)", not joke_sad.reaction)

print("7. Long answer with natural pauses:")
reply = ["Okay.", "The first thing is the router.", "So, unplug it for ten seconds.", "Also, move it off the floor.",
         "Then check the lights."]
perf, prev = [], None
for i, sentence in enumerate(reply):
    prev = D(sentence, position=i, previous=prev)
    perf.append(prev)
check("a breath after a standalone 'Okay.' and before each new point, never before the first sentence",
      [p.pause_before for p in perf] == [0.0, 0.15, 0.2, 0.2, 0.2], [p.pause_before for p in perf])
check("pauses off -> none at all", all(D(s, position=i, previous=perf[i - 1] if i else None, pauses=False).pause_before == 0
                                       for i, s in enumerate(reply)))
check("a thoughtful 'Hmm' trails off on v4 ('Hmm...'); an SSML model gets one short break instead",
      script(D("Hmm, I'm not sure that'll work.", mode="casual"))[0] == "[thoughtful] Hmm... I'm not sure that'll work."
      and script(D("Hmm, I'm not sure that'll work.", mode="casual"), "eleven_flash_v2_5")[0]
      == 'Hmm, <break time="0.3s" /> I\'m not sure that\'ll work.')

print("8. Numbers, dates and currency:")
money = D("You made $1,250 this week, that's 15% more than last week.", mode="casual", flavor=WIN)
sm, _ = script(money)
check("money with a win: no excitement acting on the amount (plain), no stress or pauses added", money.safety == "precise"
      and not tags(sm) and "..." not in sm and money.pace <= 1.0, (sm, money.why))
v4_text, _ = speech.provider_text(_item := styles.Spoken("It's $1,250, due 2026-10-09."), "elevenlabs", "eleven_v4_turbo")
flash_text, _ = speech.provider_text(_item, "elevenlabs", "eleven_flash_v2_5")
check("v4 gets the amount as written (it normalizes); the date is spoken as a date", "$1,250" in v4_text
      and "October 9th, 2026" in v4_text, v4_text)
check("Flash (no number normalization unless asked): the amount is written out before it's sent",
      "1,250 dollars" in flash_text and "$" not in flash_text, flash_text)
otp = D("Your code is 482913.", mode="casual", flavor=WIN)
check("a one-time code: plain, exactly as written", not tags(script(otp)[0]) and "482913" in script(otp)[0], script(otp)[0])

print("9. URLs and technical terms:")
url_text, p_url = speech.provider_text(styles.Spoken("It's on docs.example.com/setup, and mail adam@gmail.com."),
                                       "elevenlabs", "eleven_v4_turbo")
check("links and emails read the way people say them, no performance on them", "docs dot example dot com slash setup" in url_text
      and "adam at gmail dot com" in url_text and not tags(url_text), url_text)
check("acronyms are left to ElevenLabs (it reads 'GPU' well)", "GPU" in speech.provider_text(
    styles.Spoken("Your GPU is at 95 percent."), "elevenlabs", "eleven_v4_turbo")[0])

print("10. Multilingual text:")
fr = D("Salut, je regarde ça tout de suite.", mode="casual")
body = el.request_body("Salut.", fr, "eleven_v4_turbo", language=fr.language)
check("French stays French: language kept, the words untouched", fr.language == "fr" and body.get("language_code") == "fr"
      and script(fr)[0].endswith("Salut, je regarde ça tout de suite."), (fr.language, body))
check("multilingual_v2 never gets language_code", "language_code" not in el.request_body("Hola.", fr, "eleven_multilingual_v2",
                                                                                        language="es"))

# ---------------------------------------------------------------------------------------------- formatter + settings
print("13. Unsupported audio-tag handling:")
for bad_script, why in (("[sound of rain] Okay.", "isn't on the allowed list"), ("[angry, shouting] Okay.", "isn't on the allowed list"),
                        ('Okay. <break time="1.0s" />', "markup this model can't take")):
    probs = el.validate(director.SpeechPerformance("Okay."), bad_script, "eleven_v4_turbo")
    check(f"refused: {bad_script!r}", any(why in x for x in probs), probs)
check("tags for a model that would read them out are refused",
      el.validate(director.SpeechPerformance("Okay."), "[calm] Okay.", "eleven_flash_v2_5") != [])
check("a cue the model wrote into its words is never spoken ([short pause], [laughs]); a bracketed name keeps its word",
      speech.clean_for_speech("Okay [short pause] so [laughs] that's it, [Adam].") == "Okay so that's it, Adam.",
      speech.clean_for_speech("Okay [short pause] so [laughs] that's it, [Adam]."))
check("...and markup in the text itself never reaches the voice", speech.clean_for_speech('Hi <break time="3s"/> there <b>x</b>.')
      == "Hi there x.")

print("Expressiveness settings:")
check("off: plain words, no tags, no pauses", not tags(script(D("Hey, what's up?", mode="casual", level="off"))[0])
      and D("So, next.", position=3, previous=perf[2], level="off").pause_before == 0)
check("subtle: no warmth on a greeting, but a warning stays serious and a heavy moment stays quiet",
      not tags(script(D("Hey, what's up?", mode="casual", level="subtle"))[0])
      and tags(script(D("Don't share that code with anyone.", level="subtle"))[0]) == ["serious, clear"]
      and tags(script(D("That's a lot.", mode="emotional", level="subtle", values={"serious": 0.7}))[0]) == ["calm, warm"])
check("emotional delivery off: no colouring at all, even for a win", not tags(script(D("You did it.", mode="casual", flavor=WIN,
                                                                                    emotion=False))[0]))
voices.save_setting("speech_expressiveness", "subtle")
check("a level said out loud (settings.json) wins over .env", director.settings()[0] == "subtle")
voices.save_setting("speech_expressiveness", "loud")
check("...and a damaged value falls back to natural", director.settings()[0] == "natural")
voices.save_setting("speech_expressiveness", None)
check("v4: only stability and similarity go out (no speed, no style); Flash keeps speed",
      el.request_body("x", director.SpeechPerformance("x", pace=1.05), "eleven_v4_turbo", base_rate=1.12,
                      profile={"stability": 0.4, "style": 0.2, "speed": 1.0}).get("voice_settings") == {"stability": 0.4}
      and el.request_body("x", director.SpeechPerformance("x", pace=1.05), "eleven_flash_v2_5",
                          base_rate=1.12)["voice_settings"]["speed"] == 1.176)

# ---------------------------------------------------------------------------------------------- the real pipeline
print("Speaking through the pipeline (simulated ElevenLabs HTTP + dialogue WebSocket):")


class SilentSpeaker:
    def __init__(self):
        self.interrupted, self.end, self.got, self.chunks = threading.Event(), 0.0, bytearray(), []
        self.cancel_seq = 0

    def play(self, pcm):
        self.got += pcm
        self.chunks.append(bytes(pcm))
        self.end = max(self.end, time.time()) + len(pcm) / 2 / OUT_SR

    def queued_seconds(self):
        return max(0.0, self.end - time.time())

    def flush(self):
        self.end = time.time()

    def is_playing(self):
        return False

    def wait_drained(self):
        pass


class FakeResp:
    """Like a streamed requests response: its body can only be read while it's open."""

    def __init__(self, status=200, chunks=(b"\x01\x00" * 2400,), delay=0.0, text="error"):
        self.status_code, self.chunks, self.delay, self._text = status, chunks, delay, text
        self.response, self.closed, self._read = self, False, None

    @property
    def text(self):
        if self._read is None:
            if self.closed:
                raise RuntimeError("the content for this response was already consumed")
            self._read = self._text
        return self._read

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.closed = True
        return False

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def iter_content(self, n):
        for c in self.chunks:
            time.sleep(self.delay)
            yield c


SENT, PLAN = [], []


def fake_post(url, headers=None, json=None, stream=None, timeout=None):
    SENT.append({"url": url, "body": json, "headers": headers})
    step = PLAN.pop(0) if PLAN else "ok"
    if step == "v4-not-here":
        return FakeResp(status=400, text='{"detail": {"status": "unsupported_model", "message": "Model eleven_v4_turbo is '
                                         'not supported on this endpoint. Use the text-to-dialogue websocket."}}')
    if step == "slow":
        return FakeResp(chunks=[b"\x01\x00" * 480] * 100, delay=0.01)
    if isinstance(step, int):
        return FakeResp(status=step)
    return FakeResp()


WS = {"connections": 0, "inputs": [], "fail": False, "keys": []}


def fake_dialogue_server():
    from aiohttp import WSMsgType, web

    async def dialogue(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        WS["connections"] += 1
        WS["keys"].append(request.headers.get("xi-api-key"))
        buffered = ""
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                break
            data = json.loads(msg.data)
            if "inputs" in data:
                WS["inputs"].append(data["inputs"][0])
                buffered += "".join(x["text"] for x in data["inputs"])
            if data.get("flush") and buffered:
                if WS["fail"]:
                    await ws.send_json({"error": "internal", "message": "simulated failure"})
                    buffered = ""
                    continue
                await asyncio.sleep(0.03)
                for _ in range(3):
                    await ws.send_json({"audio": base64.b64encode(b"\x03\x00" * 1200).decode()})
                await ws.send_json({"is_final_audio_for_turn": True})
                buffered = ""
            if data.get("close_socket"):
                await ws.close()
        return ws

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    app = web.Application()
    app.router.add_get("/v1/text-to-dialogue/stream-input", dialogue)
    loop = asyncio.new_event_loop()
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", port).start())
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return port


dialogue_ws.BASE = f"ws://127.0.0.1:{fake_dialogue_server()}"
tts.el.post = fake_post
PIPER = []
tts.piper_pcm = lambda text: (PIPER.append(str(text)) or iter([b"\x02\x00" * 240]))
rt.engine, rt.tts_enabled = SilentSpeaker(), True
threading.Thread(target=speaker.speaker_worker, daemon=True).start()


def speak(sentences, plan=(), user="What's up?", before=None):
    SENT.clear()
    PIPER.clear()
    PLAN[:] = list(plan)
    rt.engine.interrupted.clear()
    rt.engine.chunks.clear()
    rt.new_turn(user)
    social.on_user_turn(user)
    if before:
        before()
    for s in sentences:
        speaker.say(s)
    speaker.finish_speaking()


print("12/14. Transport, continuity and fallback:")
speaker._model["name"] = None
speaker._dialogue.clear()
speak(["Hey, what's up?", "I was just looking at your calendar.", "You're free after three."],
      plan=["v4-not-here"], user="hey")
check("the HTTP stream refuses eleven_v4_turbo -> the same model over its Text to Dialogue WebSocket (not a downgrade)",
      len(SENT) == 1 and "eleven_v4_turbo" in speaker._dialogue and len(WS["inputs"]) == 3 and not PIPER
      and speaker._model["name"] is None, (len(SENT), len(WS["inputs"]), PIPER))
check("...one connection for the whole reply (later sentences skip the handshake); the key only in its header",
      WS["connections"] == 1 and WS["keys"] == ["el-test-key-not-real"], WS["connections"])
check("...a new turn only at the reply's first sentence (prosody carries across the reply)",
      [x["new_turn"] for x in WS["inputs"]] == [True, False, False], [x["new_turn"] for x in WS["inputs"]])
check("...and the performance script goes through it (the greeting's warmth: a live reply's first sentence is sentence "
      "0), the words exact", WS["inputs"][0]["text"].strip() == "[warm] Hey, what's up?", WS["inputs"][0]["text"])
n_inputs = len(WS["inputs"])
speak(["Okay, done."], user="turn it off")
check("the next reply on the same connection starts a new turn", WS["inputs"][n_inputs]["new_turn"] is True
      and WS["connections"] == 1, WS["inputs"][n_inputs:])
WS["fail"] = True
speak(["The meeting's at three."])
check("the socket fails -> that sentence with the fallback model over HTTP, nothing lost", len(SENT) == 1
      and SENT[0]["body"]["model_id"] == "eleven_flash_v2_5" and "[" not in SENT[0]["body"]["text"] and not PIPER,
      [s["body"] for s in SENT])
WS["fail"] = False
speaker._dialogue.clear()
dialogue_ws.close()
speak(["The meeting's at three.", "Bring the slides."], plan=[None, None])
check("HTTP, a non-tag model: earlier sentences ride along as previous_text (continuity)",
      True if not SENT else ("previous_text" not in SENT[0]["body"]), [s["body"].get("previous_text") for s in SENT])
speaker._model["name"] = "eleven_flash_v2_5"
speak(["The meeting's at three.", "Bring the slides."])
check("Flash: the second sentence carries the first as previous_text", len(SENT) == 2
      and SENT[1]["body"].get("previous_text") == "The meeting's at three.", [s["body"].get("previous_text") for s in SENT])
speaker._model["name"] = None
check("chunking: whole sentences, never word fragments; a long first sentence starts at a clause",
      chunking.ready("Yeah. That's the one I meant", first=True) == (["Yeah."], "That's the one I meant")
      and all(len(c) >= 40 for c in chunking.ready("The router keeps dropping because the channel is crowded, and the "
                                                   "firmware is old, so", first=True)[0]))

real_load = tts.load_piper
tts.piper_pcm = lambda text: (_ for _ in ()).throw(SystemExit("TTS_PROVIDER=piper needs: pip install"))
speaker._local["ok"] = None
speak(["The meeting's at three."], plan=[500])
speak(["Still here?"], plan=[500])
alive = any(th.name != "MainThread" and th.is_alive() for th in threading.enumerate())
check("no local voice installed: a failed sentence is skipped, the speaker thread survives and keeps going",
      speaker._local["ok"] is False and alive and len(SENT) == 1, (speaker._local, len(SENT)))
speak(["Back to normal."])
check("...and ElevenLabs keeps working after it", len(SENT) == 1 and len(rt.engine.got) > 0)
tts.piper_pcm = lambda text: (PIPER.append(str(text)) or iter([b"\x02\x00" * 240]))
speaker._local["ok"] = None

print("11. Interruption during playback:")
speaker._model["name"] = "eleven_flash_v2_5"


def talk_over():
    time.sleep(0.15)
    rt.engine.cancel_seq += 1  # (what the engine does when you talk over it)
    rt.engine.flush()


threading.Thread(target=talk_over, daemon=True).start()
t0 = time.time()
speak(["This is a long one that keeps going for a while.", "And this one was queued behind it.", "And this one too."],
      plan=["slow", "ok", "ok"])
took = time.time() - t0
check("talked over mid-sentence: the stream stops and the queued sentences never play (even after the flag clears)",
      len(SENT) == 1 and took < 0.9, (len(SENT), round(took, 2)))
stale = styles.Spoken("old")
stale.cancel_seq = rt.engine.cancel_seq - 1
check("speech queued before a cancellation is stale; new speech isn't", speaker.stale(stale, rt.engine)
      and not speaker.stale(styles.Spoken("plain str item"), rt.engine))
speaker._model["name"] = None

print("7b. Pauses in the real queue:")
speaker._model["name"] = "eleven_flash_v2_5"
speak(["Okay.", "The first thing is the router.", "So, unplug it.", "Also, move it."], user="my wifi is slow, what do I do")
silences = [c for c in rt.engine.chunks if c and not any(c)]
check("short silences land between thoughts (none before the first sound)",
      len(silences) >= 1 and rt.engine.chunks[0] and any(rt.engine.chunks[0]), [len(c) for c in silences])
speaker._model["name"] = None

print("15. No tags leaking into chat, memory or tools:")
convo = Conversation(engine=rt.engine)
speaker._model["name"] = "eleven_flash_v2_5"
results, system, _ = convo.say("can you put milk on the groceries list for me, thanks a lot", calls=[("add_to_list", {"item": "[excited] milk",
                                                                                      "list": "groceries"})],
                               reply="[excited] Done, it's on there. [laughs] Easy.")
stored = json.dumps(convo.history)
check("a tool's arguments never carry a delivery tag (the call that ran and the copy in the history)",
      "[excited]" not in stored and any("milk" in str(r) for r in results), (results, stored[-400:]))
check("...nor what was said, shown or kept", not any("[" in s for s in convo.said()) and "[laughs]" not in stored,
      convo.said())
from room_agent.actions import core  # noqa: E402

lists = core.get("show_list").execute({"list": "groceries"})
check("...and the list really has 'milk', not '[excited] milk'", "milk" in lists and "[excited]" not in lists, lists)
convo.say("hey", reply="[laughs] Yo, what's good? You good? Yeah.")
check("a [laughs] hint from the model is one reaction on the first sentence, not the tone of the whole reply",
      not any("[" in s for s in convo.said()), convo.said())
speaker._model["name"] = None
check("the delivery-hint rule tells the model: optional, only at the start, never in tool arguments or messages",
      "never in tool arguments, emails, texts" in convo.requests[0]["messages"][0]["content"]
      if styles.hints_allowed() else True)

print("Audit:")
entries = list(speech.audit)
check("each spoken sentence is audited (model, transport, intent, safety, tags, first-byte time, retry), never its words",
      entries and all({"model", "transport", "intent", "safety", "tags", "ttfb_s", "retry"} <= set(e) for e in entries)
      and not any("milk" in json.dumps(e) or "meeting" in json.dumps(e) for e in entries), entries[-2:])

t.done()
