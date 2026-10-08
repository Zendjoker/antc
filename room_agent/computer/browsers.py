"""Which browsers are on this PC, which one they're using, and opening pages in the right one.

Choosing the browser (never silently another one):
    1. the one they named ("in Chrome")       not installed -> say so; no substitute
    2. the one in front right now
    3. the one used most recently (this session, while that's still recent)
    4. the system default browser             (read only: never changed)
    "the other browser" -> the one other than the current, when that's unambiguous; otherwise ask.

Opening a page launches the browser's own executable with the URL: every browser opens it in a NEW TAB of its existing
window, so the user's tabs are never closed or replaced. Then the result is checked: the browser's address bar (read
through accessibility) must show the site, or one of its window titles must change to it. Only then is it "opened".
"""

import logging
import os
import re
import subprocess
import time
import urllib.parse
from dataclasses import dataclass

log = logging.getLogger("room-agent")
IS_WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class Browser:
    key: str
    name: str
    proc: str               # process image name
    path_hint: str = ""     # part of the install path that tells it apart (Opera GX vs Opera: both opera.exe)
    new_tab_args: tuple = ()


KNOWN = {
    "opera_gx": Browser("opera_gx", "Opera GX", "opera.exe", "opera gx"),
    "opera": Browser("opera", "Opera", "opera.exe"),
    "chrome": Browser("chrome", "Chrome", "chrome.exe"),
    "edge": Browser("edge", "Edge", "msedge.exe"),
    "firefox": Browser("firefox", "Firefox", "firefox.exe", new_tab_args=("-new-tab",)),
    "brave": Browser("brave", "Brave", "brave.exe"),
}
NAMED = [("opera_gx", r"opera\s*gx|\bgx\b"), ("opera", r"\bopera\b"), ("chrome", r"\b(google\s+)?chrome\b"),
         ("edge", r"\b(microsoft\s+)?edge\b"), ("firefox", r"\b(mozilla\s+)?firefox\b|\bmozilla\b"), ("brave", r"\bbrave\b")]
PROGIDS = {"operagxstable": "opera_gx", "operastable": "opera", "chromehtml": "chrome", "msedgehtm": "edge",
           "firefoxurl": "firefox", "bravehtml": "brave"}
OTHER = re.compile(r"\b(the\s+)?other\s+(browser|one)\b", re.I)
RECENT_S = 30 * 60

SITES = {  # what people call a site -> its address
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "google": "https://www.google.com",
    "google maps": "https://maps.google.com", "maps": "https://maps.google.com", "google drive": "https://drive.google.com",
    "drive": "https://drive.google.com", "google calendar": "https://calendar.google.com", "calendar": "https://calendar.google.com",
    "google docs": "https://docs.google.com", "github": "https://github.com", "reddit": "https://www.reddit.com",
    "twitter": "https://x.com", "x": "https://x.com", "netflix": "https://www.netflix.com", "amazon": "https://www.amazon.com",
    "wikipedia": "https://en.wikipedia.org", "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai",
    "spotify": "https://open.spotify.com", "twitch": "https://www.twitch.tv", "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com", "linkedin": "https://www.linkedin.com", "outlook": "https://outlook.live.com",
    "stack overflow": "https://stackoverflow.com", "stackoverflow": "https://stackoverflow.com",
    "hacker news": "https://news.ycombinator.com", "whatsapp": "https://web.whatsapp.com", "discord": "https://discord.com/app",
}
ENGINES = {
    "google": "https://www.google.com/search?q={q}", "youtube": "https://www.youtube.com/results?search_query={q}",
    "bing": "https://www.bing.com/search?q={q}", "duckduckgo": "https://duckduckgo.com/?q={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}", "amazon": "https://www.amazon.com/s?k={q}",
    "github": "https://github.com/search?q={q}", "reddit": "https://www.reddit.com/search/?q={q}",
}


# ---------------------------------------------------------------- what's installed (cached)
_installed = None


def _registry_browsers():
    """{key: exe} from the Windows 'Start Menu Internet' registrations (machine and user)."""
    import winreg

    out = {}
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for base in (r"SOFTWARE\Clients\StartMenuInternet", r"SOFTWARE\WOW6432Node\Clients\StartMenuInternet"):
            try:
                root = winreg.OpenKey(hive, base)
            except OSError:
                continue
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    cmd = winreg.QueryValue(winreg.OpenKey(root, sub + r"\shell\open\command"), None)
                except OSError:
                    continue
                exe = cmd.split('"')[1] if cmd.startswith('"') else cmd.split(" ")[0]
                key = key_for_exe(exe)
                if key and key not in out and os.path.exists(exe):
                    out[key] = exe
    return out


def _known_paths():
    la, pf, pf86 = os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")
    return {"opera": [os.path.join(la, r"Programs\Opera\opera.exe")],
            "opera_gx": [os.path.join(la, r"Programs\Opera GX\opera.exe")],
            "chrome": [os.path.join(pf, r"Google\Chrome\Application\chrome.exe"), os.path.join(la, r"Google\Chrome\Application\chrome.exe")],
            "edge": [os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"), os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe")],
            "firefox": [os.path.join(pf, r"Mozilla Firefox\firefox.exe"), os.path.join(pf86, r"Mozilla Firefox\firefox.exe")],
            "brave": [os.path.join(pf, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
                      os.path.join(la, r"BraveSoftware\Brave-Browser\Application\brave.exe")]}


def installed(refresh=False):
    """{key: exe path} of the supported browsers on this PC."""
    global _installed
    if _installed is None or refresh:
        found = {}
        if IS_WINDOWS:
            try:
                found = _registry_browsers()
            except Exception as e:
                log.debug("browser registry not readable: %s", e)
            for key, paths in _known_paths().items():
                if key not in found:
                    hit = next((p for p in paths if p and os.path.exists(p)), None)
                    if hit:
                        found[key] = hit
        _installed = found
    return dict(_installed)


def key_for_exe(exe):
    path = str(exe or "").lower().replace("/", "\\")
    name = os.path.basename(path)
    for key, b in KNOWN.items():
        if name == b.proc and (not b.path_hint or b.path_hint in path):
            if key == "opera" and "opera gx" in path:
                continue
            return key
    return None


def default_key():
    """The system default browser (for https links), or None. Read only."""
    if not IS_WINDOWS:
        return None
    try:
        import winreg

        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice")
        progid = winreg.QueryValueEx(k, "ProgId")[0].lower().replace(" ", "")
    except OSError:
        return None
    return next((key for p, key in PROGIDS.items() if progid.startswith(p)), None)


def named_in(text):
    """The browser they named ('in chrome', 'with opera gx'), or None."""
    t = str(text or "")
    return next((key for key, pat in NAMED if re.search(pat, t, re.I)), None)


# ---------------------------------------------------------------- windows
def _exe_of(pid):
    try:
        import psutil

        return psutil.Process(pid).exe()
    except Exception:
        return ""


def _top_windows():
    """[(hwnd, pid, title)] of visible top-level windows with a title."""
    import ctypes
    from ctypes import wintypes as wt

    from room_agent import config

    config.real_desktop("listing the PC's windows")
    user32 = ctypes.windll.user32
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                out.append((hwnd, pid.value, buf.value))
        return True

    user32.EnumWindows(each, 0)
    return out


def browser_windows(key=None):
    """[(key, hwnd, title)] of open browser windows, front-most first (Windows lists them in z-order)."""
    out, exes = [], {}
    for hwnd, pid, title in _top_windows():
        if pid not in exes:
            exes[pid] = key_for_exe(_exe_of(pid))
        k = exes[pid]
        if k and (key is None or k == key):
            out.append((k, hwnd, title))
    return out


def foreground_browser():
    """(key, hwnd) if the window in front is a supported browser, else (None, None)."""
    if not IS_WINDOWS:
        return None, None
    import ctypes

    hwnd = ctypes.windll.user32.GetForegroundWindow()
    for k, h, _ in browser_windows():
        if h == hwnd:
            return k, h
    return None, None


def title_of(hwnd):
    import ctypes

    user32 = ctypes.windll.user32
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def read_state(hwnd):
    """{"url", "title"} of a browser window's active tab, read now ('' where it can't be read)."""
    from room_agent.computer import uia

    url = ""
    try:
        url = uia.address(hwnd)
    except Exception as e:
        log.debug("address not readable: %s", e)
    return {"url": url, "title": title_of(hwnd) if IS_WINDOWS else ""}


# ---------------------------------------------------------------- choosing
def choose(said_browser="", text=""):
    """-> (key, why) or (None, the problem to tell them). `said_browser`: the tool argument; `text`: their words."""
    from room_agent.computer.context import desk

    have = installed()
    if not have:
        return None, "FAILED: no supported browser (Opera, Opera GX, Chrome, Edge, Firefox, Brave) was found on this PC."
    wanted = str(said_browser or "").strip()
    if OTHER.search(wanted) or (not wanted and OTHER.search(str(text or ""))):
        current = desk.current_browser()
        others = [k for k in desk.recent_browsers() if k != current and k in have]
        if current and len(others) == 1:
            return others[0], "the other browser you used"
        if current and not others and len(have) == 2:
            return next(k for k in have if k != current), "the other browser"
        return None, ("NEEDS: which browser? (Installed: " + ", ".join(KNOWN[k].name for k in have) + ".)")
    key = named_in(wanted) or (named_in(text) if not wanted else None)
    if wanted and not key and wanted.lower() not in ("", "default", "current", "this", "same", "it"):
        return None, f"FAILED: '{wanted}' isn't a browser I can control (Opera, Opera GX, Chrome, Edge, Firefox, Brave)."
    if key:
        if key not in have:
            return None, (f"FAILED: {KNOWN[key].name} isn't installed on this PC, so nothing was opened (installed: "
                          + ", ".join(KNOWN[k].name for k in have) + "). Don't open it somewhere else unless they say so.")
        return key, "you named it"
    front, _ = foreground_browser()
    if front in have:
        return front, "it's in front"
    recent = desk.current_browser(max_age=RECENT_S)
    if recent in have:
        return recent, "you used it last"
    d = default_key()
    if d in have:
        return d, "your default browser"
    if len(have) == 1:
        return next(iter(have)), "the only browser"
    return None, "NEEDS: which browser? (Installed: " + ", ".join(KNOWN[k].name for k in have) + ".)"


# ---------------------------------------------------------------- addresses
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
_DOMAIN = re.compile(r"^(?:www\.)?[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?(/.*)?$", re.I)


def to_url(site):
    """'youtube' / 'github.com/x' / 'https://...' -> a safe https URL, or None if it isn't an address."""
    s = str(site or "").strip().strip("'\"<>").rstrip(".")
    if not s:
        return None
    low = re.sub(r"^(the\s+)?", "", s.lower()).strip()
    low = re.sub(r"\s+(website|site|page|homepage|web\s*site)$", "", low)
    if low in SITES:
        return SITES[low]
    if _SCHEME.match(s):
        p = urllib.parse.urlparse(s)
        return s if p.scheme in ("http", "https") and p.netloc else None  # (never javascript:, file:, data: ...)
    if " " not in s and _DOMAIN.match(s):
        return "https://" + s
    return None


def search_url(query, engine="google", site=""):
    q = str(query or "").strip()
    if site:
        q = f"site:{site} {q}"
    template = ENGINES.get(str(engine or "google").lower().strip(), ENGINES["google"])
    return template.format(q=urllib.parse.quote_plus(q))


def host(url):
    u = str(url or "")
    if not _SCHEME.match(u):
        u = "https://" + u
    h = (urllib.parse.urlparse(u).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def same_site(a, b):
    ha, hb = host(a), host(b)
    return bool(ha and hb) and (ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha))


def _site_word(url):
    """What the site is called in a window title: 'youtube.com' -> 'youtube', 'mail.google.com' -> 'gmail'."""
    h = host(url)
    if h.startswith("mail.google."):
        return "gmail"
    parts = h.split(".")
    return parts[-2] if len(parts) >= 2 else h


# ---------------------------------------------------------------- opening
def _launch(exe, args):
    """Start the browser with arguments (no shell). -> True if it started."""
    from room_agent import config

    config.real_desktop("launching a browser")
    subprocess.Popen([exe, *args], close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    return True


def _expected_title_words(url, query=""):
    words = [w for w in re.findall(r"\w{3,}", str(query).lower())][:3]
    return words or [_site_word(url)]


def wait_loaded(key, url, before_titles, query="", timeout=8.0):
    """Wait until a window of browser `key` shows the page. -> (hwnd, state) or (None, last state seen)."""
    from room_agent import cancel

    deadline, last = time.time() + timeout, {}
    want_words = _expected_title_words(url, query)
    while time.time() < deadline and not cancel.requested():
        for _, hwnd, title in browser_windows(key):
            st = read_state(hwnd)
            last = st
            if st["url"] and same_site(st["url"], url) and (not query or all(w in st["url"].lower() or w in title.lower()
                                                                             for w in want_words[:1])):
                return hwnd, st
            if title != before_titles.get(hwnd) and all(w in title.lower() for w in want_words[:1]):
                return hwnd, st
        time.sleep(0.4)
    return None, last


def safe_launch_url(url):
    """A URL that's safe to put on a browser's command line: http(s) with a host, no whitespace / control characters,
    and it can't be read as a switch ("--gpu-launcher=..." would make Chromium run a program)."""
    u = str(url or "")
    if not u or u.lstrip() != u or u.startswith(("-", "/")) or re.search(r"[\s\x00-\x1f\x7f\"]", u):
        return False
    p = urllib.parse.urlparse(u)
    return p.scheme in ("http", "https") and bool(p.netloc) and not p.netloc.startswith("-")


def open_url(url, browser="", said="", query=""):
    """Open `url` in a new tab of the right browser and check that it's showing. -> tool result text."""
    from room_agent.computer.context import desk

    if not safe_launch_url(url):
        return "FAILED: that isn't a plain web address (http / https), so it wasn't opened."
    key, why = choose(browser, said)
    if key is None:
        return why
    exe = installed()[key]
    b = KNOWN[key]
    before = {h: t for _, h, t in browser_windows(key)}
    try:
        _launch(exe, [*b.new_tab_args, url])
    except Exception as e:
        return f"FAILED: {b.name} couldn't be started ({e.__class__.__name__}). Nothing was opened."
    hwnd, st = wait_loaded(key, url, before, query)
    desk.used_browser(key)
    from room_agent import cancel

    if not hwnd and cancel.requested():
        return f"FAILED: stopped: they interrupted while {b.name} was opening {host(url)}; whether it opened isn't confirmed."
    if not hwnd:
        return (f"UNKNOWN: not confirmed: {b.name} was asked to open {host(url)}, but its window never showed the page "
                f"within a few seconds. Tell them you couldn't confirm it opened; don't say it did.")
    desk.saw_page(key, hwnd, st.get("url") or url, st.get("title", ""))
    return f"OK: opened {host(url)} in a new {b.name} tab ({why}); it's showing \"{_short_title(st.get('title', ''), b)}\"."


def _short_title(title, b):
    t = re.sub(rf"\s*[-–—]\s*{re.escape(b.name)}.*$", "", str(title or ""), flags=re.I)
    return t[:80]
