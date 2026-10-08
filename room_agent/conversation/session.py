"""One awake stretch of conversation: no wake word needed, with check-ins and going back to sleep."""

import logging
import random
import time

import numpy as np

from room_agent import runtime as rt
from room_agent import social
from room_agent import livelog, trace
from room_agent.audio import mic as mic_input
from room_agent.audio import debug
from room_agent.audio.fillers import filler_if_slow, stop_thinking
from room_agent.audio.mic import record_utterance
from room_agent.audio.sounds import tone
from room_agent.audio.speaker import finish_speaking, say
from room_agent.audio.speech_check import AGENT_ECHO, NOISE, UNCERTAIN, classify_audio, transcript_uncertain
from room_agent.audio.stt import transcribe
from room_agent.audio.tts import clip
from room_agent.config import (CHECKIN_AFTER_S, CHECKIN_CHANCE, CHECKIN_COOLDOWN_S, EXPLAIN_DONE_S, EXPLAIN_WAIT_S,
                               LISTEN_WAIT_S, MAX_CHECKINS, MIN_VOICE_MASS, MISSING_WAIT_S, SLEEP_AFTER_CHECKIN_S,
                               SLEEP_AFTER_S, SLEEP_GRACE_S)
from room_agent.conversation.states import State
from room_agent.conversation.turn import take_turn
from room_agent.text import is_let_me_finish, is_quiet_command, join_fragments, looks_unfinished, strip_wake
from room_agent.tools.timers import acknowledge_ring

log = logging.getLogger("room-agent")

_last_checkin = 0.0


def settle_mic(started=None):
    """After the agent spoke, decide what the mic heard meanwhile. Returns (heard, interrupted):
    - you interrupted (verified): your words are kept, heard=True, interrupted=True;
    - something voice-like overlapped the end of what it said (`started` = when it began) but wasn't decided:
      the last few seconds are transcribed and checked; kept only if it's really you (heard=True);
    - otherwise everything is dropped (it was the agent's own echo)."""
    engine = rt.engine
    if engine.interrupted.is_set():
        debug.event("settle", branch="interrupted: keep everything from the interruption on")
        engine.drain_mic()  # keeps everything from your interruption on
        engine.interrupted.clear()
        return True, True
    if started is not None and engine.voice_heard_at >= started and engine.barge_verifier:
        kept = engine.keep_recent_mic(3.0)
        if kept and mic_input.voice_mass(kept) >= MIN_VOICE_MASS:  # (echo with no voice in it isn't worth transcribing)
            debug.ctx.why = "settle"
            try:
                is_user, detail = engine.barge_verifier(np.concatenate([f for f, _ in kept]), check_speaker=False)
            except Exception as e:
                is_user, detail = False, str(e)
            debug.ctx.why = "utterance"
            debug.event("settle", branch="voice overlapped the end of the reply", frames=len(kept), is_user=is_user, detail=detail)
            if is_user:
                log.info("you spoke as it finished: kept (%s)", detail)
                engine.interrupted.clear()
                return True, False
        else:
            debug.event("settle", branch="overlap but too little voice", frames=len(kept or []),
                        voice_mass=mic_input.voice_mass(kept or []))
    engine.drain_mic()
    engine.interrupted.clear()
    return False, False


def speak_phrase(kind, history=None):
    """Say a pre-recorded line (instant). Fully interruptible, and recorded as the agent's own words
    so the mic can't mistake it for you."""
    rt.turn_start = None  # no "thinking" sound for a stock phrase
    text = rt.phrases.pick(kind)
    print("Agent:", text, flush=True)
    rt.recent_speech.append(text)
    if history and history[-1]["role"] == "assistant":
        history.append({"role": "assistant", "content": text})  # so Claude knows it asked "you good?"
    rt.engine.interrupted.clear()
    started = time.time()
    try:
        rt.speak_q.put(clip(text))
        finish_speaking()
    except Exception as e:
        log.warning("couldn't say %r: %s", text, e)
    return settle_mic(started)  # (heard, interrupted)


def speak_line(text, history=None):
    """Say a line Jarvis came up with by itself (a greeting), then listen like after any reply. -> (heard, interrupted)"""
    rt.turn_start = None
    print("Agent:", text, flush=True)
    if history is not None:
        history.append({"role": "assistant", "content": text})  # (so its next reply knows it just greeted them)
    rt.engine.interrupted.clear()
    started = time.time()
    try:
        say(text)
        finish_speaking()
    except Exception as e:
        log.warning("couldn't say %r: %s", text, e)
    rt.last_reply = text
    return settle_mic(started)


def converse(mic_q, history, text=None, heard=False, barged=False):
    """ACTIVE conversation: no wake word needed. Returns why it ended: "sleep" (long silence) or
    "quiet" (you asked it to be quiet until you call it). `heard`: your verified speech is already waiting."""
    global _last_checkin
    engine = rt.engine
    quiet_since, checked_in_at, checkins, rolled_for = time.time(), None, 0, None
    pending = None  # audio already recorded (e.g. you spoke right after the goodbye)
    carry, trailed = "", False  # an unfinished sentence waiting for the rest of it
    carry_wait, expired = LISTEN_WAIT_S, False  # how long to wait for that rest; expired: the wait is over, answer it
    while True:
        if text is None:
            rt.state.go(State.LISTENING)
            if not heard:
                engine.interrupted.clear()  # (a timer announcement you talked over is over)
            now = time.time()
            sleep_at = checked_in_at + SLEEP_AFTER_CHECKIN_S if checked_in_at else quiet_since + SLEEP_AFTER_S
            checkin_at = quiet_since + CHECKIN_AFTER_S
            may_check = (
                checked_in_at is None
                and rolled_for != quiet_since  # one roll of the dice per silence
                and checkins < MAX_CHECKINS
                and now - _last_checkin >= CHECKIN_COOLDOWN_S
                and checkin_at < sleep_at
            )
            next_event = min(checkin_at, sleep_at) if may_check else sleep_at
            if pending is not None:
                pcm = pending
            else:
                if rt.explaining:  # "let me finish": give them time to start, a statement gets a little extra quiet
                    wait = EXPLAIN_DONE_S if carry.rstrip().endswith((".", "!")) else EXPLAIN_WAIT_S if not carry else LISTEN_WAIT_S
                else:
                    wait = carry_wait if carry else max(0.3, next_event - now)
                pcm = record_utterance(mic_q, start_timeout=wait, already_heard=heard)
            heard, pending = False, None
            if pcm is None:
                rt.explaining = False  # they never started (or never continued)
            if pcm is None and carry:  # they never finished the sentence: answer what was said
                text, carry, trailed, expired = join_fragments(carry, ""), "", True, True
                continue
            if pcm is None:  # silence until the next event
                if barged:
                    log.info("false alarm: nobody was actually talking")
                    barged = False
                if may_check and time.time() < sleep_at - 0.1:
                    rolled_for = quiet_since
                    if random.random() < CHECKIN_CHANCE:
                        checkins += 1
                        _last_checkin = time.time()
                        rt.state.go(State.IDLE_CHECK, "long silence")
                        heard, barged = speak_phrase("checkin", history)
                        checked_in_at = None if (heard or barged) else time.time()
                    continue
                rt.state.go(State.IDLE_CHECK, "going to sleep")
                heard, barged = speak_phrase("sleep")
                if not (heard or barged):
                    # A moment of grace: if you start talking right as it says goodbye, it stays awake.
                    rt.state.go(State.LISTENING, "grace period")
                    pending = record_utterance(mic_q, start_timeout=SLEEP_GRACE_S)
                    if pending is None:
                        return "sleep"
                    log.info("you started talking, staying awake")
                # you cut the goodbye off (or answered right after it): stay awake and listen
                quiet_since, checked_in_at = time.time(), None
                continue
            rt.state.go(State.PROCESSING)
            rt.turn_start = time.time()
            filler_if_slow(rt.turn_start)
            text = transcribe(pcm)
            rt.stt_seconds = time.time() - rt.turn_start
            social.heard(pcm, text)  # (how it was said: supporting evidence for the social layer, a few ms)
            label, detail = classify_audio(text, mic_input.last_speech_start)  # who is this? before what does it mean?
            rt.stt_uncertain = label in ("USER", "UNCERTAIN") and transcript_uncertain(text)
            conf = rt.stt_confidence or {}
            log.info("transcript: raw=%r accepted=%r -> %s (%s)%s%s", rt.stt_raw, text,
                     "ACCEPTED" if label in ("USER", "UNCERTAIN") else "REJECTED", f"{label}: {detail}",
                     f", logprob {conf['logprob']:.2f}, no-speech {conf['no_speech']:.2f}" if "logprob" in conf else "",
                     ", UNCERTAIN (no memory, nothing irreversible)" if rt.stt_uncertain else "")
            livelog.event("speech", accepted=label in ("USER", "UNCERTAIN"), label=label, why=detail, text=text,
                          raw=rt.stt_raw or "", uncertain=bool(rt.stt_uncertain), stt_s=float(rt.stt_seconds or 0),
                          **{k: round(float(v), 3) for k, v in conf.items() if isinstance(v, (int, float))})
            trace.note("INPUT", repr(text))
            trace.note("AUDIO", f"{label} ({detail})")
            debug.event("decision", text=text, label=label, detail=detail, barged=barged, tts_end_in=rt.tts_end - time.time())
            if label == NOISE:  # nothing, or what Whisper "hears" in silence ("Thank you."): not you
                stop_thinking()
                rt.turn_start = None
                if text:
                    log.info("ignored %r: %s", text, detail)
                trace.note("ACTION", "IGNORE (noise)")
                trace.flush()
                if barged:  # you cut it off with a sound, not words
                    heard, barged = speak_phrase("resume", history)
                text = None
                continue
            if label == AGENT_ECHO or (label == UNCERTAIN and rt.tts_end > time.time() and not rt.ringing):
                stop_thinking()
                rt.turn_start = None
                log.info("self-voice rejected: %s (heard %r)", detail, text)
                trace.note("ACTION", "IGNORE (agent echo)")
                trace.flush()
                text = None
                continue
            if label == UNCERTAIN:
                log.debug("kept %r: it resembles the agent's words but the agent isn't speaking (%s)", text, detail)
            if not strip_wake(text):  # "hey jarvis" again, mid-conversation
                stop_thinking()
                print("You:", text, flush=True)
                heard, barged = speak_phrase("wake", history)
                quiet_since, checked_in_at, text = time.time(), None, None
                continue
            if rt.explaining and not (is_let_me_finish(text) or is_quiet_command(text)):
                carry = join_fragments(carry, text)  # a piece of the explanation: collect it, answer a finished thought
                quiet_since, checked_in_at = time.time(), None
                if not carry.endswith("?"):
                    stop_thinking()
                    rt.turn_start = None
                    trace.note("ACTION", "HOLD (explaining)")
                    trace.flush()
                    text = None
                    continue
                text, carry, trailed, rt.explaining = carry, "", True, False
            elif carry and not is_let_me_finish(text):  # the rest of an unfinished sentence: this turn must now be answered
                text, carry, trailed = join_fragments(carry, text), "", True
        said = strip_wake(text) or text
        if not expired and looks_unfinished(said):  # trailing off: keep it and wait for the rest, no model call
            acknowledge_ring()
            stop_thinking()
            rt.turn_start = None
            carry, carry_wait, trailed = said, LISTEN_WAIT_S, False
            quiet_since, checked_in_at, text = time.time(), None, None
            trace.note("ACTION", "CONTINUE_LISTENING (unfinished)")
            trace.flush()
            continue
        expired = False
        print("You:", text, flush=True)
        acknowledge_ring()  # confirmed speech stops a ringing timer or alarm; then it's handled like any other turn
        if is_let_me_finish(said):  # decided here, in code: no model call, no reply; just keep listening patiently
            stop_thinking()
            rt.turn_start = None
            rt.explaining, quiet_since, checked_in_at, text = True, time.time(), None, None
            trace.note("ACTION", "HOLD (let me finish)")
            trace.flush()
            continue
        if is_quiet_command(said):  # decided here, in code: no model call, nothing goes into the history
            stop_thinking()
            speak_phrase("quiet")
            engine.drain_mic()  # committed: talking over the acknowledgement doesn't cancel quiet mode
            engine.interrupted.clear()
            return "quiet"
        if rt.turn_start is None:  # command said together with the wake word: thinking sound if it's slow
            rt.turn_start = time.time()
            filler_if_slow(rt.turn_start)
        t0 = time.time()
        try:
            rt.state.go(State.PROCESSING)
            note = " (answer now, don't wait for more)" if trailed else ""
            result = take_turn(history, f"(I cut you off mid-reply) {said}{note}" if barged else f"{said}{note}",
                               raw=said, final=trailed)
        except Exception as e:
            result = None
            log.error("Error: %s", e)
            if rt.tts_enabled:
                rt.speak_q.put(tone(330, 0.25))
                finish_speaking()
        finally:
            rt.turn_start = None
            heard, barged = settle_mic(t0)
        carry_wait = MISSING_WAIT_S if rt.control.get("hold") else LISTEN_WAIT_S  # (a request still lacking a detail)
        carry = said if result == "listen" and not trailed else ""  # unfinished: keep it, wait for the rest
        trailed = False
        if result and result.startswith("quiet"):  # the model recognized it (go_quiet tool)
            if result == "quiet-silent":  # nothing was said yet: acknowledge exactly once
                speak_phrase("quiet")
            engine.drain_mic()  # committed, like above
            engine.interrupted.clear()
            return "quiet"
        quiet_since, checked_in_at, text = time.time(), None, None
