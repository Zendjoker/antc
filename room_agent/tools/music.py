"""Choosing what to play: "play my gym playlist", "play Drake", "play some lo-fi", "play X on YouTube".

Spotify (the desktop app, no developer account needed): its accessibility tree has a "Play <name>" button for every
playlist in your library and for every search result, so:
    1. one of your playlists matches what they said -> its Play button
    2. otherwise Spotify's own search (spotify:search:...) -> the top result's Play button
YouTube: the browser tools (a YouTube search in a new tab, then the first video).

Played only counts when Windows' media controls show something new playing afterwards (title / artist changed and the
status is Playing); otherwise it's "not confirmed".
"""

import os
import re
import time
import urllib.parse

from room_agent.computer import uia

WAIT_S = 8.0


def _spotify_window():
    from room_agent.computer import browsers

    for hwnd, pid, title in browsers._top_windows():
        if "spotify" in browsers._exe_of(pid).lower():
            return hwnd
    return None


def _open_uri(uri):
    from room_agent import config

    config.real_desktop("opening a spotify: link")
    os.startfile(uri)  # noqa: S606 (a spotify: link, handled by the Spotify app)


def _buttons(hwnd):
    """[(what it plays, El)] for every 'Play ...' button in the Spotify window."""
    out = []
    for e in uia.find(hwnd, ["button"], limit=1500):
        if e.name.lower().startswith("play ") and e.enabled:
            out.append((e.name[5:].strip(), e))
    return out


def _now():
    """(title, artist, playing?) from Windows' media controls, or None."""
    from room_agent.tools import media

    try:
        snap = media._winrt(media._snapshot)
    except Exception:
        return None
    if snap is None:
        return None
    return snap["title"], snap["artist"], snap["status"] == media.PLAYING


def _score(name, query):
    a, b = name.lower(), query.lower().strip()
    if a == b:
        return 1.0
    if b and (a.startswith(b) or f" {b}" in f" {a}"):
        return 0.85
    wa, wb = set(re.findall(r"\w+", a)), set(re.findall(r"\w+", b))
    return 0.7 * len(wa & wb) / max(len(wb), 1) if wb else 0.0


def _stop():
    from room_agent import cancel

    return cancel.requested()


def _wait_playing(before, query):
    """Something new is playing (it changed from `before`, and it's playing). -> (title, artist) or None."""
    deadline = time.time() + WAIT_S
    while time.time() < deadline and not _stop():
        now = _now()
        if now and now[2] and (before is None or (now[0], now[1]) != (before[0], before[1]) or not before[2]):
            return now[0], now[1]
        time.sleep(0.4)
    return None


def _launch_spotify():
    _open_uri("spotify:")
    deadline = time.time() + 12
    while time.time() < deadline and not _stop():
        hwnd = _spotify_window()
        if hwnd:
            return hwnd
        time.sleep(0.5)
    return None


def spotify(query, kind="any"):
    out = _spotify(query, kind)
    if not out.startswith("OK") and _stop():
        return "FAILED: stopped: they interrupted; nothing new is confirmed playing."
    return out


def _spotify(query, kind="any"):
    hwnd = _spotify_window() or _launch_spotify()
    if not hwnd:
        return "FAILED: Spotify isn't running and couldn't be started, so nothing was played."
    before = _now()
    library = _buttons(hwnd)
    if kind in ("any", "playlist"):
        scored = sorted(((_score(n, query), n, e) for n, e in library), key=lambda s: -s[0])
        if scored and scored[0][0] >= (0.85 if kind == "any" else 0.5):
            _, name, el = scored[0]
            if not uia.invoke(el):
                return f"FAILED: couldn't press Play on your playlist \"{name}\"."
            got = _wait_playing(before, query)
            if not got:
                return f"UNKNOWN: not confirmed: pressed Play on your playlist \"{name}\", but nothing new started playing."
            return f"OK: playing your playlist \"{name}\" on Spotify (now: {got[0]}{' by ' + got[1] if got[1] else ''})."
    seen = {n for n, _ in library}
    _open_uri("spotify:search:" + urllib.parse.quote(query))
    deadline, results = time.time() + WAIT_S, []
    while time.time() < deadline and not results and not _stop():
        time.sleep(0.6)
        results = [(n, e) for n, e in _buttons(hwnd) if n not in seen]
    if not results:
        return f"FAILED: Spotify's search for \"{query}\" showed no results to play (or didn't load). Nothing was played."
    best = max(results, key=lambda ne: _score(ne[0], query))
    name, el = best if _score(best[0], query) > 0 else results[0]
    if not uia.invoke(el):
        return f"FAILED: couldn't press Play on \"{name}\" in Spotify."
    got = _wait_playing(before, query)
    if not got:
        return f"UNKNOWN: not confirmed: pressed Play on \"{name}\" in Spotify, but nothing new started playing."
    return f"OK: playing on Spotify: {got[0]}{' by ' + got[1] if got[1] else ''} (from the search for \"{query}\")."


def youtube(query, browser=""):
    from room_agent import runtime as rt
    from room_agent.computer import browser_ops, browsers

    out = browsers.open_url(browsers.search_url(query, "youtube"), browser, rt.turn_text or "", query=query)
    if not out.startswith("OK"):
        return out
    key, hwnd = browser_ops.target_window(browser)
    if not key:
        return hwnd
    time.sleep(1.0)
    clicked = browser_ops.click(hwnd, key, number=1)
    if not clicked.startswith("OK"):
        return f"FAILED: the YouTube results opened, but the first video couldn't be started: {clicked.split(':', 1)[-1].strip()}"
    return "OK: playing on YouTube: " + clicked.split("clicked", 1)[-1].strip(" ;.")


def play(query, service="", kind="any", browser=""):
    query = " ".join(str(query or "").split())
    if not query:
        return "NEEDS: what to play (a song, artist, album, playlist or a mood like 'lo-fi')."
    service = str(service or "").lower()
    if service == "youtube":
        return youtube(query, browser)
    if service in ("", "spotify"):
        from room_agent.tools import apps

        has_spotify = bool(_spotify_window()) or apps.find("Spotify")[0] is not None
        if has_spotify:
            return spotify(query, kind)
        if service == "spotify":
            return "FAILED: Spotify isn't installed on this PC. Offer YouTube instead."
        return youtube(query, browser)
    return f"FAILED: I can play from Spotify or YouTube, not {service}."
