"""Choosing music, offline: a simulated Spotify window (library playlists, search results) and Windows' now-playing
info. Nothing is played for real; no network.

Run:  .venv\\Scripts\\python -m tests.test_music
"""

from tests.harness import setup_env

setup_env()

from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor  # noqa: E402
from room_agent.computer.uia import El  # noqa: E402
from room_agent.tools import music  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

t = Checker()
core.ensure_loaded()
music.WAIT_S = 0.5


class FakeSpotify:
    def __init__(self):
        self.running, self.searched, self.now, self.dead = True, None, ("Old song", "Someone", False), False
        self.library = ["SOUL", "Country", "Special GYM", "Morning", "Techno Zend"]
        self.catalog = {"drake": [("Drake", "Drake", "Artist"), ("God's Plan", "Drake", "Song")],
                        "lo-fi": [("lofi hip hop radio", "Lofi Girl", "Playlist")]}

    def window(self):
        return 42 if self.running else None

    def buttons(self, hwnd):
        names = list(self.library)
        if self.searched is not None:
            names += [f"{title}" for title, _, _ in self.catalog.get(self.searched, [])]
        return [(n, El(name="Play " + n, kind="Button", raw={"plays": n})) for n in names]

    def open_uri(self, uri):
        if uri == "spotify:":
            self.running = True
        elif uri.startswith("spotify:search:"):
            import urllib.parse

            self.searched = urllib.parse.unquote(uri.split(":", 2)[2]).lower()

    def invoke(self, el):
        if self.dead:
            return "Invoke"  # (pressed, but nothing happens)
        what = el.raw["plays"]
        hit = next((x for v in self.catalog.values() for x in v if x[0] == what), None)
        self.now = (hit[0], hit[1], True) if hit else (f"First song of {what}", "Various", True)
        return "Invoke"


sp = FakeSpotify()
music._spotify_window, music._buttons, music._open_uri = sp.window, sp.buttons, sp.open_uri
music._now = lambda: sp.now
music.uia.invoke = sp.invoke
convo = Conversation()


def run(name, args, said):
    rt.new_turn(said)
    return executor.execute(name, args)


print("Spotify:")
convo.say("play my gym playlist", reply="SHOULD NOT BE NEEDED")
t.check("'play my gym playlist' -> your 'Special GYM' playlist, instantly (no model call), confirmed by now-playing",
        not convo.requests and sp.now[2] and sp.now[0] == "First song of Special GYM"
        and convo.said() == ["Playing Special GYM."], (convo.said(), sp.now))
r = run("play_music", {"query": "Drake"}, "play Drake")
t.check("'play Drake' -> Spotify search, the top result plays, checked", r.success and sp.searched == "drake"
        and sp.now[0] == "Drake" and "Drake" in r.message, r.message)
r = run("play_music", {"query": "lo-fi"}, "play some lo-fi")
t.check("'play some lo-fi' -> a lo-fi result from the search", r.success and "lofi" in r.message.lower(), r.message)
sp.dead = True
before = sp.now
r = run("play_music", {"query": "SOUL", "kind": "playlist"}, "play my soul playlist")
t.check("pressed Play but nothing new started -> 'not confirmed', never 'playing'", not r.success and "not confirmed" in r.message,
        r.message)
sp.dead = False
sp.catalog["nothingness"] = []
r = run("play_music", {"query": "nothingness"}, "play nothingness")
t.check("a search with no results -> says so, nothing played", not r.success and "no results" in r.message, r.message)
sp.running = False
launched = []
orig_open = sp.open_uri
sp.open_uri = lambda uri: (launched.append(uri), orig_open(uri))
music._open_uri = sp.open_uri
r = run("play_music", {"query": "Morning", "kind": "playlist"}, "play my morning playlist")
t.check("Spotify closed -> started first, then plays", r.success and launched[0] == "spotify:", (r.message, launched))

print("YouTube and choosing the service:")
calls = []
music.youtube = lambda q, browser="": (calls.append(q), f"OK: playing on YouTube: \"{q} video\"")[1]
r = run("play_music", {"query": "interstellar soundtrack", "service": "youtube"}, "play the interstellar soundtrack on youtube")
t.check("'... on YouTube' -> YouTube, not Spotify", r.success and calls == ["interstellar soundtrack"], r.message)
music._spotify_window = lambda: None
from room_agent.tools import apps  # noqa: E402

apps.find = lambda q, cache=None: (None, [])
r = run("play_music", {"query": "jazz"}, "play some jazz")
t.check("no Spotify on the PC -> YouTube instead", r.success and calls[-1] == "jazz", r.message)
r = run("play_music", {"query": "jazz", "service": "spotify"}, "play jazz on spotify")
t.check("asked for Spotify but it isn't installed -> says so (no silent switch)", not r.success and "isn't installed" in r.message)
r = run("play_music", {"query": "jazz", "service": "tidal"}, "play jazz on tidal")
t.check("a service it can't use -> says so", not r.success)
t.done("MUSIC")
