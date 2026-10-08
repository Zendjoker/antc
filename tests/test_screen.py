"""Screen understanding, offline: screenshots are simulated and the vision model is a stub (no API call, no cost).

Covers: PNG encoding and downscaling, image -> screen coordinates (DPI / offset), stale screenshots, private windows
refused, permission (off / ask once / allow, remembered), only when they asked about the screen, the image never kept,
vision output parsing, click-by-position only from a fresh screenshot and never on sensitive buttons.

Run:  .venv\\Scripts\\python -m tests.test_screen
"""

import json
import struct
import time
import zlib

from tests.harness import setup_env

setup_env(SCREEN_VISION="ask", VISION_PROVIDER="claude")

import numpy as np  # noqa: E402

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor  # noqa: E402
from room_agent.audio import voices  # noqa: E402
from room_agent.computer import screen, vision, winput  # noqa: E402
from room_agent.computer.context import desk  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
core.ensure_loaded()

print("Images:")
img = (np.arange(40 * 30 * 3) % 251).astype(np.uint8).reshape(30, 40, 3)
data = screen.png(img)
w, h = struct.unpack(">II", data[16:24])
idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
raw = zlib.decompress(idat)
t.check("PNG: valid header and size, pixels round-trip", data[:8] == b"\x89PNG\r\n\x1a\n" and (w, h) == (40, 30)
        and raw[1:4] == img[0, 0].tobytes() and len(raw) == 30 * (1 + 40 * 3))
big = np.zeros((2160, 3840, 3), np.uint8)
small, scale = screen.downscale(big)
t.check("a 4K screenshot is scaled to the model's size (longest side 1568)", max(small.shape[:2]) == 1568
        and abs(scale - 1568 / 3840) < 0.001, (small.shape, scale))
shot = screen.Shot(png=b"x", width=960, height=540, scale=0.5, left=1920, top=100, hwnd=7, title="Settings",
                   rect=(1920, 100, 3840, 1180), at=time.time())
t.check("image pixel -> physical screen pixel (scale and the window's position on monitor 2)",
        screen.to_screen(shot, 100, 50) == (2120, 200), screen.to_screen(shot, 100, 50))

print("Freshness:")
screen.foreground, screen.window_rect = (lambda: 7), (lambda hwnd: (1920, 100, 3840, 1180))
t.check("fresh: same window, same place, just taken", screen.fresh(shot) == "")
old = screen.Shot(**{**shot.__dict__, "at": time.time() - 30})
t.check("30 s old -> stale", "old" in screen.fresh(old), screen.fresh(old))
screen.foreground = lambda: 8
t.check("another window in front now -> stale", "different window" in screen.fresh(shot))
screen.foreground, screen.window_rect = (lambda: 7), (lambda hwnd: (0, 0, 800, 600))
t.check("the window moved -> stale (its coordinates are no good)", "moved" in screen.fresh(shot))
screen.window_rect = lambda hwnd: (1920, 100, 3840, 1180)

print("Privacy:")
for title, proc, url in [("1Password", "1Password.exe", ""), ("Sign in - Google Accounts - Opera", "opera.exe", ""),
                         ("Inbox (3) - Gmail", "opera.exe", "https://mail.google.com/mail/u/0/"),
                         ("WhatsApp", "WhatsApp.exe", ""), ("Checkout - Amazon", "chrome.exe", ""),
                         ("Chase Online Banking", "msedge.exe", "")]:
    t.check(f"private: {title!r} refused", bool(screen.private(1, title, proc, url)), (title, proc, url))
t.check("an ordinary window is allowed", screen.private(1, "Fixes.md - Visual Studio Code", "Code.exe", "") == "")

print("Permission and intent:")
captured, analyzed = [], []
front = {"hwnd": 7, "title": "Settings", "process": "SystemSettings.exe", "url": ""}
screen.front_info = lambda: dict(front)
screen.capture = lambda scope="window", hwnd=None: (captured.append(scope), screen.Shot(
    png=b"PNGDATA", width=960, height=540, scale=0.5, left=1920, top=100, hwnd=7, title=front["title"],
    rect=(1920, 100, 3840, 1180), at=time.time()))[1]
vision.analyze = lambda shot, q: (analyzed.append((len(shot.png), q)), {
    "summary": "The Windows Settings app, on the Bluetooth page.", "text": "Error 0x80070005: access denied",
    "elements": [{"name": "Add device", "kind": "button", "box": [100, 40, 220, 80]},
                 {"name": "Delete device", "kind": "button", "box": [300, 40, 420, 80]}]})[1]
spend_before = config.SPEND_FILE.read_text() if config.SPEND_FILE.exists() else ""


def run(name, args, said):
    rt.new_turn(said)
    return executor.execute(name, args)


r = run("analyze_screen", {"question": "what am I looking at?"}, "what am I looking at?")
t.check("first time (ask mode): asks permission once, no screenshot taken", r.message.startswith("NEEDS") and not captured,
        r.message)
r = run("allow_screen_vision", {"allow": True}, "yes, you can look at my screen")
t.check("their yes is saved (kept across restarts)", r.success and json.loads(config.SETTINGS_FILE.read_text())
        .get("screen_vision_consent") is True)
r = run("analyze_screen", {"question": "what am I looking at?"}, "what am I looking at?")
t.check("F. 'what am I looking at?' -> one fresh screenshot, described", r.success and captured == ["window"]
        and "Bluetooth" in r.message, r.message)
t.check("...the image went to the (stub) model and is not kept afterwards", analyzed and analyzed[0][0] > 0
        and desk.screen["shot"].png == b"")
t.check("'read this error' -> the error text comes back", "0x80070005" in r.message)
captured.clear()
r = run("analyze_screen", {"question": "what's on screen"}, "set a timer for five minutes")
t.check("not asked about the screen this turn -> not looked (asks first)", r.message.startswith("NEEDS_CONFIRMATION")
        and not captured, r.message)
rt.pending = None
front.update(title="Sign in - Google Accounts", process="opera.exe")
r = run("analyze_screen", {"question": "what's this"}, "what's this on my screen")
t.check("a private window in front -> no screenshot taken or sent", not r.success and "private" in r.message and not captured,
        r.message)
front.update(title="Settings", process="SystemSettings.exe")
voices.save_setting("screen_vision_consent", False)
r = run("analyze_screen", {"question": "what's this"}, "look at my screen")
t.check("they said no earlier -> stays no", not r.success and not captured and "shouldn't" in r.message, r.message)
voices.save_setting("screen_vision_consent", True)
config.SCREEN_VISION = "off"
r = run("analyze_screen", {"question": "what's this"}, "look at my screen")
t.check("SCREEN_VISION=off -> never", not r.success and not captured, r.message)
config.SCREEN_VISION = "ask"
t.check("no API money was spent by any of this (stub model)", (config.SPEND_FILE.read_text() if config.SPEND_FILE.exists()
                                                                 else "") == spend_before)

print("Clicking by position (last resort):")
clicks = []
winput.click_at = lambda x, y: (clicks.append((x, y)), True)[1]
r = run("analyze_screen", {"question": "find the add device button"}, "look at my screen, find the add device button")
r = run("click_on_screen", {"target": "Add device"}, "click add device")
t.check("clicks the element's centre, mapped to the screen", r.success and clicks == [(1920 + 160 / 0.5, 100 + 60 / 0.5)], (r.message, clicks))
r = run("click_on_screen", {"target": "Add device"}, "click add device again")
t.check("...and that screenshot is used up: a second click needs a new look", not r.success and len(clicks) == 1, r.message)
r = run("analyze_screen", {"question": "x"}, "look at my screen")
desk.screen["shot"].at -= 60
r = run("click_on_screen", {"target": "Add device"}, "click add device")
t.check("a stale screenshot -> nothing clicked", not r.success and "out of date" in r.message and len(clicks) == 1, r.message)
r = run("analyze_screen", {"question": "x"}, "look at my screen")
r = run("click_on_screen", {"target": "Delete device"}, "click delete device")
t.check("'Delete' is never clicked by position", not r.success and len(clicks) == 1, r.message)

print("Vision output:")
p = vision._parse('Sure! {"summary": "A code editor.", "text": "", "elements": [{"name": "Run", "kind": "button", '
                  '"box": [1, 2, 3, 4]}, {"name": "bad", "box": "nope"}]} trailing')
t.check("JSON is found inside extra words; malformed elements dropped", p["summary"] == "A code editor."
        and len(p["elements"]) == 1, p)
t.check("not JSON at all -> the text is kept as the summary, no elements", vision._parse("Just a desktop.")["elements"] == [])
t.done("SCREEN")
