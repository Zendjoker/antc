"""Volume and media control, offline: Windows' audio endpoint and media session are faked (including players that ignore a
command), and OpenAI is a fake. Checks nothing is reported done unless the new state was read back.

Run:  .venv\\Scripts\\python -m tests.test_media        (the live run on this PC is tests/media_live.py)
"""

import asyncio
import datetime
import json
import os
import sys
import tempfile
import threading
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from tests.harness import Checker, Conversation, setup_env  # noqa: E402
TMP = setup_env()

from room_agent import runtime as rt  # noqa: E402
from room_agent.llm import openai_backend  # noqa: E402
from room_agent.tools import apps, media  # noqa: E402
from room_agent.tools.registry import active_tools, run_tool  # noqa: E402

apps.apps = lambda refresh=False: {"entries": [{"name": "Spotify", "id": "SpotifyAB.Spotify_x!Spotify", "procs": []}], "exes": {}}


# ---------------------------------------------------------------- a pretend audio endpoint
class Endpoint:
    def __init__(self):
        self.level, self.muted, self.stuck = 0.75, 0, False

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, v, _):
        if not self.stuck:
            self.level = v

    def GetMute(self):
        return self.muted

    def SetMute(self, m, _):
        if not self.stuck:
            self.muted = m


EP = Endpoint()
media._endpoint = lambda: EP

# ---------------------------------------------------------------- a pretend media session
TRACKS = [("Keep On Running", "Mizmo"), ("Blinding Lights", "The Weeknd"), ("Levitating", "Dua Lipa")]


class Session:
    source_app_user_model_id = "SpotifyAB.Spotify_x!Spotify"

    def __init__(self):
        self.i, self.status, self.pos, self.deaf, self.refuse = 0, media.PAUSED, 47.0, False, False
        self.stamp = 0

    async def try_get_media_properties_async(self):
        return NS(title=TRACKS[self.i][0], artist=TRACKS[self.i][1])

    def get_playback_info(self):
        return NS(playback_status=self.status)

    def get_timeline_properties(self):
        return NS(position=datetime.timedelta(seconds=self.pos), last_updated_time=self.stamp)

    async def _do(self, fn):
        if self.refuse:
            return False
        if not self.deaf:
            fn()
            self.stamp += 1
        return True

    def try_play_async(self):
        return self._do(lambda: setattr(self, "status", media.PLAYING))

    def try_pause_async(self):
        return self._do(lambda: setattr(self, "status", media.PAUSED))

    def try_skip_next_async(self):
        return self._do(lambda: (setattr(self, "i", (self.i + 1) % len(TRACKS)), setattr(self, "pos", 0.0)))

    def try_skip_previous_async(self):
        def back():
            if self.pos >= 3:
                self.pos = 0.0  # partway in: restart the song
            else:
                self.i, self.pos = (self.i - 1) % len(TRACKS), 0.0
        return self._do(back)


SESSION = {"s": Session()}


async def fake_session():
    return SESSION["s"]


media._session = fake_session

# ---------------------------------------------------------------- fake OpenAI



from tests.harness import Checker, Conversation  # noqa: E402

convo = Conversation(engine=None)
fake, REQUESTS, SPOKEN = convo.model, convo.requests, convo.spoken
from room_agent.conversation.turn import take_turn  # noqa: E402

t = Checker()
check, FAILS = t.check, t.fails


def tool(name, **args):
    return run_tool(name, args)


print("volume (state read back after every change):")
check("get_volume", tool("get_volume") == "OK: the volume is at 75%.", tool("get_volume"))
r = tool("set_volume", percent=40)
check("set_volume 40 -> 40%", r == "OK: the volume is now 40%." and round(EP.level * 100) == 40, r)
r = tool("volume_up")
check("volume_up with no amount -> +10 (no question asked)", r == "OK: the volume went from 40% to 50%.", r)
r = tool("volume_up", amount=5)
check("'a little louder' (5) -> 55%", r == "OK: the volume went from 50% to 55%.", r)
r = tool("volume_down", amount=25)
check("'turn it way down' (25) -> 30%", r == "OK: the volume went from 55% to 30%.", r)
EP.level = 0.97
r = tool("volume_up", amount=10)
check("louder near the top stops at 100%", r == "OK: the volume went from 97% to 100%.", r)
r = tool("volume_up")
check("louder at 100% says it's the maximum", r == "OK: the volume is already at 100%, the maximum.", r)
r = tool("mute")
check("mute -> muted", r == "OK: the sound is muted." and EP.muted, r)
r = tool("mute")
check("mute again -> already muted", r.startswith("OK: the sound was already muted"), r)
r = tool("set_volume", percent=40)
check("'volume 40' while muted also unmutes", r == "OK: the volume is now 40%." and not EP.muted, r)
EP.muted = 1
r = tool("unmute")
check("unmute -> back on", r == "OK: the sound is back on, at 40%." and not EP.muted, r)
EP.stuck = True
r = tool("set_volume", percent=80)
check("a volume change that doesn't stick -> FAILED", r.startswith("FAILED") and "40%" in r, r)
r = tool("mute")
check("a mute that doesn't stick -> FAILED", r.startswith("FAILED"), r)
EP.stuck = False
r = tool("set_volume", percent=150)
check("set_volume out of range is refused by the schema", r.startswith("FAILED") and "between 0 and 100" in r, r)

print("media (playback state / track read back after every action):")
s = SESSION["s"]
r = tool("get_current_media")
check("what am I listening to", r == "OK: Keep On Running by Mizmo (Spotify), paused.", r)
r = tool("play_pause", action="play")
check("'keep playing' -> playing", r == "OK: playing: Keep On Running by Mizmo (Spotify)." and s.status == media.PLAYING, r)
r = tool("play_pause", action="play")
check("play when already playing -> says so", r.startswith("OK: Keep On Running by Mizmo (Spotify) was already playing"), r)
r = tool("play_pause", action="pause")
check("'pause the music' -> paused", r.startswith("OK: paused") and s.status == media.PAUSED, r)
r = tool("play_pause")
check("play_pause toggle -> playing", r.startswith("OK: playing") and s.status == media.PLAYING, r)
r = tool("next_track")
check("next song -> new track confirmed", r == "OK: now playing Blinding Lights by The Weeknd (Spotify).", r)
r = tool("previous_track")
check("go back (at the start of a song) -> previous track", r == "OK: now playing Keep On Running by Mizmo (Spotify).", r)
s.pos = 60.0
r = tool("previous_track")
check("go back (partway in) -> restarted, said honestly", r == "OK: restarted Keep On Running by Mizmo (Spotify) from the beginning.", r)
s.deaf = True
r = tool("next_track")
check("player accepts a skip but the track never changes -> FAILED", r.startswith("FAILED") and "couldn't confirm" in r, r)
r = tool("play_pause", action="pause")
check("player ignores pause -> FAILED, still playing", r.startswith("FAILED") and "still playing" in r, r)
s.deaf, s.refuse = False, True
r = tool("next_track")
check("player refuses to skip -> FAILED", r.startswith("FAILED") and "didn't accept" in r, r)
s.refuse = False
SESSION["s"] = None
for t in ("play_pause", "next_track", "previous_track"):
    r = tool(t)
    check(f"{t} with nothing playing -> FAILED, nothing done", r.startswith("FAILED: nothing is playing"), r)
check("get_current_media with nothing playing", tool("get_current_media").startswith("OK: nothing is playing"))
SESSION["s"] = s

print("natural phrasings get the media tools:")
MEDIA = {"set_volume", "volume_up", "volume_down", "mute", "unmute", "play_pause", "next_track", "previous_track",
         "get_volume", "get_current_media"}
for said in ["Turn it down", "Volume 40%", "A little louder", "Mute", "Pause the music", "Keep playing", "Next song",
             "Go back", "What am I listening to?"]:
    offered = {t["name"] for t in openai_backend.relevant_tools(active_tools(), [{"role": "user", "content": said}])}
    check(f"{said!r}", MEDIA <= offered, MEDIA - offered)
rt.last_media_at = 0
offered = {t["name"] for t in openai_backend.relevant_tools(active_tools(), [{"role": "user", "content": "how's it going?"}])}
check("plain chat doesn't pay for the media tools", not (MEDIA & offered))

print("through the conversation (fake model):")


def turn(said, scripts):
    REQUESTS.clear()
    SPOKEN.clear()
    fake.scripts = list(scripts)
    rt.turn_text = said
    history = []
    take_turn(history, said, final=True)
    return " ".join(SPOKEN)


EP.level, s.deaf = 0.5, False
said = turn("a little louder", [{"tool": ("volume_up", {"amount": 5})}, {"text": "Bumped it up to 55."}])
check("'a little louder' -> volume_up runs, confirmation spoken after", round(EP.level * 100) == 55 and "55" in said, said)
said = turn("turn it down", [{"text": "Turned it down."}, {"tool": ("volume_down", {})}, {"text": "Down to 45."}])
check("'Turned it down' said before the tool ran is held back", "Turned it down." not in said and round(EP.level * 100) == 45, said)
s.deaf = True
said = turn("next song", [{"tool": ("next_track", {})}, {"text": "Skipped to the next one."},
                          {"text": "Hmm, Spotify didn't change tracks."}])
check("a skip that didn't happen is never claimed", "Skipped" not in said, said)
s.deaf = False

print("\nALL MEDIA TESTS PASSED" if not FAILS else f"\nFAILED: {FAILS}")
sys.stdout.flush()
os._exit(1 if FAILS else 0)
