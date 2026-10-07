"""Windows volume and media control.

Volume and mute go through Windows Core Audio (the default output device's master volume, read back after every change).
Play / pause / next / previous / "what's playing" go through Windows' media session API (the same one behind the media
card in the volume flyout), so it works with whatever app is playing: Spotify, a browser, a video player...
Every action is checked afterwards (the volume really is 40%, the playback status really is paused, the track really
changed) and only then reported as done. Note: this is the PC's master volume, so Jarvis's own voice changes with it."""

import asyncio
import logging
import os
import threading
import time

from room_agent import runtime as rt

log = logging.getLogger("room-agent")
IS_WINDOWS = os.name == "nt"
STEP = 10  # "turn it up / down" when they don't say how much
PLAYING, PAUSED = 4, 5  # GlobalSystemMediaTransportControlsSessionPlaybackStatus
STATUS = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}


# ---------------------------------------------------------------- volume (Core Audio)
def _endpoint():
    import comtypes
    from pycaw.pycaw import AudioUtilities

    try:
        comtypes.CoInitialize()  # (each thread that talks to COM needs this; a second call is harmless)
    except OSError:
        pass
    return AudioUtilities.GetSpeakers().EndpointVolume


def _level(ev):
    return round(ev.GetMasterVolumeLevelScalar() * 100)


def _done_media():
    rt.last_media_at = time.time()


def _set_level(percent, unmute=True):
    """Set and read back. -> (ok, level now, muted now)."""
    ev = _endpoint()
    percent = max(0, min(100, int(round(percent))))
    ev.SetMasterVolumeLevelScalar(percent / 100, None)
    if unmute and percent > 0 and ev.GetMute():
        ev.SetMute(0, None)  # "volume 40" while muted means they want to hear it
    for _ in range(10):
        now = _level(ev)
        if abs(now - percent) <= 1:
            return True, now, bool(ev.GetMute())
        time.sleep(0.05)
    return False, _level(ev), bool(ev.GetMute())


def get_volume():
    if not IS_WINDOWS:
        return "UNAVAILABLE: volume control only works on Windows."
    ev = _endpoint()
    return f"OK: the volume is at {_level(ev)}%" + (", and it's muted." if ev.GetMute() else ".")


def set_volume(percent):
    if not IS_WINDOWS:
        return "UNAVAILABLE: volume control only works on Windows."
    ok, now, muted = _set_level(percent)
    _done_media()
    if not ok:
        return f"FAILED: tried to set the volume to {percent}%, but it's at {now}%."
    return f"OK: the volume is now {now}%" + (" (still muted)." if muted else ".")


def _nudge(amount, sign):
    if not IS_WINDOWS:
        return "UNAVAILABLE: volume control only works on Windows."
    ev = _endpoint()
    before = _level(ev)
    amount = max(1, min(100, int(round(amount or STEP))))
    target = max(0, min(100, before + sign * amount))
    if target == before:
        return f"OK: the volume is already at {before}%, the {'maximum' if sign > 0 else 'minimum'}."
    ok, now, muted = _set_level(target)
    _done_media()
    if not ok:
        return f"FAILED: tried to change the volume, but it's still at {now}%."
    return f"OK: the volume went from {before}% to {now}%" + (" (still muted)." if muted else ".")


def volume_up(amount=STEP):
    return _nudge(amount, +1)


def volume_down(amount=STEP):
    return _nudge(amount, -1)


def _set_mute(on):
    if not IS_WINDOWS:
        return "UNAVAILABLE: volume control only works on Windows."
    ev = _endpoint()
    if bool(ev.GetMute()) == on:
        return f"OK: the sound was already {'muted' if on else 'on'}" + ("." if on else f", at {_level(ev)}%.")
    ev.SetMute(1 if on else 0, None)
    for _ in range(10):
        if bool(ev.GetMute()) == on:
            _done_media()
            return "OK: the sound is muted." if on else f"OK: the sound is back on, at {_level(ev)}%."
        time.sleep(0.05)
    return f"FAILED: tried to {'mute' if on else 'unmute'}, but it didn't change."


def mute():
    return _set_mute(True)


def unmute():
    return _set_mute(False)


# ---------------------------------------------------------------- media (Windows media sessions)
def _winrt(make):
    """Run a WinRT coroutine to completion on its own thread (so it never meets another event loop)."""
    out = {}

    def run():
        try:
            out["value"] = asyncio.run(make())
        except Exception as e:  # noqa: BLE001 (reported to the caller)
            out["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(15)
    if "error" in out:
        raise out["error"]
    return out.get("value")


async def _session():
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

    manager = await Manager.request_async()
    current = manager.get_current_session()
    if current is None:  # none marked current: take one that's playing, else any
        sessions = list(manager.get_sessions())
        current = next((s for s in sessions if s.get_playback_info().playback_status == PLAYING), None) or (
            sessions[0] if sessions else None)
    return current


async def _snapshot(session=None):
    s = session or await _session()
    if s is None:
        return None
    props = await s.try_get_media_properties_async()
    info, timeline = s.get_playback_info(), s.get_timeline_properties()
    return {"session": s, "app": _app_name(s.source_app_user_model_id), "title": props.title or "",
            "artist": props.artist or "", "status": info.playback_status,
            "position": timeline.position.total_seconds() if timeline.position else 0.0,
            "updated": timeline.last_updated_time}


def _app_name(aumid):
    """Spotify's id "SpotifyAB.SpotifyMusic_zpd...!Spotify" -> "Spotify" (the installed app's name when known)."""
    try:
        from room_agent.tools.apps import apps

        for e in apps()["entries"]:
            if e.get("id") and e["id"].lower() == (aumid or "").lower():
                return e["name"]
    except Exception:
        pass
    name = (aumid or "").split("!")[-1]
    name = name.split(".")[1] if "!" not in (aumid or "") and name.count(".") >= 1 else name
    return name.removesuffix(".exe") or "an app"


def _track(snap):
    if not snap["title"]:
        return f"something in {snap['app']}"
    return f"{snap['title']}" + (f" by {snap['artist']}" if snap["artist"] else "") + f" ({snap['app']})"


NOTHING = "FAILED: nothing is playing in an app Windows can control right now (no media session), so nothing was done."


def get_current_media():
    if not IS_WINDOWS:
        return "UNAVAILABLE: media control only works on Windows."
    snap = _winrt(_snapshot)
    if snap is None:
        return "OK: nothing is playing right now (no app has a media session)."
    return f"OK: {_track(snap)}, {STATUS.get(snap['status'], 'unknown')}."


def play_pause(action="toggle"):
    """action: play, pause or toggle. Done only when the playback status really is what was asked for."""
    if not IS_WINDOWS:
        return "UNAVAILABLE: media control only works on Windows."
    action = (action or "toggle").lower()

    async def run():
        snap = await _snapshot()
        if snap is None:
            return NOTHING
        s, before = snap["session"], snap["status"]
        want = {"play": PLAYING, "pause": PAUSED}.get(action) or (PAUSED if before == PLAYING else PLAYING)
        if before == want:
            return f"OK: {_track(snap)} was already {STATUS[want]}."
        accepted = await (s.try_play_async() if want == PLAYING else s.try_pause_async())
        for _ in range(25):
            if s.get_playback_info().playback_status == want:
                _done_media()
                return f"OK: {STATUS[want]}: {_track(snap)}."
            await asyncio.sleep(0.1)
        return (f"FAILED: asked {snap['app']} to {'play' if want == PLAYING else 'pause'}"
                + ("" if accepted else " and it refused") + f", but it's still {STATUS.get(s.get_playback_info().playback_status, 'unchanged')}.")

    return _winrt(run)


def _skip(forward):
    if not IS_WINDOWS:
        return "UNAVAILABLE: media control only works on Windows."

    async def run():
        snap = await _snapshot()
        if snap is None:
            return NOTHING
        s = snap["session"]
        accepted = await (s.try_skip_next_async() if forward else s.try_skip_previous_async())
        if not accepted:
            return f"FAILED: {snap['app']} didn't accept {'skipping' if forward else 'going back'} right now."
        for _ in range(40):  # up to 4 s for the player to report the new track
            await asyncio.sleep(0.1)
            now = await _snapshot(s)
            if (now["title"], now["artist"]) != (snap["title"], snap["artist"]):
                _done_media()
                return f"OK: now playing {_track(now)}."
            if not forward and now["updated"] != snap["updated"] and now["position"] < 5 <= snap["position"]:
                _done_media()  # "previous" partway into a song restarts it (that's what players do)
                return f"OK: restarted {_track(now)} from the beginning."
        return (f"FAILED: asked {snap['app']} to {'skip' if forward else 'go back'}, but I couldn't confirm the track "
                f"changed (still {_track(snap)}).")

    return _winrt(run)


def next_track():
    return _skip(True)


def previous_track():
    return _skip(False)
