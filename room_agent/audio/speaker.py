"""Playback: the speaker thread that turns queued text and sounds into audio, and the helpers that feed it."""

import logging
import re
import time

import requests

from room_agent import runtime as rt
from room_agent.audio import tts, voices
from room_agent.audio.fillers import LOOP, speaker_busy, start_thinking, stop_thinking
from room_agent.audio.sounds import load_sound, tone
from room_agent import speech
from room_agent.audio.styles import Spoken
from room_agent.social.delivery import VoiceDelivery
from room_agent.speech import elevenlabs, normalize
from room_agent.config import EL_FALLBACK_MODEL, EL_KEY, EL_MODEL, EL_TEXT_NORMALIZATION, EL_SEED, OUT_SR, WAKE_SOUND
from room_agent.speech import timing
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
    timing.mark("first_text")
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
        # One steady voice: their chosen voice, pace and style (speech.perform), never a mood guess or the model's own
        # tag ("low mood -> soft x0.95" used to override the style they had just asked for)
        item.style = "" if voices.current.style == "normal" else voices.current.style
        item.delivery = None
        item.performance = speech.perform(sentence)  # (HOW to say it; the words stay `sentence`: speech/)
        rt.speak_q.put(item)


def mark_first_audio(out=None):
    timing.mark("tts_first_byte")
    timing.mark("first_audio")
    timing.mark("first_audible", time.time() + (out.queued_seconds() if out is not None else 0.0))  # (heard after what's queued)
    if rt.turn_start is not None:
        log.info("answer started %.2fs after you stopped talking", time.time() - rt.turn_start)
        rt.turn.timing.setdefault("first_sound", time.time() - rt.turn_start)
        rt.turn_start = None
        rt.state.go(State.SPEAKING)


def _play(out, pcm):
    out.play(pcm)
    rt.tts_end = time.time() + out.queued_seconds()


class InvalidAudio(Exception):
    pass


_model = {"name": None}  # the ElevenLabs model in use (EL_MODEL, or EL_FALLBACK_MODEL after it was refused)
_unsupported = set()  # models that refused the speech profile's voice settings: they get only the speed from now on


def _profile(p, model):
    """The tested voice settings (speech_profiles in settings.json) for this sentence, unless this model refused them."""
    profiles = voices.saved("speech_profiles", {}) or {}
    profile = profiles.get(p.purpose) or profiles.get("default")
    if profile and model in _unsupported:
        return {k: v for k, v in profile.items() if k == "speed"}
    return profile


def _payload(item, model):
    text, p = speech.provider_text(item, "elevenlabs", model)
    previous = " ".join(list(rt.turn_speech)[:-1])[-500:]  # (this reply so far, for prosody continuity)
    return elevenlabs.request_body(text, p, model, rt.speech_rate, previous_text=previous, language=p.language,
                                   normalization=EL_TEXT_NORMALIZATION, seed=EL_SEED or None, profile=_profile(p, model))


def stream_elevenlabs(item, out, model=None):
    """Stream one sentence from ElevenLabs straight into the speaker as it arrives. -> bytes played."""
    model = model or _model["name"] or EL_MODEL
    played = 0
    with tts.el.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voices.current.eleven}/stream?output_format=pcm_{OUT_SR}",
        headers={"xi-api-key": EL_KEY},
        json=_payload(item, model),
        stream=True,
        timeout=(5, 15),
    ) as r:
        r.raise_for_status()
        leftover = b""
        for chunk in r.iter_content(4096):
            if out.interrupted.is_set():
                break
            mark_first_audio(out)
            data = leftover + chunk
            n = len(data) // 2 * 2  # keep 16-bit samples aligned
            _play(out, data[:n])
            played += n
            leftover = data[n:]
    if not played and not out.interrupted.is_set():
        raise InvalidAudio("no audio came back")
    return played


def _speak_elevenlabs(item, out):
    """-> True if ElevenLabs spoke it (or it was interrupted); False: say it with the local voice instead. Any
    ElevenLabs problem costs at most expressiveness, never the sentence."""
    try:
        stream_elevenlabs(item, out)
        return True
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        if tts.elevenlabs_refused(e):
            voices.fall_back_to_piper(f"status {code}, probably out of credits")
            return False
        model = _model["name"] or EL_MODEL
        if code in (400, 422) and model not in _unsupported and voices.saved("speech_profiles"):
            # (maybe only the voice settings were refused, e.g. a stability this model doesn't take: keep the model,
            # drop the settings, before giving up the model itself)
            _unsupported.add(model)
            try:
                stream_elevenlabs(item, out)
                log.warning("ElevenLabs refused the speech profile's voice settings for %s (status %d): using the "
                            "voice's own settings with this model", model, code)
                return True
            except requests.HTTPError as e2:
                _unsupported.discard(model)  # (not the settings: the model itself is refused)
                code = e2.response.status_code if e2.response is not None else code
            except Exception as e2:  # noqa: BLE001
                _unsupported.discard(model)
                log.warning("ElevenLabs unavailable (%s): local voice for this sentence", e2.__class__.__name__)
                return False
        if code in (400, 404, 422) and EL_FALLBACK_MODEL and model != EL_FALLBACK_MODEL:
            log.warning("ElevenLabs refused %s (status %d): switching to %s", model, code, EL_FALLBACK_MODEL)
            _model["name"] = EL_FALLBACK_MODEL
            try:
                stream_elevenlabs(item, out)
                return True
            except Exception as e2:
                log.warning("ElevenLabs fallback model failed too (%s): local voice for this sentence", e2.__class__.__name__)
                return False
        log.warning("ElevenLabs error (status %d): local voice for this sentence", code)
    except (requests.Timeout, requests.ConnectionError, InvalidAudio) as e:
        log.warning("ElevenLabs unavailable (%s): local voice for this sentence", e.__class__.__name__)
    return False


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
            timing.mark("tts_start")
            if voices.provider() != "piper" and _speak_elevenlabs(item, out):
                continue
            text, p = speech.provider_text(item, "piper", None)
            for part in normalize.clauses(text):  # (a long sentence starts sounding after its first clause)
                spoken = Spoken(part)
                spoken.delivery = VoiceDelivery(pace=p.pace * rt.speech_rate, energy=p.energy)  # (their pace x this moment's)
                for pcm in tts.piper_pcm(spoken):
                    if out.interrupted.is_set():
                        break
                    mark_first_audio(out)
                    _play(out, pcm)
                if out.interrupted.is_set():
                    break
        except Exception as e:
            log.error("TTS error: %s", e)
        finally:
            if isinstance(item, str) and item is not LOOP:
                timing.mark("generation_done", overwrite=True)
            speaker_busy.clear()
            rt.speak_q.task_done()
