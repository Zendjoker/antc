"""Where the time goes between the user stopping and Jarvis being heard. One set of timestamps per turn (rt.turn.speech),
each recorded once, by whichever stage reaches it first:

    user_endpoint       the user's last speech frame (audio/mic.py)
    endpoint_detected   silence long enough to end the turn: recording stops, transcription starts
    reasoning_start     the model (or a reflex / pending action) starts working on the answer
    first_text          the first complete sentence of the answer exists (semantic text)
    tts_start           the voice engine starts on it (ElevenLabs request sent / Piper synthesis started)
    tts_first_byte      the first audio bytes come back from the voice engine
    first_audio         the first audio is handed to the speaker
    first_audible       ...plus what was already queued ahead of it in the output buffer (≈ when it's heard)
    generation_done     the last sentence's audio has been generated

summary() turns them into: endpointing, speech-to-text, reasoning, queue wait, TTS time-to-first-byte, buffer latency and
the one number that matters most, user stop -> first audible sound.
"""

import logging
import time

from room_agent import runtime as rt

log = logging.getLogger("room-agent")
ORDER = ("user_endpoint", "endpoint_detected", "reasoning_start", "first_text", "tts_start", "tts_first_byte", "first_audio",
         "first_audible", "generation_done")


def _speech():
    t = rt.turn
    if not hasattr(t, "speech") or t.speech is None:
        t.speech = {}
    return t.speech


def mark(name, at=None, overwrite=False):
    s = _speech()
    if overwrite or name not in s:
        s[name] = at if at is not None else time.time()


def summary(speech=None):
    s = speech if speech is not None else _speech()

    def d(a, b):
        return round(s[b] - s[a], 3) if a in s and b in s and s[b] >= s[a] else None

    start = "user_endpoint" if "user_endpoint" in s else "endpoint_detected"
    out = {"endpointing_s": d("user_endpoint", "endpoint_detected"), "stt_s": d("endpoint_detected", "reasoning_start"),
           "reasoning_s": d("reasoning_start", "first_text"), "queue_s": d("first_text", "tts_start"),
           "tts_ttfb_s": d("tts_start", "tts_first_byte"), "audio_buffer_s": d("first_audio", "first_audible"),
           "time_to_first_sound_s": d(start, "first_audible"), "generation_s": d("tts_start", "generation_done")}
    return {k: v for k, v in out.items() if v is not None}


def log_summary():
    out = summary()
    if out.get("time_to_first_sound_s") is not None:
        log.info("speech timing: " + ", ".join(f"{k[:-2].replace('_', ' ')} {v:.2f}s" for k, v in out.items()))
    return out
