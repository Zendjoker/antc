"""LIVE volume + media run on this PC (no model, no API cost):  .venv\\Scripts\\python -m tests.media_live
Every tool goes through run_tool as when the model calls it; each result is then checked independently by reading
Windows' state directly. Your volume, mute and play/pause state are put back at the end."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TRACE", "0")

from room_agent.tools import media  # noqa: E402
from room_agent.tools.registry import run_tool  # noqa: E402

FAILS = []


def level():
    return media._level(media._endpoint())


def muted():
    return bool(media._endpoint().GetMute())


def now():
    return media._winrt(media._snapshot)


def step(label, tool, args, expect_ok, verify):
    t0 = time.time()
    out = run_tool(tool, args)
    took = time.time() - t0
    ok = out.startswith("OK") == expect_ok and verify()
    print(f"  {'ok  ' if ok else 'FAIL'} {label:34} {took:4.1f}s  {out}")
    if not ok:
        FAILS.append(label)
    return out


orig_level, orig_muted, snap = level(), muted(), now()
orig_playing = bool(snap and snap["status"] == media.PLAYING)
print(f"before: volume {orig_level}%, muted={orig_muted}, media: {media._track(snap) if snap else 'none'} "
      f"({media.STATUS.get(snap['status']) if snap else '-'})")
print("volume:")
step("get_volume", "get_volume", {}, True, lambda: True)
step("set_volume 40 ('Volume 40%')", "set_volume", {"percent": 40}, True, lambda: level() == 40)
step("volume_up 5 ('A little louder')", "volume_up", {"amount": 5}, True, lambda: level() == 45)
step("volume_up default ('louder')", "volume_up", {}, True, lambda: level() == 55)
step("volume_down default ('Turn it down')", "volume_down", {}, True, lambda: level() == 45)
step("mute ('Mute')", "mute", {}, True, lambda: muted())
step("unmute", "unmute", {}, True, lambda: not muted())
print("media:")
if snap is None:
    print("  (nothing has a media session: open Spotify and play something, then run again)")
    FAILS.append("no media session")
else:
    step("get_current_media ('What am I...')", "get_current_media", {}, True, lambda: True)
    step("play_pause play ('Keep playing')", "play_pause", {"action": "play"}, True, lambda: now()["status"] == media.PLAYING)
    time.sleep(1.5)
    before = now()["title"]
    step("next_track ('Next song')", "next_track", {}, True, lambda: now()["title"] != before)
    time.sleep(1.0)
    after_next = now()["title"]
    step("previous_track ('Go back')", "previous_track", {}, True,
         lambda: now()["title"] != after_next or now()["position"] < 5)
    step("play_pause pause ('Pause the music')", "play_pause", {"action": "pause"}, True,
         lambda: now()["status"] == media.PAUSED)
    step("get_current_media (after)", "get_current_media", {}, True, lambda: "paused" in run_tool("get_current_media", {}))
print("restoring:")
media._set_level(orig_level, unmute=False)
media._endpoint().SetMute(1 if orig_muted else 0, None)
if snap is not None and orig_playing:
    run_tool("play_pause", {"action": "play"})
s = now()
print(f"after: volume {level()}%, muted={muted()}, media: {media._track(s) if s else 'none'} "
      f"({media.STATUS.get(s['status']) if s else '-'})")
print("\nALL LIVE MEDIA STEPS PASSED" if not FAILS else f"\nFAILED: {FAILS}")
