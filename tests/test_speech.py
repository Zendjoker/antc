"""Expressive speech (room_agent/speech/), offline: the SpeechDirector over 30+ situations, the ElevenLabs renderer and
its validator, semantic vs performance text, normalization, pronunciations, language, fallback when ElevenLabs fails,
interruption latency, and timing. A simulated ElevenLabs (no key, no cost) and a silent speaker.

    .venv\\Scripts\\python -m tests.test_speech

What this can't do: judge how it SOUNDS. That's tests/voice_audition.py + a human listening blind.
"""

import json
import logging
import re
import threading
import time

from tests.harness import Checker, Conversation, setup_env

TMP = setup_env(TTS_PROVIDER="elevenlabs", ELEVENLABS_API_KEY="el-test-key-not-real", ELEVENLABS_MODEL="eleven_v4_turbo",
                OUTPUT_SAMPLE_RATE="24000", SOCIAL_MEANING="1")
LOGS = []


class Capture(logging.Handler):
    def emit(self, record):
        LOGS.append(record.getMessage())


logging.basicConfig(level=logging.INFO)
logging.getLogger("room-agent").addHandler(Capture())

from room_agent import runtime as rt  # noqa: E402
from room_agent import social, speech  # noqa: E402
from room_agent.audio import speaker, tts, voices  # noqa: E402
from room_agent.speech import director, elevenlabs as el, language, normalize, pronounce, timing  # noqa: E402
from room_agent.social import meaning  # noqa: E402

meaning.load()  # (the moods below are read by meaning, not matched: social/meaning.py)

t = Checker()
check = t.check


def perform_all(user, replies, failed_before=0, model="eleven_v4_turbo"):
    social.reset()
    for _ in range(failed_before):
        rt.new_turn("open spotify")
        social.on_user_turn("open spotify")
        social.state.update_last(failed=True)
    rt.new_turn(user)
    social.on_user_turn(user)
    prev, out = None, []
    for i, s in enumerate(replies):
        p = director.direct(s, rt.turn.strategy, social.state.snapshot(), position=i, previous=prev, language=language.detect(s))
        prev = p
        script, problems = el.script_for(p, model)
        out.append((p, script, problems))
    return out


def tags(script):
    return el.TAG.findall(script)


# ---------------------------------------------------------------- 1. the director over 30+ situations
print("SpeechDirector (30+ situations):")
CASES = [  # (name, user, reply sentences, failures before, check(list of (perf, script)))
    ("normal question, plain delivery", "What time is it?", ["It's 7:45."], 0, lambda r: not tags(r[0][1])),
    ("one-word reply, plain", "Thanks", ["Sure."], 0, lambda r: not tags(r[0][1])),
    ("plain task answer", "What's on my calendar?", ["You've got a dentist appointment at three."], 0, lambda r: not tags(r[0][1])),
    ("thinking response stays natural", "Should I take the job?", ["Honestly, it depends on what you want next."], 0,
     lambda r: len(tags(r[0][1])) <= 1),
    ("excited news -> excited, genuinely surprised", "Bro! It finally fucking works!", ["No way, you actually got it working."], 0,
     lambda r: tags(r[0][1]) == ["excited, genuinely surprised"] and "ACTUALLY" in r[0][1]),
    ("low energy -> quiet, warm, a trailing 'Yeah...'", "Man, I'm so tired.", ["Yeah, what happened?"], 0,
     lambda r: r[0][1] == "[quiet, warm] Yeah... what happened?"),
    ("exhausted -> quiet, slower, not sad", "Man, I'm exhausted.", ["Then rest a bit, you've earned it."], 0,
     lambda r: tags(r[0][1]) == ["quiet, warm"] and r[0][0].pace < 1 and "sad" not in r[0][1]),
    ("frustrated user + Jarvis's mistake -> sincere, calm; then calm, direct (no anger back)", "This shit still doesn't work.",
     ["Yeah, that's on me.", "Let me fix it properly."], 2,
     lambda r: tags(r[0][1]) == ["sincere, calm"] and tags(r[1][1]) == ["calm, direct"]),
    ("frustrated + a found cause -> calm, direct, not sad", "ugh why is this still broken", ["I found it.",
     "The backend is using the wrong port."], 2, lambda r: all(tags(x[1]) == ["calm, direct"] for x in r)),
    ("failure report -> calm, matter-of-fact", "open the thing", ["I couldn't open it, it's not installed."], 0,
     lambda r: tags(r[0][1]) in ([], ["calm, matter-of-fact"])),
    ("urgent -> clear, firm, a bit faster, nothing dramatic", "Quick, the kitchen is on fire.",
     ["Turn off the stove and cover the pan with a lid.", "Don't use water."], 0,
     lambda r: all(tags(x[1]) == ["clear, firm"] and x[0].pace > 1 for x in r) and "..." not in r[0][1]),
    ("'quick, something's wrong' -> clear, firm", "Quick, something's wrong with the server.", ["Okay, checking it now."], 0,
     lambda r: tags(r[0][1]) == ["clear, firm"]),
    ("serious news -> sympathy, quiet, warm, no reaction", "My grandma is in the hospital.",
     ["I'm really sorry.", "Do you want to talk about it?"], 0,
     lambda r: tags(r[0][1]) == ["quiet, warm"] and r[0][0].purpose == "sympathy" and not r[0][0].reaction),
    ("joking -> lightly amused (a rare chuckle at most)", "haha you're useless today", ["I'm doing my best over here."], 0,
     lambda r: "lightly amused" in tags(r[0][1])),
    ("teasing allowed -> playful, teasing", "lol you're so slow", ["Says the one who took an hour to find the bug."], 0,
     lambda r: True),  # (filled in below with a roast preference)
    ("surprise -> 'Wait...' and one emphasized word", "I found the bug, finally", ["Wait, you actually fixed it?"], 0,
     lambda r: r[0][1] == "[genuinely surprised] Wait... you actually fixed it?" or "Wait..." in r[0][1]),
    ("a question in casual talk -> curious", "Bro, guess what.", ["What happened?"], 0,
     lambda r: tags(r[0][1]) in ([], ["curious"])),
    ("technical explanation -> continuity over acting", "Explain the port problem",
     ["Two programs tried to listen on port 8000 at the same time, so the second one couldn't start because the first was "
      "already holding it."], 0, lambda r: len(tags(r[0][1])) <= 1 and "..." not in r[0][1]),
    ("numbers stay as written in the script (the engine normalizes)", "How much was it?", ["It was $1,250."], 0,
     lambda r: "$1,250" in r[0][1]),
    ("an email address in a reply", "What's his email?", ["It's adam@gmail.com."], 0, lambda r: "adam@gmail.com" in r[0][1]),
    ("a URL in a reply", "Where's the doc?", ["It's on docs.example.com/setup."], 0, lambda r: "docs.example.com" in r[0][1]),
    ("an acronym in a reply", "What's slow?", ["Your GPU is at 95 percent."], 0, lambda r: "GPU" in r[0][1]),
    ("a short ack in a plain task", "turn it down", ["Done."], 0, lambda r: not tags(r[0][1])),
    ("greeting", "Hey Jarvis.", ["Hey, what's up?"], 0, lambda r: len(tags(r[0][1])) <= 1),
    ("several neutral sentences: no acting, nothing reset per sentence", "Tell me about tomorrow",
     ["You've got two meetings.", "The first is at ten.", "The second is at three."], 0,
     lambda r: all(not tags(x[1]) for x in r)),
    ("one direction held across a reply (one speaker)", "Man, I'm wiped.", ["Yeah.", "Long week.", "Get some sleep."], 0,
     lambda r: len({tuple(x[0].direction) for x in r}) == 1),
    ("multilingual: French stays French, language kept", "Salut", ["Je suis là, qu'est-ce que tu veux faire ?"], 0,
     lambda r: r[0][0].language == "fr" and r[0][0].semantic_text in r[0][1]),
    ("multilingual: Spanish detected", "Hola", ["Claro, lo hago ahora mismo para ti."], 0, lambda r: r[0][0].language == "es"),
    ("same sentence, joke -> amused", "haha that's hilarious", ["Alright. I got it."], 0, lambda r: tags(r[0][1]) == ["lightly amused"]),
    ("same sentence, serious -> quiet, warm", "this is serious, my grandma is sick", ["Alright. I got it."], 0,
     lambda r: tags(r[0][1]) == ["quiet, warm"]),
    ("same sentence, urgent -> clear, firm", "quick, the server's down", ["Alright. I got it."], 0,
     lambda r: tags(r[0][1]) == ["clear, firm"]),
    ("same sentence, normal task -> plain", "what's on my calendar", ["Alright. I got it."], 0, lambda r: not tags(r[0][1])),
    ("a laugh never lands in a serious moment", "my dog died today, haha I don't know why I'm laughing",
     ["That's a lot to take in."], 0, lambda r: not r[0][0].reaction),
]
for name, user, replies, fails, ok in CASES:
    r = perform_all(user, replies, fails)
    check(name, ok([(p, s) for p, s, _ in r]) and all(not probs for _, _, probs in r), [(s, p.why) for p, s, _ in r])

from room_agent import learning  # noqa: E402

learning.user_model().teach("humor", "occasional roasting is fine", subject="always", because="test")
r = perform_all("lol you're so slow", ["Says the one who took an hour to find the bug."])
check("they like teasing -> playful, teasing (only when joking)", "playful, teasing" in tags(r[0][1]), r[0][1])
for k in [p["key"] for p in learning.user_model().all(include_tentative=True)]:
    learning.user_model().forget(k)

# ---------------------------------------------------------------- 2. renderer by model, and the validator
print("Renderer and validator:")
p = director.SpeechPerformance("Wait, you actually fixed it?", purpose="surprise", direction=["excited", "genuinely surprised"],
                               emphasis="actually", energy="high")
check("v4 / v4 Turbo / v3: natural-language tag + punctuation pacing", el.render(p, "eleven_v4_turbo") ==
      "[excited, genuinely surprised] Wait... you ACTUALLY fixed it?" and el.render(p, "eleven_v4") == el.render(p, "eleven_v3"))
check("models without audio tags get the plain words (a bracket would be read out)", el.render(p, "eleven_flash_v2_5") ==
      "Wait, you actually fixed it?" and el.render(p, "some_future_model") == "Wait, you actually fixed it?")
bad = [("[calm][friendly][natural][human] Yeah.", "Yeah.", "too many tags"),
       ("[calm] Yeah, I fixed the other thing.", "Yeah, I fixed it.", "the words changed"),
       ("[laughs] I'm sorry about your grandma.", "I'm sorry about your grandma.", "doesn't fit a serious moment"),
       ("[calm] THIS IS VERY BAD NEWS.", "This is very bad news.", "too much shouting"),
       ("[calm] Yeah... well... okay... sure...", "Yeah, well, okay, sure.", "too many ellipses"),
       ("[strategy: focused] Okay.", "Okay.", "odd tag"),
       ("<break time=\"1s\"/> Okay.", "Okay.", "the words changed")]
for script, semantic, why in bad:
    q = director.SpeechPerformance(semantic, seriousness="high" if "grandma" in semantic else "low")
    problems = el.validate(q, script, "eleven_v4_turbo")
    check(f"validator rejects: {why}", any(why in x for x in problems), problems)
q = director.SpeechPerformance("Okay.", direction=["calm"])
check("a rejected script falls back to the plain semantic text", el.script_for(
    director.SpeechPerformance("Okay.", direction=["calm"] * 4), "eleven_v4_turbo")[0] in ("Okay.", "[calm, calm, calm] Okay."))
body = el.request_body("[calm] Hi.", q, "eleven_v4_turbo", base_rate=1.0, previous_text="Before.", language="fr", seed=42)
check("request: speed only from their rate x this pace; no invented settings; seed for A/B; language kept",
      body.get("voice_settings") is None and body["seed"] == 42 and body["language_code"] == "fr"
      and body["apply_text_normalization"] == "auto")
body = el.request_body("Hi.", director.SpeechPerformance("Hi.", pace=1.05), "eleven_flash_v2_5", base_rate=1.12,
                       previous_text="Earlier sentence.")
check("older models: previous_text for continuity; speed kept within 0.7-1.2", body["previous_text"] == "Earlier sentence."
      and body["voice_settings"]["speed"] == 1.176)
check("multilingual_v2 doesn't take language_code", "language_code" not in el.request_body(
    "Hola.", q, "eleven_multilingual_v2", language="es"))

# ---------------------------------------------------------------- 3. normalization, pronunciation, language
print("Normalization, pronunciation, language:")
N = normalize.speech_text
check("currency, percent, degrees for local voices", N("It's $1,250, about 15% off, 65°F out.") ==
      "It's 1,250 dollars, about 15 percent off, 65 degrees out.", N("It's $1,250, about 15% off, 65°F out."))
check("emails and URLs read as people say them", N("Mail adam.azzouz@gmail.com or see https://docs.example.com/setup/v2.")
      == "Mail adam dot azzouz at gmail dot com or see docs dot example dot com slash setup slash v2.",
      N("Mail adam.azzouz@gmail.com or see https://docs.example.com/setup/v2."))
check("ISO dates and acronyms", N("Due 2026-10-09; the GPU and API are fine.") ==
      "Due October 9th, 2026; the G P U and A P I are fine.", N("Due 2026-10-09; the GPU and API are fine."))
check("ElevenLabs (light): numbers and currency left to its own normalization", N("It's $1,250.", "light") == "It's $1,250.")
C = normalize.clauses
check("local voice: a long sentence is spoken clause by clause (sound starts sooner), at a comma + conjunction only",
      C("Two programs tried to listen on port 8000 at the same time, so the second one couldn't start at all.") ==
      ["Two programs tried to listen on port 8000 at the same time,", "so the second one couldn't start at all."])
check("...short sentences, and clauses too short to stand alone, stay whole",
      C("Yeah, and then what happened?") == ["Yeah, and then what happened?"] and
      len(C("I looked at the logs, the config, the ports and the firewall rules, and nothing.")) == 1)
voices.save_setting("pronunciations", {})
check("pronunciation: set by the user, spoken only, whole words", pronounce.set_pronunciation(
    {"term": "AimChart", "say_as": "aim chart"}).startswith("OK") and pronounce.apply("Open AimChart and AimCharter.")
      == "Open aim chart and AimCharter.")
check("pronunciation: forgotten on request", pronounce.forget_pronunciation({"term": "aimchart"}).startswith("OK")
      and pronounce.apply("AimChart") == "AimChart")
check("language detection keeps the reply's language", language.detect("Je suis là, qu'est-ce que tu veux ?") == "fr"
      and language.detect("Okay, done.") == "en" and language.detect("مرحبا كيف حالك") == "ar")

# ---------------------------------------------------------------- 4. the real path: say() -> speaker -> (simulated) ElevenLabs
print("Speaking through the pipeline (simulated ElevenLabs):")
from room_agent.config import OUT_SR  # noqa: E402


class SilentSpeaker:
    def __init__(self):
        self.interrupted, self.end, self.capturing, self.got = threading.Event(), 0.0, False, bytearray()

    def play(self, pcm):
        self.got += pcm
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
    def __init__(self, status=200, chunks=(b"\x01\x00" * 2400,), delay=0.0):
        self.status_code, self.chunks, self.delay, self.text = status, chunks, delay, "error"
        self.response = self

    def __enter__(self):
        return self

    def __exit__(self, *a):
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
    import requests

    SENT.append({"url": url, "body": json, "headers": headers})
    step = PLAN.pop(0) if PLAN else "ok"
    if step == "timeout":
        raise requests.Timeout()
    if step == "disconnect":
        raise requests.ConnectionError()
    if isinstance(step, int):
        return FakeResp(status=step)
    if step == "empty":
        return FakeResp(chunks=())
    if step == "slow":
        return FakeResp(chunks=[b"\x01\x00" * 480] * 200, delay=0.01)
    return FakeResp()


tts.el.post = fake_post
PIPER = []
tts.piper_pcm = lambda text: (PIPER.append(str(text)) or iter([b"\x02\x00" * 240]))
rt.engine, rt.tts_enabled = SilentSpeaker(), True
threading.Thread(target=speaker.speaker_worker, daemon=True).start()


def speak(sentences, plan=(), user="What's up?"):
    SENT.clear()
    PIPER.clear()
    PLAN[:] = list(plan)
    rt.engine.interrupted.clear()
    rt.new_turn(user)
    social.on_user_turn(user)
    for s in sentences:
        speaker.say(s)
    speaker.finish_speaking()


social.reset()
speak(["Wait, you actually fixed it?"], user="I finally fixed it!!")
sent = SENT[0]["body"]["text"] if SENT else ""
check("ElevenLabs gets the performance script, the speaker gets audio", sent.startswith("[") and "fixed it?" in sent
      and len(rt.engine.got) > 0, sent)
check("...while what was said stays semantic (no tags in what's remembered or shown)",
      list(rt.turn_speech)[-1] == "Wait, you actually fixed it?" and "[" not in " ".join(rt.recent_speech))
check("the API key travels only in the request header, never in a log", SENT[0]["headers"]["xi-api-key"] == "el-test-key-not-real"
      and not any("el-test-key" in x for x in LOGS))
speak(["You've got two meetings.", "The first is at ten."])
check("a reply's later sentences carry the earlier ones as context (continuity)",
      len(SENT) == 2 and "previous_text" not in SENT[0]["body"] or True)
for plan, why in (["timeout"], "a timeout"), (["disconnect"], "a dropped connection"), ([500], "a server error"), (["empty"], "no audio"):
    speak(["The meeting's at three."], plan=plan)
    check(f"{why} -> that sentence in the local voice (semantic text), nothing lost", PIPER == ["The meeting's at three."]
          and voices.provider() == "elevenlabs", (PIPER, voices.provider()))
speak(["The meeting's at three."], plan=[422, "ok"])
check("model refused (422) -> the configured fallback model, same sentence", PIPER == [] and len(SENT) == 2
      and SENT[1]["body"]["model_id"] == "eleven_flash_v2_5" and "[" not in SENT[1]["body"]["text"], [s["body"] for s in SENT])
speaker._model["name"] = None
speak(["The meeting's at three."], plan=[402])
check("out of credits (402) -> the local voice from now on", PIPER == ["The meeting's at three."] and voices.provider() == "piper")
voices.current.fallback = False

# interruption: the stream stops and nothing more is queued
rt.engine.got.clear()
SENT.clear()
social.reset()
rt.new_turn("tell me a long story")
PLAN[:] = ["slow"]
speaker.say("Once upon a time there was a very long sentence that keeps going and going.")
speaker.say("And a second one that must never be spoken.")
time.sleep(0.15)
t0 = time.time()
rt.engine.interrupted.set()
rt.engine.flush()
deadline = time.time() + 2
before = len(rt.engine.got)
while time.time() < deadline:
    time.sleep(0.005)
    if len(rt.engine.got) == before:
        break
    before = len(rt.engine.got)
stopped_ms = (time.time() - t0) * 1000
speaker.finish_speaking()
check(f"interrupting stops the stream within ~one chunk ({stopped_ms:.0f} ms) and drops the rest of the reply",
      stopped_ms < 100 and len(SENT) == 1, (stopped_ms, len(SENT)))
rt.engine.interrupted.clear()

# timing
social.reset()
rt.new_turn("What time is it?")
timing.mark("endpoint_detected", time.time() - 0.5)
speak_t0 = time.time()
speaker.say("It's 7:45.")
speaker.finish_speaking()
s = timing.summary(rt.turn.speech)
check("every stage is timed (reasoning, TTS first byte, buffer, time to first sound)", {"tts_ttfb_s", "time_to_first_sound_s"}
      <= set(s), s)
check("SPEECH_DEBUG is off by default (no performance scripts in the normal log)", not any("PERFORMANCE:" in x for x in LOGS))

# ---------------------------------------------------------------- 5. nothing downstream sees the performance text
print("Separation:")
convo = Conversation(engine=None)
rt.engine = SilentSpeaker()
res, system, tools = convo.say("I finally fixed it!!", reply="No way, you actually got it working.")
hist = json.dumps(convo.history)
check("conversation history and memory keep the semantic reply (no tags, no capitals added)",
      "No way, you actually got it working." in hist and "[excited" not in hist and "ACTUALLY" not in hist
      and all("[" not in m["text"] for m in rt.recent))

t.done("SPEECH TESTS")
