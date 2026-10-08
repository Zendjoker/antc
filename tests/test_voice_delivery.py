"""Voice delivery, offline: "talk normal" applies at once and is kept, delivery tags never reach the stored conversation,
and a weak emotional guess doesn't change how Jarvis sounds. (Real ElevenLabs streaming/cancellation was measured
separately with two short requests; this suite never calls it.)

Run:  .venv\\Scripts\\python -m tests.test_voice_delivery
"""

import json

from tests.harness import setup_env

setup_env(TTS_PROVIDER="elevenlabs", ELEVENLABS_MODEL="eleven_v4_turbo", ELEVENLABS_API_KEY="fake-not-used")

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.audio import voices  # noqa: E402
from room_agent.cognition import reflex  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

t = Checker()
rt.tts_enabled = True
convo = Conversation()

print("'Talk normal':")
voices.save_style("soft")
for said in ("talk normal", "talk normally again", "speak like a normal person", "go back to your normal voice"):
    m = reflex.match(said)
    t.check(f"{said!r} -> handled directly (no model call)", m is not None and m[0].name == "set_speaking_style", m and m[1])
voices.save_style("soft")
convo.say("talk normal", reply="SHOULD NOT BE NEEDED")
saved = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
t.check("applied at once and kept across restarts (settings file)", voices.current.style == "normal"
        and saved.get("speaking_style") == "normal" and not convo.requests, (voices.current.style, saved, len(convo.requests)))
t.check("...with a short spoken confirmation", convo.said() == ["Okay, normal voice."], convo.said())
voices.current.fallback = True  # (ElevenLabs down: the local voice is speaking)
voices.save_style("soft")
convo.say("talk normal", reply="SHOULD NOT BE NEEDED")
t.check("works with the local fallback voice too", voices.current.style == "normal", voices.current.style)
voices.current.fallback = False

print("Delivery tags never stored:")
convo.say("I had a rough day", reply="[soft] That sounds rough. [warm] Want to talk about it?")
stored = " ".join(m["content"] if isinstance(m["content"], str) else " ".join(b.get("text", "") for b in m["content"]
                                                                             if isinstance(b, dict))
                  for m in convo.history if m["role"] == "assistant")
t.check("the conversation history keeps the words only", "[soft]" not in stored and "[warm]" not in stored
        and "That sounds rough." in stored, stored[-200:])
t.check("the transcript (dashboard / memory) too", all("[" not in m["text"] for m in rt.recent), [m["text"] for m in rt.recent][-2:])
t.check("what went to the speaker is the words, not the tags", all(not s.startswith("[") for s in convo.said()), convo.said())

print("No style change from a weak guess:")
from room_agent.speech import director  # noqa: E402
from room_agent.social.strategy import ResponseStrategy  # noqa: E402

weak = ResponseStrategy(mode="emotional", tone="gentle", response_energy="low", confidence=0.2)
p = director.direct("Yeah, that makes sense.", weak, None)
t.check("a low-confidence mood reading leaves the delivery plain", not p.direction, (p.direction, p.why))
t.done("VOICE DELIVERY")
