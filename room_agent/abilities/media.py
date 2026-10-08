"""Volume and media playback (implementation: tools/media.py)."""

import os
import re

from room_agent import runtime as rt
from room_agent.abilities._kit import NO_ARGS, params, recently, tool
from room_agent.actions.core import Group, register_claim, register_group, register_line

IS_WINDOWS = os.name == "nt"
AMOUNT = {"type": "integer", "minimum": 1, "maximum": 100, "description": "Percentage points. Leave it out for a normal step "
          "(10). 'A little / a bit' = 5, 'a lot / way' = 25. Never ask how much."}

register_group(Group(
    "media", re.compile(r"volume|loud|quiet|soft|turn (it |that |this )?(up|down)|mute|sound|audio|pause|play|resume|stop the|"
                        r"music|song|track|skip|next|previous|go back|listening|spotify|youtube|percent|%", re.I),
    lambda: recently(rt.last_media_at), "volume and media playback",
    "PC volume (exact or up/down), mute, play/pause, next/previous track and what's playing, in whatever app is playing "
    "(Spotify, a browser...)", lambda: IS_WINDOWS))
register_line("choosing what to play (searching for a song, artist or playlist)",
              "not built; it can only control what's already playing", available=lambda: False)
register_claim("media", r"^(paused|resumed|skipped|muted|unmuted)\b|\b(i'?ve|i have|i)\s+(just\s+)?(paused|resumed|skipped|muted|unmuted)\b"
                        r"|\b(it'?s|that'?s|music'?s|sound'?s|song'?s|everything'?s|you'?re)\s+(now\s+)?(paused|muted|unmuted|resumed)\b"
                        r"|\b(turned|turning|cranked|bumped) (it |that |the volume |the music |the sound )?(up|down)\b"
                        r"|\bvolume'?s?\b.{0,20}\b(is|at|now|set|to)\b.{0,10}\d|\b(now playing|playing again)\b"
                        r"|\b(music|sound|song|it)('s| is) back on\b|\b(next|previous|last) (song|track)\b.{0,20}\b(on|up|playing)\b")


# ---------------------------------------------------------------- checks and undo
def _volume_state(args, before=None):
    from room_agent.tools import media

    ev = media._endpoint()
    return {"volume": media._level(ev), "muted": bool(ev.GetMute())}


VOLUME_OK = {"set_volume": lambda a, b, c: abs(c["volume"] - max(0, min(100, int(a["percent"])))) <= 1,
             "volume_up": lambda a, b, c: c["volume"] > b["volume"] or b["volume"] == 100,
             "volume_down": lambda a, b, c: c["volume"] < b["volume"] or b["volume"] == 0,
             "mute": lambda a, b, c: c["muted"],
             "unmute": lambda a, b, c: not c["muted"]}


def _put_volume(args, before, after):
    from room_agent.tools import media

    ok, now, _ = media._set_level(before["volume"], unmute=False)
    ev = media._endpoint()
    ev.SetMute(1 if before["muted"] else 0, None)
    if not ok or bool(ev.GetMute()) != before["muted"]:
        return f"FAILED: tried to put the volume back to {before['volume']}%, but it's at {now}%."
    media._done_media()
    return f"OK: the volume is back to {now}%" + (" (muted, as it was)." if before["muted"] else ".")


def _media_state(args, before=None):
    from room_agent.tools import media

    snap = media._winrt(media._snapshot)
    return None if snap is None else {k: snap[k] for k in ("title", "artist", "app", "status")}


def _put_playback(args, before, after):
    from room_agent.tools import media

    return media.play_pause("play" if before["status"] == media.PLAYING else "pause")


def _undo_next(args, before, after):
    from room_agent.tools import media

    return media.previous_track()


def _call(fn_name, convert=None):
    def run(args):
        from room_agent.tools import media

        fn = getattr(media, fn_name)
        return fn(*convert(args, media)) if convert else fn()
    return run


common = dict(group="media", claim="media", available=lambda: IS_WINDOWS)
def _say_level(result):
    """Spoken after a reflex volume change: the level actually read back."""
    level = (result.state_after or {}).get("volume")
    return None if level is None else f"Okay, it's at {level}."


volume = dict(observe=_volume_state, undo=_put_volume, undo_is_symmetric=True, **common)
level_said = dict(reflex_say=_say_level)
tool("set_volume", "Set the PC's volume to an exact level: 'volume 40%' = 40, 'half volume' = 50, 'max volume' = 100.",
     params({"percent": {"type": "integer", "minimum": 0, "maximum": 100}}, ["percent"]),
     _call("set_volume", lambda a, m: [int(a["percent"])]), event="volume.changed", verify=VOLUME_OK["set_volume"],
     expect=lambda a, b: {"volume": max(0, min(100, int(a["percent"]))), **({"muted": False} if int(a["percent"]) else {})},
     skip_if_satisfied=True, **level_said,
     reflex=[(r"(?:set\s+(?:the\s+)?)?volume\s+(?:to\s+|at\s+)?(?P<percent>\d{1,3})\s*(?:%|percent)?", {})], **volume)
tool("volume_up", "Turn the PC's volume up: 'louder', 'a little louder', 'turn it up'. Act right away.",
     params({"amount": AMOUNT}), _call("volume_up", lambda a, m: [int(a.get("amount") or m.STEP)]), event="volume.changed",
     verify=VOLUME_OK["volume_up"], **level_said,
     reflex=[(r"(?:turn\s+(?:it|the volume|the music|the sound|that)\s+up|volume\s+up|louder|make it louder)", {}),
             (r"(?:a\s+(?:little|bit|tad)\s+louder|turn\s+it\s+up\s+a\s+(?:little|bit))", {"amount": 5})], **volume)
tool("volume_down", "Turn the PC's volume down: 'quieter', 'turn it down', 'a bit lower'. Act right away.",
     params({"amount": AMOUNT}), _call("volume_down", lambda a, m: [int(a.get("amount") or m.STEP)]), event="volume.changed",
     verify=VOLUME_OK["volume_down"], **level_said,
     reflex=[(r"(?:turn\s+(?:it|the volume|the music|the sound|that)\s+down|volume\s+down|quieter|softer|make it quieter)", {}),
             (r"(?:a\s+(?:little|bit|tad)\s+(?:quieter|softer)|turn\s+it\s+down\s+a\s+(?:little|bit))", {"amount": 5})],
     **volume)
tool("mute", "Mute the PC's sound.", NO_ARGS, _call("mute"), event="volume.muted", verify=VOLUME_OK["mute"],
     expect=lambda a, b: {"muted": True},
     reflex=[(r"mute(?:\s+(?:it|the sound|the volume|everything|the pc|the music))?", {})], **volume)
tool("unmute", "Unmute the PC's sound.", NO_ARGS, _call("unmute"), event="volume.unmuted", verify=VOLUME_OK["unmute"],
     expect=lambda a, b: {"muted": False},
     reflex=[(r"unmute(?:\s+(?:it|the sound|the volume|everything|the pc|the music))?", {})], **volume)
tool("get_volume", "The PC's current volume and whether it's muted.", NO_ARGS, _call("get_volume"), changes_state=False,
     **common)
tool("play_pause", "Play or pause whatever is playing on the PC (Spotify, a browser, a video...). 'Pause the music' / 'stop "
     "the music' = pause; 'play', 'resume', 'keep playing' = play.",
     params({"action": {"type": "string", "enum": ["play", "pause", "toggle"]}}),
     _call("play_pause", lambda a, m: [a.get("action", "toggle")]), event="media.changed", observe=_media_state,
     undo=_put_playback, undo_is_symmetric=True,
     reflex=[(r"(?:pause|stop)\s+(?:the\s+|my\s+)?(?:music|song|track|playback|video)|pause(?:\s+it)?", {"action": "pause"}),
             (r"(?:resume|unpause|keep playing|play\s+(?:the\s+)?music|play it|resume (?:the\s+)?music)", {"action": "play"})],
     **common)
tool("next_track", "Skip to the next song or track: 'next song', 'skip this'.", NO_ARGS, _call("next_track"),
     event="media.changed", observe=_media_state, undo=_undo_next,
     reflex=[(r"(?:next|skip)(?:\s+(?:song|track|this|it|this song|this track))?", {})], **common)
tool("previous_track", "Go to the previous song or track: 'previous song', 'go back', 'play the last one again'. (Partway "
     "into a song, players restart it instead.)", NO_ARGS, _call("previous_track"), event="media.changed",
     observe=_media_state, reflex=[(r"(?:previous|last)\s+(?:song|track)|go back a (?:song|track)", {})], **common)
tool("get_current_media", "What's playing on the PC right now: song, artist, app, playing or paused.", NO_ARGS,
     _call("get_current_media"), changes_state=False, **common)
