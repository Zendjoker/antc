"""Live diagnostic of the speech-input path with AUDIO_DEBUG on: python tests/barge_debug.py
Real mic, real speaker, real engine (AEC, VAD, barge-in, Whisper verification) and the same settle_mic /
record_utterance / transcribe steps the app runs after the agent talks, minus the LLM.
A local voice (Piper) plays the agent; it tells you what to say and when. Output: debug/audio/<run>/events.jsonl + wavs."""
import os
import pathlib
import sys
import time

os.environ.setdefault("AUDIO_DEBUG", "1")
os.environ.setdefault("AUDIO_DEBUG_RING_S", "600")
os.environ.setdefault("MIC_DEVICE", "Shure MV6")
os.environ.setdefault("SPEAKER_DEVICE", "Headphones (2- Shure MV6")  # the device the user picked; .env's FISHER is gone
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.chdir(pathlib.Path(__file__).resolve().parents[1])

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.audio import debug  # noqa: E402
from room_agent.audio import mic as mic_input  # noqa: E402
from room_agent.audio.devices import setup_devices  # noqa: E402
from room_agent.audio.engine import AudioEngine  # noqa: E402
from room_agent.audio.mic import record_utterance  # noqa: E402
from room_agent.audio.sounds import tone  # noqa: E402
from room_agent.audio.speech_check import classify_audio  # noqa: E402
from room_agent.audio.stt import load_whisper, transcribe, verify_barge  # noqa: E402
from room_agent.audio.tts import load_piper, piper_pcm  # noqa: E402
from room_agent.conversation.session import settle_mic  # noqa: E402

MONOLOGUE = ("Sure, let me tell you about that. Once upon a time there was a small lighthouse on a rocky coast, and every "
             "night the keeper climbed the long spiral stairs to light the great lamp, so that ships far out at sea could "
             "find their way safely home through the storm. The keeper never missed a single night in forty years.")
SHORT = "Okay, I can do that for you. Give me a second to think it over, and then I will tell you what I found."
INTERRUPTS = ["Can you motivate me?", "Bye, Jarvis.", "Wake me up by tomorrow at seven.", "What alarm are you going to set for me?",
              "Stop, I have a question.", "Tell me a joke."]
NORMAL = ["Can you motivate me?", "Wake me up by tomorrow at seven.", "What alarm are you going to set for me?"]

setup_devices()
rt.engine = engine = AudioEngine(config.OUT_SR, aec=config.AEC, noise_suppression=config.NOISE_SUPPRESSION, barge_in=config.BARGE_IN,
                                 speech_rms=config.SPEECH_RMS, barge_ms=config.BARGE_MIN_SPEECH_MS, barge_vad=config.BARGE_VAD)
engine.start(capture=True)
load_piper()
load_whisper()
engine.barge_verifier = verify_barge
rt.tts_enabled = True
transcribe(__import__("numpy").zeros(16000, dtype="int16"))  # warm up


def play_and_wait(text, interruptible=False):
    engine.interrupted.clear()
    engine.play(b"".join(piper_pcm(text)))
    time.sleep(0.1)
    while engine.is_playing() and not (interruptible and engine.interrupted.is_set()):
        time.sleep(0.02)


def run(kind, expected, monologue):
    print(f"[{kind}] say: {expected!r}" if kind != "silence" else "[silence] stay silent", flush=True)
    if kind == "interrupt":
        play_and_wait(f"Interrupt me while I talk, and say: {expected}")
    elif kind == "normal":
        play_and_wait(f"Wait until I finish and beep, then say: {expected}")
    else:
        play_and_wait("Stay silent now, and do not say anything until the next instruction.")
    time.sleep(1.0)
    engine.drain_mic()
    engine.interrupted.clear()
    rt.recent_speech.append(monologue)
    debug.event("trial_start", kind=kind, expected=expected, agent_text=monologue)
    started = time.time()
    engine.play(b"".join(piper_pcm(monologue)))
    time.sleep(0.2)
    while engine.is_playing() and not engine.interrupted.is_set():
        time.sleep(0.02)
    interrupted = engine.interrupted.is_set()
    heard, barged = settle_mic(started)
    rt.tts_end = time.time() + engine.echo_tail()
    if kind == "normal":
        engine.play(tone(880, 0.15))
        time.sleep(0.4)
    pcm = record_utterance(mic_input_q, already_heard=heard, start_timeout=7 if kind != "silence" else 8)
    text = transcribe(pcm) if pcm is not None else None
    label, detail = classify_audio(text, mic_input.last_speech_start) if text else ("none", "")
    debug.event("trial_end", kind=kind, expected=expected, interrupted=interrupted, heard=heard, barged=barged,
                recorded=pcm is not None, seconds=(len(pcm) / 16000) if pcm is not None else 0, transcript=text, label=label, detail=detail)
    print(f"    interrupted={interrupted} heard={heard} recorded={pcm is not None} -> {text!r} [{label}]", flush=True)


mic_input_q = engine.mic_q
time.sleep(1.5)
play_and_wait("Speech input test. I will give you an instruction before each trial. Keep a normal distance from the mic.")
for sentence in INTERRUPTS:
    run("interrupt", sentence, MONOLOGUE)
for sentence in NORMAL:
    run("normal", sentence, SHORT)
run("silence", "", SHORT)
play_and_wait("That was the last trial. Thank you.")
print("saved to", debug.DIR, flush=True)
engine.close()
sys.stdout.flush()
os._exit(0)
