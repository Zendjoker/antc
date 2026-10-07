"""The claim check (truth.ClaimGuard) against a labelled set of sentences: what must be held back (an unconfirmed claim)
and what must be spoken (ordinary talk, honest failures, confirmed claims).

Run:  .venv\\Scripts\\python -m tests.test_claims
"""

from tests.harness import setup_env

TMP = setup_env()

from room_agent.actions import core  # noqa: E402
from room_agent.truth import ClaimGuard  # noqa: E402
from tests.harness import Checker  # noqa: E402

core.ensure_loaded()  # (each area registers its claims)
t = Checker()


def guard(*results):
    """A guard that has seen these (tool, result) pairs this turn."""
    g = ClaimGuard(memory_count=lambda: 3, home_known=lambda: True)
    for name, out in results:
        g.tool_result(name, out)
    return g


HELD = [  # (sentence, tool results this turn, the claim kind that must be caught)
    ("Timer's set for ten minutes.", [], "timer"),
    ("Your alarm is set for 7.", [], "timer"),
    ("Saved it.", [], "memory_save"),
    ("I've saved that for you.", [], "memory_save"),
    ("Not a problem, I've saved it.", [], "memory_save"),            # (a "not" in another clause isn't a negation)
    ("No worries, I'll remember that.", [], "memory_save"),
    ("Done!", [], "done"),
    ("All set.", [], "done"),
    ("Done!", [("set_volume", "OK: the volume is now 30%."), ("open_app", "FAILED: couldn't find FakeApp.")], "done"),
    ("Timer's set, want another?", [], "timer"),                     # (the claim before the question still counts)
    ("I turned the lights off.", [], "home"),
    ("The heater is now on.", [], "home"),
    ("Opened Spotify for you.", [], "app"),
    ("I've closed Discord.", [], "app"),
    ("Spotify is now on monitor 2.", [], "app"),
    ("It's maximized.", [], "app"),
    ("Switched you over to Chrome.", [], "app"),
    ("Muted.", [], "media"),
    ("Paused it.", [], "media"),
    ("Turned it down.", [], "media"),
    ("Volume's at 40 now.", [], "media"),
    ("Now playing Blinding Lights.", [], "media"),
    ("Sent it!", [], "message"),
    ("I've emailed him.", [], "message"),
    ("It's been sent.", [], "message"),
    ("Added dinner to your calendar.", [], "calendar"),
    ("I'll open Spotify.", [], "future_action"),
    ("I can see your calendar.", [], "access"),
    ("A timer needs at least a minute.", [], "timer_limit"),
    ("I switched to a new voice.", [], "voice"),
    ("I searched online and found it.", [], "web"),
    ("I've forgotten that.", [], "memory_forget"),
    ("Cancelled the pasta timer.", [("cancel_timer", "OK: nothing set matched 'pasta', nothing cancelled.")], "timer"),
    ("Turned it up.", [("volume_up", "OK: the volume is already at 100%, the maximum.")], "media"),
    ("I don't have anything saved about you yet.", [], "memory_empty"),
    ("I don't know where you live.", [], "no_location"),
]
SPOKEN = [  # (sentence, tool results this turn): must NOT be held back
    ("The store opened in 1990.", []),
    ("I moved to Chicago last year, you said?", []),
    ("You brought up a good point.", []),
    ("Put on a jacket, it's cold out.", []),
    ("The Lakers are playing tonight.", []),
    ("He skipped practice.", []),
    ("The game was paused for rain.", []),
    ("That song is great.", []),
    ("Want me to set a timer?", []),
    ("Should I save that?", []),
    ("I couldn't turn it off, sorry.", []),
    ("I wasn't able to save that.", []),
    ("I can't open that one.", []),
    ("All set: Spotify's maximized on monitor 2.", [("open_app", "OK: Spotify is open."),
                                                    ("move_window_to_monitor", "OK: Spotify is on monitor 2 now."),
                                                    ("maximize_window", "OK: Spotify is maximized.")]),
    ("Timer's set for ten minutes.", [("set_timer", "OK: timer 'timer' is running, 600 seconds.")]),
    ("Done!", [("set_volume", "OK: the volume is now 30%.")]),
    ("Muted.", [("mute", "OK: the sound is muted.")]),
    ("Sent it!", [("gmail_send", "OK: sent to andrew@client.test.")]),
    ("Saved it.", [("remember", "OK: saved.")]),
    ("Spotify's in front now.", [("open_app", "OK: Spotify was already open; it's now in front.")]),
    ("Volume's back to 70.", [("undo_last_action", "OK: the volume is back to 70%.")]),
    ("Moved it back.", [("undo_last_action", "OK: Spotify is back on monitor 1.")]),
    ("Hey, how was your day?", []),
    ("Honestly, that sounds rough.", []),
]

print("claims that must be held back (nothing confirmed them):")
for sentence, results, kind in HELD:
    got = guard(*results).unverified(sentence)
    t.check(f"{sentence!r} -> {kind}", kind in got, got)
print("talk that must be spoken:")
for sentence, results in SPOKEN:
    got = guard(*results).unverified(sentence)
    t.check(f"{sentence!r}", not got, got)
t.done("CLAIM TESTS")
