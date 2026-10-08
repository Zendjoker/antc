"""The two ways to run the agent: by voice (wake word) or by typing."""

import logging
import threading
import time

import numpy as np

from room_agent import runtime as rt
from room_agent import config
from room_agent import trace
from room_agent.audio.fillers import stop_thinking
from room_agent.audio import speaker_id
from room_agent.audio.mic import record_utterance, wait_for_wake
from room_agent.audio.speaker import beep
from room_agent.audio.stt import load_whisper, transcribe, verify_barge
from room_agent.audio.tts import clip, el, load_piper, record_in_background
from room_agent.config import (BARGE_VERIFY, EL_KEY, LLM_PROVIDER, MODEL, SR, STT_PROVIDER, TTS_PROVIDER, WAKE_ACK_DELAY,
                               WAKE_SOUND_ON, WAKE_WORD)
from room_agent.conversation.history import start_history
from room_agent.conversation import greet
from room_agent.conversation.session import converse, speak_line, speak_phrase
from room_agent.conversation.states import State
from room_agent.conversation.turn import take_turn
from room_agent.llm.client import client
from room_agent.text import WAKE_PREFIX, is_quiet_command, strip_wake

log = logging.getLogger("room-agent")


def _try(fn):
    try:
        fn()
    except BaseException as e:
        log.debug("%s: %s", getattr(fn, "__name__", fn), e)


def warm_up():
    """Do the slow first-time setup now (GPU kernels, TLS connections) so your first question isn't slower."""
    t0 = time.time()
    if TTS_PROVIDER == "elevenlabs":
        # the free local voice is the backup if ElevenLabs runs out of credits: have it ready
        threading.Thread(target=lambda: _try(load_piper), daemon=True).start()
    try:
        if STT_PROVIDER == "whisper":
            transcribe(np.zeros(SR, dtype=np.int16))
        if LLM_PROVIDER == "claude":
            client().models.retrieve(MODEL)
        if TTS_PROVIDER == "elevenlabs":
            el.get("https://api.elevenlabs.io/v1/models", headers={"xi-api-key": EL_KEY}, timeout=5)
    except Exception as e:
        log.debug("warm-up: %s", e)
    log.debug("warm-up took %.1fs", time.time() - t0)


def voice_loop():
    import openwakeword
    from openwakeword.model import Model

    engine, phrases, state, writer = rt.engine, rt.phrases, rt.state, rt.writer
    # WAKE_WORD is a built-in name or a path to your own trained .onnx file; either way this fetches the feature models
    openwakeword.utils.download_models(model_names=[WAKE_WORD])
    oww = Model(wakeword_models=[WAKE_WORD], inference_framework="onnx")
    if STT_PROVIDER == "whisper":
        load_whisper()
        if BARGE_VERIFY:
            engine.barge_verifier = verify_barge
    if speaker_id.gate.active():
        speaker_id.gate.score(np.zeros(SR, dtype=np.int16))  # warm up the model
        engine.set_sensitive(True, config.SPEAKER_BARGE_VAD, config.SPEAKER_BARGE_RMS, config.SPEAKER_BARGE_GAP)
        if engine.barge_verifier is None:
            engine.barge_verifier = speaker_id.verify_speaker_only
    else:
        log.info("speaker verification not active (%s)", speaker_id.gate.error)
    if rt.tts_enabled:
        # record every stock phrase once (cached on disk), in the background
        record_in_background(phrases.everything())
        for kind in ("wake", "ack", "quiet"):  # the ones needed first, right now
            for p in phrases.pools[kind]:
                clip(p)
    warm_up()

    mic_q = engine.mic_q
    history = start_history()
    if WAKE_SOUND_ON == "startup":
        beep()
        engine.drain_mic()
    state.go(State.WAKE_WORD_ONLY, "started")
    unsummarized = None  # a conversation that ended in quiet mode: summarized after the next wake
    while True:
        # Asleep (or in quiet mode): only the wake word detector runs. No speech recognition, no model calls.
        log.info("Listening for '%s'%s...", WAKE_WORD, " (quiet mode)" if state.quiet else "")
        heard_wake = wait_for_wake(mic_q, oww)
        greeting = greet.take() if heard_wake is None else ""
        if heard_wake is None and not greeting:
            continue  # (a greeting that went stale: back to waiting for the wake word)
        state.go(State.LISTENING, "wake word")  # this also ends quiet mode
        engine.interrupted.clear()
        if writer and unsummarized is not None:
            writer.conversation_ended(unsummarized)
            unsummarized = None
        started = writer.mark() if writer else 0
        ended = "sleep"
        try:
            # Did you keep going ("hey jarvis, what's the weather?") or stop at "hey jarvis"? The recording
            # starts with "hey jarvis" itself so no word of the command gets clipped; it's stripped from the text.
            if greeting:  # you came home: it speaks first, then it's an ordinary conversation (no wake word needed)
                state.go(State.IDLE_CHECK, "greeting you")
                heard, barged = speak_line(greeting, history)
                ended = converse(mic_q, history, heard=heard, barged=barged)
            else:
                pcm = record_utterance(mic_q, start_timeout=WAKE_ACK_DELAY, prefix=heard_wake)
                command = strip_wake(transcribe(pcm)) if pcm is not None else ""
                if command:
                    ended = converse(mic_q, history, text=command)
                else:
                    if WAKE_SOUND_ON == "wake":
                        beep()
                    state.go(State.IDLE_CHECK, "answering the wake word")
                    heard, barged = speak_phrase("wake", history)
                    ended = converse(mic_q, history, heard=heard, barged=barged)
        except Exception:
            log.exception("conversation crashed, going back to listening")  # never leave it deaf
        if writer and ended != "quiet":
            writer.conversation_ended(started)  # a line or two about it, for next time
        elif writer:
            unsummarized = started  # quiet means no model calls at all: this waits for the next wake
        state.go(State.QUIET if ended == "quiet" else State.WAKE_WORD_ONLY,
                 "you asked me to be quiet" if ended == "quiet" else "long silence")
        oww.reset()
        engine.drain_mic()


def text_loop():
    """Typing instead of talking, with the same states: in quiet mode only "hey jarvis" gets an answer."""
    state, phrases = rt.state, rt.phrases
    history = start_history()
    state.go(State.LISTENING, "text mode")
    print("Text mode. Type a message, or 'quit'.")
    carry = ""  # an unfinished sentence waiting for the rest of it
    while True:
        try:
            text = input("You: ").strip()
        except EOFError:
            return
        if text.lower() in ("quit", "exit"):
            return
        if not text:
            continue
        if carry and not state.quiet:
            text, carry = f"{carry} {text}", ""
        trace.note("INPUT", repr(text))
        trace.note("AUDIO", "USER (typed)")
        if state.quiet:
            if not WAKE_PREFIX.match(text):
                continue  # quiet mode: no answer, no model call
            state.go(State.LISTENING, "wake word")
            text = strip_wake(text)
            if not text:
                print("Agent:", phrases.pick("wake"), flush=True)
                continue
        said = strip_wake(text) or text
        if is_quiet_command(said):
            print("Agent:", phrases.pick("quiet"), flush=True)
            state.go(State.QUIET, "you asked me to be quiet")
            continue
        try:
            state.go(State.PROCESSING)
            result = take_turn(history, said)
            if result == "listen":
                carry = said
                print("(listening...)", flush=True)
                state.go(State.LISTENING)
                continue
            if result and result.startswith("quiet"):
                if result == "quiet-silent":
                    print("Agent:", phrases.pick("quiet"), flush=True)
                state.go(State.QUIET, "you asked me to be quiet")
                continue
            state.go(State.LISTENING)
        except Exception as e:
            log.error("Error: %s", e)
            stop_thinking()
            state.go(State.LISTENING, "after an error")
