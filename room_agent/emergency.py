"""Emergency stop: everything Jarvis is doing stops, at once.

    - it stops talking (and a ringing alarm stops)
    - the current turn is cancelled: research, a page load, a click or typing sequence stop between steps
    - a request waiting for a yes is dropped
    - a shutdown / restart Jarvis scheduled is cancelled
    - actions still running are marked CANCELED in the journal
    - it's written to the audit log

Three ways in: say "stop everything" / "emergency stop" (a reflex: no model call), the global hotkey EMERGENCY_HOTKEY
(default Ctrl+Alt+J, works even when Jarvis isn't listening, e.g. in the middle of a long action), or the dashboard.
"""

import logging
import threading
import time

from room_agent import config
from room_agent import runtime as rt

log = logging.getLogger("room-agent")
stopped_at = [0.0]
stopping_turn = [None]  # (the turn that asked for the stop by voice: it isn't itself cancelled)


def stop_everything(via="voice"):
    """-> what was stopped (for the log / reply)."""
    done = []
    voice = via == "voice"  # (said as a new turn: the request that was running is an older one; this one replies)
    stopping_turn[0] = rt.turn if voice else None
    stopped_at[0] = time.time()  # (every request that started before now stops at its next step: cancel.requested)
    if not voice:
        try:
            rt.turn.cancel.set()
            done.append("the current request")
        except Exception:
            pass
    eng = rt.engine
    if eng is not None:
        try:
            eng.flush()
            if not voice:
                eng.interrupted.set()  # (by voice it was already listening: "Stopped." must still be heard)
            done.append("speaking")
        except Exception:
            pass
    try:
        while not rt.speak_q.empty():
            rt.speak_q.get_nowait()
            rt.speak_q.task_done()
    except Exception:
        pass
    try:
        from room_agent.tools.timers import acknowledge_ring

        if rt.ringing:
            acknowledge_ring()
            done.append("a ringing alarm")
    except Exception:
        pass
    try:
        from room_agent.actions import pending

        if rt.pending is not None:
            pending.cancel("emergency stop")
            rt.pending = None
            done.append("a request waiting for a yes")
    except Exception:
        pass
    try:
        from room_agent.tools import pcsettings

        if pcsettings.scheduled_power[0]:
            pcsettings.cancel_power()
            done.append("the scheduled shutdown")
    except Exception:
        pass
    try:
        from room_agent.actions import journal

        with journal._lock:
            running = [e for e in journal._entries.values() if e["state"] not in journal.FINAL]
        for e in running:
            journal.state(journal.CANCELED, "emergency stop", jid=e["id"])
        if running:
            done.append(f"{len(running)} running action{'s' if len(running) != 1 else ''}")
    except Exception:
        pass
    try:
        from room_agent.missions import engine

        paused = engine.pause_all("emergency stop")
        if paused:
            done.append(f"{paused} background mission{'s' if paused != 1 else ''} (paused, resumable)")
    except Exception:
        pass
    from room_agent import audit

    audit.event("emergency_stop", via=via, stopped=done)
    log.warning("EMERGENCY STOP (%s): stopped %s", via, ", ".join(done) or "nothing was running")
    return done


# ---------------------------------------------------------------- the global hotkey
MODS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}


def _parse(combo):
    keys = [k.strip().lower() for k in str(combo).split("+") if k.strip()]
    mods = sum(MODS[k] for k in keys if k in MODS)
    rest = [k for k in keys if k not in MODS]
    if len(rest) != 1:
        return None
    k = rest[0]
    vk = ord(k.upper()) if len(k) == 1 and k.isalnum() else {"pause": 0x13, "escape": 0x1B, "f12": 0x7B}.get(k)
    return (mods | 0x4000, vk) if vk else None  # (0x4000: MOD_NOREPEAT)


def start_hotkey():
    """Listen for EMERGENCY_HOTKEY anywhere in Windows (its own thread and message loop)."""
    import os

    combo = _parse(config.EMERGENCY_HOTKEY) if config.EMERGENCY_HOTKEY else None
    if os.name != "nt" or combo is None:
        return False

    def run():
        import ctypes
        from ctypes import wintypes as wt

        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 0x4A56, combo[0], combo[1]):
            log.warning("emergency hotkey %s couldn't be registered (another app uses it)", config.EMERGENCY_HOTKEY)
            return
        log.info("emergency stop: press %s anywhere", config.EMERGENCY_HOTKEY)
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                stop_everything("hotkey")

    threading.Thread(target=run, name="emergency-hotkey", daemon=True).start()
    return True
