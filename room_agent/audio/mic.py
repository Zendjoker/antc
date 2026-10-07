"""Listening: pulling frames from the mic, detecting speech, waiting for the wake word, recording an utterance."""

import collections
import time

import numpy as np

from room_agent import runtime as rt
from room_agent.audio import debug
from room_agent.config import (EXPLAIN_PATIENCE, FRAME, MIN_VOICE_MASS, SILENCE_S, SPEECH_RMS, SR, VAD_THRESHOLD,
                               WAKE_GUARD_S, WAKE_THRESHOLD)

last_speech_start = None  # when the latest utterance's speech began (epoch seconds), for judging echo by timing


def next_frame(mic_q):
    """Next (cleaned audio, voice probability) from the mic, skipping internal markers."""
    while True:
        item = mic_q.get()
        if isinstance(item, tuple):
            return item


def is_speech(pcm, voice):
    """A human voice (voice detector), close enough to be talking to us (loudness)."""
    rms = np.sqrt(np.mean(pcm.astype(np.float32) ** 2))
    return voice >= VAD_THRESHOLD and rms >= SPEECH_RMS * 0.5


def voice_mass(frames):
    """How much voice a stretch of audio holds: summed voice probability of its voiced frames."""
    return sum(v for _, v in frames if v >= 0.3)


def wait_for_wake(mic_q, oww):
    """Block until the wake word. Returns the last ~2 s of audio, which contain "hey jarvis" itself
    (and the start of a command if you kept talking)."""
    oww.reset()
    recent = collections.deque(maxlen=int(2.0 * SR / FRAME))
    while True:
        pcm, voice = next_frame(mic_q)
        recent.append((pcm, voice))
        engine = rt.engine
        if engine and (engine.is_playing() or time.time() - engine._last_play < WAKE_GUARD_S):
            continue  # don't let the agent (e.g. "just say hey Jarvis") wake itself
        scores = oww.predict(pcm)
        if max(scores.values()) > WAKE_THRESHOLD:
            return list(recent)


def record_utterance(mic_q, silence_s=None, max_s=15, start_timeout=5, prefix=(), already_heard=False):
    """Record until you stop talking. Returns None if you never start (silence is never returned).
    `prefix` frames (already captured, e.g. "hey jarvis") start the recording but don't count as you talking now.
    `already_heard`: what's waiting in the queue is your verified speech (an interruption), so the recording
    ends at your next pause instead of waiting for you to start."""
    silence_s = (SILENCE_S if silence_s is None else silence_s) * (EXPLAIN_PATIENCE if rt.explaining else rt.patience)
    frames, silent, heard, talked, waited = [f for f, _ in prefix], 0.0, already_heard, 0.0, 0.0
    mass = 0.0  # voice evidence collected in this recording
    first_i = last_i = None  # frame numbers of the first and last speech frame (diagnostics)
    n_speech = 0
    global last_speech_start
    last_speech_start = time.time() if already_heard else None
    while True:
        f, voice = next_frame(mic_q)
        frames.append(f)
        step = len(f) / SR
        if voice >= 0.3:
            mass += voice
        if is_speech(f, voice):
            if not heard:
                last_speech_start = time.time() - step
            heard, silent = True, 0.0
            last_i, n_speech = len(frames) - 1, n_speech + 1
            first_i = last_i if first_i is None else first_i
        else:
            silent += step
        if heard:
            talked += step
            if silent >= silence_s and not already_heard and mass < MIN_VOICE_MASS:
                # a click or echo flicker, not speech: never hand it to Whisper (it invents words from it)
                debug.event("vad_rejected_trigger", frames=len(frames), speech_frames=n_speech, voice_mass=mass)
                frames, heard, silent, mass, last_speech_start = (
                    frames[: len(prefix)] + frames[max(len(prefix), len(frames) - 6):], False, 0.0, 0.0, None)
                waited, talked = waited + talked, 0.0
                if waited > start_timeout:
                    return None
                continue
            if silent >= silence_s or talked > max_s:  # max length counts from your first word
                break
        else:
            waited += step
            if waited > start_timeout:
                return None
    if debug.ENABLED:
        raw, tts_share, t_first = debug.raw_of(frames)
        debug.event("utterance", frames=len(frames), seconds=len(frames) * FRAME / SR, already_heard=already_heard,
                    prefix_frames=len(prefix), first_speech_frame=first_i, last_speech_frame=last_i, speech_frames=n_speech,
                    voice_mass=mass, ended="max_s" if talked > max_s else "silence", tts_share=tts_share,
                    processed=debug.wav("utterance_processed", np.concatenate(frames)), raw=debug.wav("utterance_raw", raw))
    return np.concatenate(frames)
