"""One answer to "should the current request stop now?", used by every step that takes time (page loads, research, file
searches, waiting for music or a window, the model loop between rounds).

It should stop when:
    - the turn was cancelled (a phone caller hung up, the reply was cut off)
    - they talked over Jarvis (the audio engine's interruption flag; not on a phone call)
    - an emergency stop happened after this request started (hotkey / dashboard / voice), even if a newer turn exists:
      the stop compares times, so it reaches the action that was already running
"""

from room_agent import runtime as rt


def requested():
    t = rt.turn
    if t.cancel.is_set():
        return True
    eng = rt.engine
    if eng is not None and not t.output and getattr(eng, "interrupted", None) is not None and eng.interrupted.is_set():
        return True
    from room_agent import emergency

    return emergency.stopped_at[0] > t.started and t is not emergency.stopping_turn[0]
