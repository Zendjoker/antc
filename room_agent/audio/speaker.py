"""Playback: the speaker thread that turns queued text and sounds into audio, and the helpers that feed it."""

import logging
import re
import time

import requests

from room_agent import runtime as rt
from room_agent.audio import tts, voices
from room_agent.audio.fillers import LOOP, speaker_busy, start_thinking, stop_thinking
from room_agent.audio.sounds import load_sound, tone
from room_agent.audio.styles import Spoken, delivery_text
from room_agent.config import EL_KEY, EL_MODEL, OUT_SR, WAKE_SOUND
from room_agent.conversation.states import State
from room_agent.text import strip_stage_directions

log = logging.getLogger("room-agent")

_wake_sound = None


def finish_speaking():
    """Wait until everything queued has been fetched and played (or you interrupted)."""
    rt.speak_q.join()
    stop_thinking()  # a reply with no words (only a tool call) mustn't leave the loop running
    if rt.engine:
        rt.engine.wait_drained()


def beep():
    """Wake-up sound: your intro clip if there is one, otherwise a short beep. Waits until it's done."""
    global _wake_sound
    if _wake_sound is None:
        _wake_sound = tone()
        if WAKE_SOUND:
            try:
                _wake_sound = load_sound(WAKE_SOUND)
            except Exception as e:
                log.warning("couldn't load WAKE_SOUND %s (%s), using a beep", WAKE_SOUND, e)
    rt.speak_q.put(_wake_sound)
    finish_speaking()


def say(sentence):
    """Queue a sentence of the model's reply for speaking (and remember it, so its echo isn't mistaken for you)."""
    sentence = re.sub(r"\s*[\u2014\u2013]\s*", ", ", strip_stage_directions(sentence))  # (dashes read badly aloud)
    if not re.search(r"\w", sentence):
        return
    if rt.turn.output:  # (a phone call: the reply goes down the line, not to the room speaker)
        rt.recent_speech.append(sentence)
        rt.spoken_count += 1
        rt.turn.output(sentence)
        return
    if rt.tts_enabled and not (rt.engine and rt.engine.interrupted.is_set()):
        rt.recent_speech.append(sentence)
        rt.turn_speech.append(sentence)
        rt.spoken_count += 1
        item = Spoken(sentence)
        item.style = rt.turn_style
        rt.speak_q.put(item)


def mark_first_audio():
    if rt.turn_start is not None:
        log.info("answer started %.2fs after you stopped talking", time.time() - rt.turn_start)
        rt.turn.timing.setdefault("first_sound", time.time() - rt.turn_start)
        rt.turn_start = None
        rt.state.go(State.SPEAKING)


def _play(out, pcm):
    out.play(pcm)
    rt.tts_end = time.time() + out.queued_seconds()


def _payload(item):
    body = {"text": delivery_text(item), "model_id": EL_MODEL}
    if rt.speech_rate != 1.0:
        body["voice_settings"] = {"speed": rt.speech_rate}
    return body


def stream_elevenlabs(item, out):
    """Stream one sentence from ElevenLabs straight into the speaker as it arrives."""
    with tts.el.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voices.current.eleven}/stream?output_format=pcm_{OUT_SR}",
        headers={"xi-api-key": EL_KEY},
        json=_payload(item),
        stream=True,
        timeout=30,
    ) as r:
        r.raise_for_status()
        leftover = b""
        for chunk in r.iter_content(4096):
            if out.interrupted.is_set():
                break
            mark_first_audio()
            data = leftover + chunk
            n = len(data) // 2 * 2  # keep 16-bit samples aligned
            _play(out, data[:n])
            leftover = data[n:]


def speaker_worker():
    """Plays queue items: bytes are raw PCM at OUT_SR (beeps), str is text to speak.
    Audio goes through the engine so the echo canceller knows exactly what the speaker played."""
    out = rt.engine
    while True:
        item = rt.speak_q.get()
        try:
            if out.interrupted.is_set():
                continue  # you talked over it: drop the rest of the reply
            if item is LOOP:
                start_thinking()
                continue
            speaker_busy.set()
            stop_thinking()  # real audio takes over from the loading loop
            if isinstance(item, bytes):
                _play(out, item)
                continue
            if voices.provider() != "piper":
                try:
                    stream_elevenlabs(item, out)
                    continue
                except requests.HTTPError as e:
                    if not tts.elevenlabs_refused(e):
                        raise
                    voices.fall_back_to_piper(f"status {e.response.status_code}, probably out of credits")
            for pcm in tts.piper_pcm(item):
                if out.interrupted.is_set():
                    break
                mark_first_audio()
                _play(out, pcm)
        except Exception as e:
            log.error("TTS error: %s", e)
        finally:
            speaker_busy.clear()
            rt.speak_q.task_done()
