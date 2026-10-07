"""Filling silence while an answer is slow: short "mm"/"one sec" clips and the looping loading sound."""

import logging
import threading
import time

from room_agent import runtime as rt
from room_agent.audio.sounds import load_sound
from room_agent.audio.tts import clip
from room_agent.config import FILLER_DELAY, FILLERS, LOADING_DELAY, LOADING_SOUND, LOADING_VOLUME, OUT_SR
from room_agent.conversation.states import State

log = logging.getLogger("room-agent")

LOOP = "<thinking loop>"  # speak_q marker: start the loading loop after whatever is queued before it
speaker_busy = threading.Event()  # the speaker worker is playing real audio
_loop_pcm = None
_loop_on = threading.Event()
_loop_lock = threading.Lock()


def filler(kind):
    """Play a short filler ("ack" or "wait"), avoiding recently used ones."""
    if not (FILLERS and rt.tts_enabled) or rt.state.quiet or rt.turn.output:
        return
    try:
        text = rt.phrases.pick(kind)
        rt.recent_speech.append(text)
        rt.speak_q.put(clip(text))
    except Exception as e:
        log.warning("filler failed: %s", e)


def start_thinking():
    if _loop_pcm and rt.engine and not rt.engine.interrupted.is_set():
        _loop_on.set()


def stop_thinking():
    """Cut the loop off right away (only loop audio is ever queued while it's on)."""
    with _loop_lock:
        if _loop_on.is_set():
            _loop_on.clear()
            rt.engine.flush()


def thinking_loop_worker():
    """Keeps ~0.2 s of the loading sound queued while thinking, so it stops almost instantly."""
    global _loop_pcm
    try:
        _loop_pcm = load_sound(LOADING_SOUND, gain=LOADING_VOLUME) if LOADING_SOUND else None
    except Exception as e:
        log.warning("couldn't load LOADING_SOUND %s (%s)", LOADING_SOUND, e)
    if not _loop_pcm:
        return
    engine = rt.engine
    pos, chunk = 0, OUT_SR // 10 * 2  # 100 ms
    while True:
        _loop_on.wait()
        with _loop_lock:
            if engine.interrupted.is_set():
                _loop_on.clear()
            elif _loop_on.is_set() and not speaker_busy.is_set() and engine.queued_seconds() < 0.2:
                end = pos + chunk
                if end <= len(_loop_pcm):
                    piece, pos = _loop_pcm[pos:end], end % len(_loop_pcm)
                else:  # wrap around seamlessly
                    piece, pos = _loop_pcm[pos:] + _loop_pcm[: end - len(_loop_pcm)], end - len(_loop_pcm)
                engine.play(piece)
        time.sleep(0.03)


def filler_if_slow(turn_start):
    """Fill a slow answer: the loading loop if there is one, otherwise a short "mm" (like a person)."""

    def check():
        if (rt.turn_start == turn_start and rt.state.state is State.PROCESSING and rt.speak_q.empty()
                and not speaker_busy.is_set()):
            if _loop_pcm:
                start_thinking()
            else:
                filler("ack")

    t = threading.Timer(LOADING_DELAY if _loop_pcm else FILLER_DELAY, check)
    t.daemon = True
    t.start()
