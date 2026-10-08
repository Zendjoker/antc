"""Emotional & social intelligence (room_agent/social/), offline: signals from words, conversation, behavior and audio;
a decaying SocialState; the ResponseStrategy the model is given; the VoiceDelivery the voice gets; habits taken out of
replies; long-term preferences kept separate; nothing temporary saved. Scripted model, no cost.

    .venv\\Scripts\\python -m tests.test_social
"""

import os
import queue
import time

import numpy as np

from tests.harness import Checker, Conversation, setup_env

TMP = setup_env(TTS_PROVIDER="piper", PHONE_MODE="0")  # (as written: independent of .env)
from room_agent import runtime as rt  # noqa: E402
from room_agent import social  # noqa: E402
from room_agent.audio import speaker, styles  # noqa: E402
from room_agent.social import delivery as dv  # noqa: E402
from room_agent.social import habits, prosody  # noqa: E402
from room_agent.social.signals import Evidence  # noqa: E402
from room_agent.text import is_quiet_command  # noqa: E402

t = Checker()
check = t.check


def turn(text, failed_before=0):
    """One user turn through the social layer (after `failed_before` failed exchanges). -> (strategy, snapshot, delivery)"""
    for _ in range(failed_before):
        rt.new_turn("open spotify")
        social.on_user_turn("open spotify")
        social.state.update_last(failed=True)
    rt.new_turn(text)
    s = social.on_user_turn(text)
    return s, social.state.snapshot(), rt.turn.delivery


def fresh(text, failed_before=0):
    social.reset()
    return turn(text, failed_before)


# ---------------------------------------------------------------- 1. signals -> state -> strategy (the examples)
print("Reading the moment:")
s, snap, d = fresh("Man... today was fucking exhausting.")
check("'today was fucking exhausting' -> low energy; calm, short, no roasting, some warmth, gentle questions only",
      snap["energy"] == "low" and s.tone.startswith("calm") and s.response_energy == "low"
      and s.response_verbosity in ("short", "minimal") and s.humor_level in ("off", "light") and s.roast_level == "off"
      and s.supportiveness == "medium" and s.questioning in ("gentle", "if_needed", "none"), (snap, s))
check("...and the voice: softer, a little slower (subtle)", d.style == "soft" and 0.94 <= d.pace < 1.0, d)
s, snap, d = fresh("LET'S GO!")
check("'LET'S GO!' -> high energy, upbeat (energy can rise)", snap["energy"] == "high" and s.response_energy == "high", (snap, s))
s, snap, d = fresh("this shit still doesn't work", failed_before=2)
check("'this shit still doesn't work' after failures -> focused: calm, short, direct, no jokes, no anger back",
      snap["interaction_mode"] == "focused" and snap["frustration"] in ("medium", "high") and s.humor_level == "off"
      and s.directness == "high" and s.response_verbosity == "short" and "calm" in s.tone
      and any("don't match their irritation" in n for n in s.notes), (snap, s))
s3, snap3, _ = fresh("Bro...", failed_before=3)
s0, snap0, _ = fresh("Bro...", failed_before=0)
check("'Bro...' after three failures reads as frustration; on its own it doesn't",
      snap3["interaction_mode"] == "focused" and snap0["interaction_mode"] == "task" and snap0["frustration"] == "low",
      (snap3["values"]["frustration"], snap0["values"]["frustration"]))
for phrase in ("whatever", "Just leave it.", "leave me alone"):
    s, snap, _ = fresh(phrase)
    check(f"{phrase!r} -> a tiny reply (or nothing), no question, no follow-up", s.response_verbosity == "minimal"
          and s.max_sentences == 1 and s.questioning == "none" and s.allow_silence, s)
s, snap, _ = fresh("finally!")
check("'finally!' -> positive", snap["mood_signal"] == "positive" or snap["values"]["positive"] >= 0.3, snap)
s, snap, d = fresh("that was fucking awesome")
check("swearing isn't frustration by itself: 'fucking awesome' -> excited", snap["mood_signal"] in ("excited", "positive")
      and snap["frustration"] == "low", snap)
s, snap, d = fresh("haha you're useless")
check("'haha you're useless' -> joking (humor up), not annoyed", snap["interaction_mode"] == "joking" and s.humor_level != "off"
      and snap["values"]["annoyed"] == 0, snap)
s, snap, d = fresh("you're useless", failed_before=2)
check("'you're useless' after failures -> annoyed with Jarvis: own it briefly, no humor",
      snap["values"]["annoyed"] >= 0.35 and s.humor_level == "off" and any("own it" in n for n in s.notes), (snap, s))
s, snap, d = fresh("my grandma is in the hospital")
check("something heavy -> serious: no jokes, no teasing, warm, slower", snap["seriousness"] in ("medium", "high")
      and s.humor_level == "off" and s.roast_level == "off" and s.supportiveness == "high" and d.pace < 1.0, (snap, s))
s, snap, d = fresh("quick, how do I put out a kitchen fire")
check("urgent -> clear, direct, short, a bit faster, no jokes", snap["interaction_mode"] == "urgent" and s.humor_level == "off"
      and s.directness == "high" and s.response_verbosity == "short" and d.pace > 1.0 and d.style == "", (snap, s, d))
s, snap, d = fresh("what's the weather tomorrow")
check("a plain request -> default strategy, nothing added to the prompt", s.is_default() and s.render() == "", s)
check("'Stop talking.' still stops Jarvis (the existing quiet command)", is_quiet_command("Stop talking."))

# ---------------------------------------------------------------- 2. text alone is weak; more sources make it stronger
print("Evidence from several sources:")
s1, snapA, _ = fresh("this doesn't work")
social.reset()
turn("open the browser", failed_before=0)
social.state.update_last(failed=True)
turn("open the browser")
social.state.update_last(failed=True)
s2, snapB, _ = turn("open the browser")
check("text alone: medium at most; repeated request after failures: stronger, from more sources",
      snapA["frustration"] in ("low", "medium") and snapB["values"]["frustration"] > snapA["values"]["frustration"]
      and "behavior" in snapB["sources"], (snapA["values"]["frustration"], snapB))
social.reset()
turn("what's the time")
social.state.update_last(reply="It's three.")
s, snap, _ = turn("no, I said the date")
check("a correction counts (behavior), and asking for shorter answers counts", snap["values"]["frustration"] > 0
      and fresh("keep it short")[0].response_verbosity == "short")
social.reset()
turn("tell me about rome")
social.state.update_last(interrupted=True)
turn("and paris")
social.state.update_last(interrupted=True)
s, snap, _ = turn("and london")
check("being cut off twice -> shorter answers", s.response_verbosity == "short" and snap["values"]["brief"] >= 0.4, snap)
s, snap, _ = fresh("tell me more about that")
check("'tell me more' -> wants to talk (questions welcome)", snap["values"]["talk"] >= 0.35 and s.questioning == "open", s)

# ---------------------------------------------------------------- 3. audio: supporting evidence only, vs their own usual
print("Audio (prosody):")


def speech(words, seconds, amp):
    """Voice-like audio: harmonic bursts (150 Hz) with pauses, `words` bursts over `seconds`."""
    sr, out = 16000, []
    burst = seconds / max(words, 1)
    for _ in range(words):
        n = int(sr * burst * 0.7)
        tt = np.arange(n) / sr
        tone = sum(np.sin(2 * np.pi * 150 * k * tt) / k for k in (1, 2, 3)) * np.hanning(n)
        out += [tone * amp, np.zeros(int(sr * burst * 0.3))]
    return np.concatenate(out + [np.zeros(sr // 4)]).astype(np.float32).clip(-32767, 32767).astype(np.int16)


social.reset()
for i in range(4):
    social.heard(speech(8, 3.0, 2000), "one two three four five six seven eight")
    turn("so anyway what's next")
ev_loud = prosody.evidence(prosody.measure(speech(14, 3.0, 5000), " ".join(["word"] * 14)), time.time())
ev_quiet = prosody.evidence(prosody.measure(speech(5, 3.0, 700), "word word word word word"), time.time())
check("louder and faster than THEIR usual -> more energy; quieter and slower -> less",
      any(e.dim == "energy_up" for e in ev_loud) and any(e.dim == "energy_down" for e in ev_quiet), (ev_loud, ev_quiet))
check("audio never produces frustration, joking or sadness on its own, and stays weak",
      all(e.dim in ("energy_up", "energy_down") and e.weight <= 0.3 for e in ev_loud + ev_quiet))
prosody.reset()
check("no baseline yet (first utterances) -> no audio evidence at all",
      prosody.evidence(prosody.measure(speech(14, 3.0, 5000), " ".join(["w"] * 14)), time.time()) == [])
t0 = time.time()
for _ in range(20):
    prosody.measure(speech(12, 5.0, 2000), " ".join(["w"] * 12))
check("measuring 5 s of speech is cheap (< 40 ms), so it never delays the answer", (time.time() - t0) / 20 < 0.04,
      round((time.time() - t0) / 20 * 1000, 1))
t0 = time.time()
for _ in range(100):
    rt.new_turn("this still doesn't work bro")
    social.on_user_turn("this still doesn't work bro")
check("a whole social update is a few ms (no model call)", (time.time() - t0) / 100 < 0.005, round((time.time() - t0) * 10, 2))

# ---------------------------------------------------------------- 4. decay and override
print("Decay and override:")
social.reset()
old = time.time() - 20 * 60
social.state.add([Evidence("frustration", 0.8, "language", old, "old"), Evidence("annoyed", 0.6, "behavior", old, "old")])
social.state.turns = 3
s, snap, _ = turn("what's on tonight")
check("frustrated 20 minutes ago is not frustrated now", snap["frustration"] == "low" and snap["interaction_mode"] != "focused",
      snap["values"])
social.reset()
turn("this shit still doesn't work", failed_before=2)
s, snap, _ = turn("haha nice, that fixed it")
check("clear current behavior overrides the earlier inference (frustration ends at once)", snap["frustration"] == "low"
      and snap["interaction_mode"] != "focused", snap["values"])

social.reset()
turn("whatever")
s, snap, _ = turn("my grandma is in the hospital")
check("'whatever' is about THAT reply only: the next turn isn't cut short", s.max_sentences == 0
      and s.response_verbosity != "minimal" and snap["interaction_mode"] == "emotional", (s, snap["values"]))
social.reset()
turn("leave me alone")
s, snap, _ = fresh("enough. quick, how do I put out a grease fire")
check("urgent answers are never capped to a tiny reply", s.max_sentences == 0 and snap["interaction_mode"] == "urgent", s)
social.reset()
turn("this shit still doesn't work", failed_before=2)
s, snap, _ = turn("lol okay whatever, it's funny at this point")
check("a joke after frustration ends the frustration (current behavior wins)", snap["frustration"] == "low", snap["values"])
from room_agent.memory.writer import SUMMARY_SYSTEM  # noqa: E402

check("conversation summaries are told to leave passing moods out (only durable things are kept)",
      "Leave out passing moods" in SUMMARY_SYSTEM)

# ---------------------------------------------------------------- 5. long-term preferences (UserModel) stay separate
print("Long-term preferences:")
from room_agent import learning  # noqa: E402
from room_agent.social.strategy import long_term  # noqa: E402

m = learning.user_model()
m.teach("response_style", "short", because="you said: keep answers short")
s, snap, _ = fresh("what do you think about space travel")
check("prefers short answers (UserModel) -> short, even in casual talk", s.response_verbosity == "short", s)
m.teach("humor", "no jokes", subject="morning", because="you said: no jokes in the morning")
import datetime  # noqa: E402

check("'no jokes in the morning' applies before noon, not after",
      long_term("haha", "joking", datetime.datetime(2026, 10, 7, 8))["humor"] == "off"
      and long_term("haha", "joking", datetime.datetime(2026, 10, 7, 15))["humor"] is None)
from room_agent.learning.model import key_for  # noqa: E402

m.forget(key_for("humor", "morning"))  # (done with it: otherwise this test depends on the hour it's run at)
m.teach("humor", "occasional roasting is fine", subject="always", because="you said: you can roast me")
s, snap, _ = fresh("haha you're so slow lol")
check("likes occasional roasting -> light teasing when joking", s.roast_level == "light", s)
s, snap, _ = fresh("my dog died this morning")
check("...but never when something is serious", s.roast_level == "off" and s.humor_level == "off", s)
for k in [p["key"] for p in m.all(include_tentative=True)]:
    m.forget(k)
mem_before = rt.memory.count()
social.reset()
for text in ("I'm so tired", "ugh this doesn't work", "LET'S GO", "my grandma is in the hospital"):
    turn(text)
learned = learning.store().preferences(m.user)
check("moods are never written to long-term memory or learned preferences", rt.memory.count() == mem_before and not learned
      and not [f for f in os.listdir(TMP) if "social" in f.lower()], (rt.memory.count(), learned, os.listdir(TMP)))

# ---------------------------------------------------------------- 6. habits taken out of replies
print("Assistant habits:")
check("stock openers go, the content stays", habits.scrub("Certainly! The timer is set.") == "The timer is set."
      and habits.scrub("Of course, it's 5 PM.") == "It's 5 PM.")
check("stock closers and therapy lines are dropped", all(habits.scrub(x) == "" for x in (
    "Is there anything else I can help you with?", "Let me know if you need anything else.",
    "I understand how frustrating that must be.", "Hope that helps!", "Feel free to ask if you have more questions.")))
check("ordinary sentences are untouched", habits.scrub("Let me know what you think of the draft.") ==
      "Let me know what you think of the draft." and habits.scrub("I understand the plan now.") == "I understand the plan now.")
check("the user's name isn't used again right away", habits.scrub("Hey Adam, it's raining.", "Adam", True) == "It's raining."
      and habits.scrub("Hey Adam, it's raining.", "Adam", False) == "Hey Adam, it's raining.")

# ---------------------------------------------------------------- 7. the voice (VoiceDelivery)
print("Voice delivery:")
check("ElevenLabs speed stays within its allowed range and their own chosen rate",
      dv.elevenlabs_speed(1.12, dv.VoiceDelivery(pace=1.05)) <= 1.2 and dv.elevenlabs_speed(0.75, dv.VoiceDelivery(pace=0.95)) >= 0.7
      and dv.elevenlabs_speed(1.0, None) == 1.0)
check("Piper: pace -> length_scale, energy -> a small volume change; defaults untouched",
      dv.piper_config(dv.VoiceDelivery(pace=0.95, energy="low")) == {"length_scale": 1.053, "volume": 0.94}
      and dv.piper_config(dv.VoiceDelivery()) is None)
fresh("Man... today was exhausting.")
q, rt.speak_q = rt.speak_q, queue.Queue()
try:
    speaker.say("Yeah, rest up.")
    item = rt.speak_q.get_nowait()
finally:
    rt.speak_q = q
check("each spoken sentence carries the reply's delivery; the words are unchanged", str(item) == "Yeah, rest up."
      and item.delivery and item.delivery.pace == 0.95 and item.style == "soft", (item, item.delivery, item.style))
check("a style tag never changes the text when the voice can't use tags (Piper now)", styles.delivery_text(item) == "Yeah, rest up.")
orig = styles.supported
styles.supported = lambda: True
try:
    check("...and with an ElevenLabs v3/v4 voice the tag is only added to what the voice reads",
          styles.delivery_text(item) == "[softly, gently] Yeah, rest up." and str(item) == "Yeah, rest up.")
finally:
    styles.supported = orig
check("no cartoon voices: pace stays within 5% either way", all(0.95 <= dv.PACE[p] <= 1.05 for p in dv.PACE))

# ---------------------------------------------------------------- 8. through the real conversation turn (scripted model)
print("In a conversation:")
social.reset()
convo = Conversation(engine=None)
convo.say("open spotify", [("open_app", {"app_name": "NoSuchAppAnywhere"})], "Hmm, couldn't find it.")
convo.say("open spotify", [("open_app", {"app_name": "NoSuchAppAnywhere"})], "Still can't find it.")
res, system, tools = convo.say("ugh this still doesn't work", reply="Spotify isn't installed, want me to open the web player?")
check("the model gets one line on how to answer (from code), told never to name feelings",
      "how to answer now" in system and "never name or guess their feelings" in system and "no jokes" in system
      and "don't match their irritation" in system, system[-800:])
res, system, tools = convo.say("Whatever.", reply="Alright. Want me to try again later? Let me know if you need anything else.")
check("'Whatever.' -> only the tiny reply is spoken", convo.said() == ["Alright."], convo.said())
social.reset()
convo.say("what's up", reply="Not much. You?")
convo.say("nothing really", reply="Fair. Anything on your mind?")
res, system, tools = convo.say("eh, not really")
check("after two replies that ended with questions, the next is told not to ask one", "don't ask a question" in system,
      system[-600:])
convo.say("what time is it", reply="Certainly! It's about noon.")
check("'Certainly!' never reaches the speaker", convo.said() == ["It's about noon."], convo.said())

t.done("SOCIAL TESTS")
