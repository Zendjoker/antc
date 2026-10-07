"""Windows app control: open, close, switch to and list apps.

The model only says WHICH app ("Spotify", "VS Code", "it"). This code finds the app, acts, and checks the result (a window
really appeared, came to the front, or went away) before anything is reported as done. Nothing here runs arbitrary
commands: the only things it starts are installed apps.

Finding apps (cached in apps_cache.json, rebuilt every few days or when an app isn't found):
  - the Start menu's app list (Get-StartApps: desktop apps and Store apps alike, each with an id Windows can launch)
  - Start Menu shortcuts (which .exe an app runs, to recognize its process)
  - registered program paths (App Paths) and common install folders
  - running processes
  - a few spoken aliases ("VS Code" -> Visual Studio Code)
"""

import ctypes
import difflib
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from room_agent import runtime as rt
from room_agent.config import APPS_CACHE

log = logging.getLogger("room-agent")
IS_WINDOWS = os.name == "nt"
CACHE_DAYS = 3
OPEN_WAIT_S, CLOSE_WAIT_S = 15.0, 8.0

# What people say -> the name in the Start menu (only where the words differ)
ALIASES = {"vs code": "visual studio code", "vscode": "visual studio code", "code": "visual studio code",
           "chrome": "google chrome", "edge": "microsoft edge", "explorer": "file explorer", "files": "file explorer",
           "word": "microsoft word", "excel": "microsoft excel", "powerpoint": "microsoft powerpoint",
           "outlook": "microsoft outlook", "teams": "microsoft teams", "calculator": "calculator",
           "photoshop": "adobe photoshop", "premiere": "adobe premiere pro", "after effects": "adobe after effects",
           "illustrator": "adobe illustrator", "lightroom": "adobe lightroom", "obs": "obs studio", "fl studio": "fl studio",
           "terminal": "terminal", "cmd": "command prompt", "settings": "settings", "task manager": "task manager"}
JUNK = re.compile(r"uninstall|readme|read me|release notes|documentation|\bhelp\b|website|\bmanual\b|license|safe mode|"
                  r"\bdebug\b|crash|repair|\bsetup\b|installer|updater?\b", re.I)
# Processes that are never "the app" (shells, hosts, Windows itself)
SYSTEM_PROCS = {"explorer.exe", "applicationframehost.exe", "textinputhost.exe", "shellexperiencehost.exe", "searchhost.exe",
                "startmenuexperiencehost.exe", "lockapp.exe", "dwm.exe", "csrss.exe", "winlogon.exe", "svchost.exe",
                "lsass.exe", "services.exe", "system", "runtimebroker.exe", "conhost.exe", "python.exe", "pythonw.exe",
                "py.exe", "update.exe", "systemsettingsbroker.exe", "widgets.exe", "nvidia overlay.exe"}
GENERIC_STEMS = {"app", "main", "launcher", "client", "update", "helper", "host", "service", "run", "start"}
PRONOUNS = {"", "it", "that", "this", "the app", "that app", "this app", "the program", "that one", "this one", "same app"}

_lock = threading.Lock()
_cache = None  # {"built": epoch, "entries": [...], "exes": {stem: path}}
_last_scan = 0.0


# ---------------------------------------------------------------- names
def norm(text):
    text = str(text or "").lower().replace("&", " and ").replace("+", " plus ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\b(the|app|application|program)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact(text):
    return norm(text).replace(" ", "")


# ---------------------------------------------------------------- discovery
_SCAN = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$sh = New-Object -ComObject WScript.Shell
$apps = @(Get-StartApps | ForEach-Object { @{ name = $_.Name; id = $_.AppID } })
$dirs = @("$env:ProgramData\Microsoft\Windows\Start Menu\Programs", "$env:APPDATA\Microsoft\Windows\Start Menu\Programs")
$links = @(Get-ChildItem -Path $dirs -Recurse -Filter *.lnk | ForEach-Object {
    $l = $sh.CreateShortcut($_.FullName); @{ name = $_.BaseName; path = $_.FullName; target = $l.TargetPath; args = $l.Arguments } })
@{ apps = $apps; links = $links } | ConvertTo-Json -Depth 3 -Compress
"""
_KNOWN_FOLDERS = {"{6D809377-6AF0-444B-8957-A3773F02200E}": os.environ.get("ProgramW6432", r"C:\Program Files"),
                  "{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}": os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                  "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}": os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
                  "{F38BF404-1D43-42F2-9305-67DE0B28FC23}": os.environ.get("SystemRoot", r"C:\Windows")}


def _powershell_scan():
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCAN], capture_output=True,
                       timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    data = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    as_list = lambda x: x if isinstance(x, list) else [x] if x else []  # (one item comes back as an object)
    return as_list(data.get("apps")), as_list(data.get("links"))


def _exe_of_link(link):
    """The process a shortcut ends up running (Discord's shortcut runs Update.exe --processStart Discord.exe)."""
    m = re.search(r"--processStart\s+\"?([^\"\s]+\.exe)", link.get("args") or "", re.I)
    if m:
        return m[1].lower()
    target = link.get("target") or ""
    return os.path.basename(target).lower() if target.lower().endswith(".exe") else ""


def _app_paths():
    """Programs registered under App Paths: {stem: exe path}."""
    import winreg

    out = {}
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            root = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths")
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                sub = winreg.EnumKey(root, i)
                path = winreg.QueryValue(root, sub).strip('"')
            except OSError:
                continue
            if path.lower().endswith(".exe") and os.path.exists(path):
                out[Path(sub).stem.lower()] = path
    return out


def _common_exes():
    """Executables one or two folders deep in the usual install places: {stem: path}."""
    out = {}
    roots = [os.environ.get("LOCALAPPDATA", "") + r"\Programs", os.environ.get("ProgramW6432", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    for root in filter(os.path.isdir, roots):
        for pattern in ("*/*.exe", "*/*/*.exe"):
            for exe in Path(root).glob(pattern):
                stem = exe.stem.lower()
                if not JUNK.search(stem) and stem not in GENERIC_STEMS:
                    out.setdefault(stem, str(exe))
    return out


def build_cache():
    """Scan everything once and save it (a couple of seconds)."""
    t0 = time.time()
    apps, links = _powershell_scan()
    by_name = {}
    for link in links:
        by_name.setdefault(norm(link.get("name")), link)
    entries, seen = [], set()
    for app in apps:
        name, aid = str(app.get("name") or "").strip(), str(app.get("id") or "").strip()
        if not name or not aid or JUNK.search(name) or norm(name) in seen:
            continue
        seen.add(norm(name))
        procs, exe = set(), ""
        link = by_name.get(norm(name))
        if link and _exe_of_link(link):
            procs.add(_exe_of_link(link))
            exe = link.get("target") or ""
        if aid.lower().endswith(".exe"):  # an id that is a path, maybe under a known folder
            procs.add(os.path.basename(aid).lower())
            folder = aid.split("\\", 1)
            exe = exe or (os.path.join(_KNOWN_FOLDERS[folder[0]], folder[1]) if folder[0] in _KNOWN_FOLDERS else aid)
        entries.append({"name": name, "id": aid, "exe": exe, "link": (link or {}).get("path", ""),
                        "procs": sorted(p for p in procs if p not in SYSTEM_PROCS)})
    for link in links:  # shortcuts that aren't in the app list
        name = str(link.get("name") or "")
        if norm(name) not in seen and not JUNK.search(name) and _exe_of_link(link):
            seen.add(norm(name))
            entries.append({"name": name, "id": "", "exe": link.get("target") or "", "link": link.get("path", ""),
                            "procs": [p for p in [_exe_of_link(link)] if p not in SYSTEM_PROCS]})
    exes = {**_common_exes(), **_app_paths()}
    cache = {"built": time.time(), "entries": entries, "exes": exes}
    try:
        APPS_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError as e:
        log.warning("couldn't save the app list: %s", e)
    log.info("found %d apps and %d programs in %.1fs", len(entries), len(exes), time.time() - t0)
    return cache


def apps(refresh=False):
    """The app list: from memory, else the saved file, else a fresh scan."""
    global _cache, _last_scan
    with _lock:
        if _cache is None and not refresh:
            try:
                saved = json.loads(APPS_CACHE.read_text(encoding="utf-8"))
                if time.time() - saved.get("built", 0) < CACHE_DAYS * 86400:
                    _cache = saved
            except (OSError, ValueError):
                pass
        if _cache is None or (refresh and time.time() - _last_scan > 30):
            _last_scan = time.time()
            _cache = build_cache()
        return _cache


def warm():
    """Build the app list in the background at startup, so the first "open X" is quick."""
    if IS_WINDOWS:
        threading.Thread(target=lambda: _safe(apps), daemon=True).start()


def _safe(fn):
    try:
        fn()
    except Exception as e:
        log.warning("couldn't list the installed apps: %s", e)


def _version(name):
    m = re.search(r"(\d{2,4})\s*$", name)
    return int(m[1]) if m else 0


def find(query, cache=None):
    """Best app for what they said -> (entry or None, other close matches). Scores: exact name, alias, all the words,
    start of the name, then near-spellings (speech recognition errors). Newer versions win a tie (Photoshop 2026)."""
    cache = cache or apps()
    q = norm(query)
    if not q:
        return None, []
    wanted = {q, norm(ALIASES.get(q, q))}
    scored = []
    for e in cache["entries"]:
        n, c = norm(e["name"]), compact(e["name"])
        nv = re.sub(r"\s*\d{2,4}$", "", n)  # without a version year
        words = set(n.split())
        if n in wanted or nv in wanted:
            s = 100
        elif c in {w.replace(" ", "") for w in wanted}:
            s = 95
        elif any(w and set(w.split()) <= words for w in wanted):
            s = 80
        elif any(w and n.startswith(w) for w in wanted):
            s = 75
        else:
            ratio = max(difflib.SequenceMatcher(None, w.replace(" ", ""), c).ratio() for w in wanted)
            s = 60 if ratio >= 0.85 else 0
        if s:
            scored.append((s, _version(e["name"]), -len(n), e))
    if not scored:
        stem = cache.get("exes", {}).get(q.replace(" ", "")) or cache.get("exes", {}).get(q)
        if stem:  # a program found in an install folder but not in the Start menu
            return {"name": query.strip().title(), "id": "", "exe": stem, "link": "", "procs": [os.path.basename(stem).lower()]}, []
        return None, []
    scored.sort(key=lambda x: x[:3], reverse=True)
    best = scored[0]
    rivals = [x[3]["name"] for x in scored[1:4] if x[0] == best[0] and norm(re.sub(r"\d{2,4}$", "", x[3]["name"]))
              != norm(re.sub(r"\d{2,4}$", "", best[3]["name"]))]
    return best[3], rivals


def resolve(name):
    """-> (entry, None) or (None, tool result). "it" / "that" means the app this conversation is about."""
    said = str(name or "").strip()
    if norm(said) in PRONOUNS or said.lower() in PRONOUNS:
        last = rt.last_active_app
        if not last:
            return None, "NEEDS: which app (nothing was done). Ask which app they mean."
        said = last["name"]
    entry, rivals = find(said)
    if entry is None:
        entry, rivals = find(said, apps(refresh=True))  # installed since the last scan?
    if entry is None:
        running = _running_by_name(said)
        if running:
            return running, None
        return None, (f"FAILED: couldn't find an app called '{said}' on this PC, so nothing was done. "
                      f"Tell them plainly: I couldn't find {said} on this PC.")
    if rivals:
        log.info("'%s' -> %s (also close: %s)", said, entry["name"], ", ".join(rivals))
    return entry, None


# ---------------------------------------------------------------- processes and windows
def _psutil():
    import psutil

    return psutil


def _matches(entry, pname):
    pname = (pname or "").lower()
    if pname in SYSTEM_PROCS:
        return False
    if entry.get("procs"):
        return pname in entry["procs"]
    stem, app = compact(Path(pname).stem), compact(entry["name"])  # (Store apps: no .exe known, go by name)
    return len(stem) >= 4 and stem not in GENERIC_STEMS and (stem == app or (len(app) >= 4 and (app in stem or stem in app)))


def processes(entry):
    psutil = _psutil()
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if _matches(entry, p.info["name"]):
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def _running_by_name(said):
    """Not installed as far as the scan knows, but running ("close notepad++")."""
    entry = {"name": said, "id": "", "exe": "", "link": "", "procs": []}
    procs = processes(entry)
    if not procs:
        return None
    try:
        entry["exe"] = procs[0].exe()
    except Exception:
        pass
    entry["procs"] = sorted({p.info["name"].lower() for p in procs})
    return entry


if IS_WINDOWS:
    from ctypes import wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    _EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user32.EnumWindows.argtypes = [_EnumProc, wt.LPARAM]
    for _f in ("IsWindowVisible", "IsIconic", "GetWindowTextLengthW", "SetForegroundWindow", "BringWindowToTop"):
        getattr(user32, _f).argtypes = [wt.HWND]
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindow.argtypes, user32.GetWindow.restype = [wt.HWND, wt.UINT], wt.HWND
    user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.GetForegroundWindow.restype = wt.HWND
    user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.IsWindowEnabled.argtypes = [wt.HWND]
    user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
    dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]

GW_OWNER, GWL_EXSTYLE, WS_EX_TOOLWINDOW, DWMWA_CLOAKED = 4, -20, 0x80, 14
SW_RESTORE, WM_CLOSE, VK_MENU, KEYEVENTF_KEYUP = 9, 0x0010, 0x12, 0x2


def windows():
    """Visible top-level app windows, front to back: [(hwnd, pid, title)]."""
    out = []

    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, GW_OWNER) \
                and not user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            n = user32.GetWindowTextLengthW(hwnd)
            cloaked = ctypes.c_int(0)
            dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if n and not cloaked.value:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                out.append((hwnd, pid.value, buf.value))
        return True

    user32.EnumWindows(_EnumProc(visit), 0)
    return out


def app_windows(entry, pids=None):
    """This app's windows. Store apps framed by ApplicationFrameHost are recognized by their title."""
    pids = {p.pid for p in processes(entry)} if pids is None else pids
    hosts = None
    out = []
    for hwnd, pid, title in windows():
        if pid in pids:
            out.append((hwnd, pid, title))
        elif compact(entry["name"]) and compact(entry["name"]) in compact(title):
            if hosts is None:
                hosts = {p.pid for p in _psutil().process_iter(["name"]) if (p.info["name"] or "").lower() == "applicationframehost.exe"}
            if pid in hosts:
                out.append((hwnd, pid, title))
    return out


def _front_pid():
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    return pid.value


def bring_to_front(hwnd):
    """Restore it if minimized and put it in front. Windows only lets the foreground app hand over focus, so the input
    queues are joined for a moment (and an Alt press is the fallback). True if it really is in front now."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    fg = user32.GetForegroundWindow()
    me, them = kernel32.GetCurrentThreadId(), user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(them and them != me and user32.AttachThreadInput(me, them, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, them, False)
    if user32.GetForegroundWindow() != hwnd:
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    for _ in range(10):
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    return False


def _launch(entry):
    if entry.get("id"):
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + entry["id"]],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    elif entry.get("link") and os.path.exists(entry["link"]):
        os.startfile(entry["link"])
    elif entry.get("exe") and os.path.exists(entry["exe"]):
        os.startfile(entry["exe"])
    else:
        raise FileNotFoundError(entry.get("exe") or entry["name"])


def _wait_for_window(entry, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        wins = app_windows(entry)
        if wins:
            return wins
        time.sleep(0.3)
    return []


def _protected_pids():
    """This agent and everything it runs inside (the terminal, VS Code...): closing those would shut Jarvis down."""
    psutil, out = _psutil(), set()
    try:
        p = psutil.Process(os.getpid())
        while p:
            out.add(p.pid)
            p = p.parent()
    except psutil.Error:
        pass
    return out


def _remember(entry, action):
    rt.last_active_app = {"name": entry["name"], "action": action, "at": time.time()}


# ---------------------------------------------------------------- tools
def open_app(app_name):
    if not IS_WINDOWS:
        return "UNAVAILABLE: app control only works on Windows."
    entry, problem = resolve(app_name)
    if problem:
        return problem
    name = entry["name"]
    if processes(entry):
        front = focus_entry(entry)
        _remember(entry, "opened")
        return (f"OK: {name} was already open; it's now in front." if front else
                f"OK: {name} is already open (it's running, but its window couldn't be brought to the front).")
    try:
        _launch(entry)
    except Exception as e:
        return f"FAILED: couldn't start {name} ({e.__class__.__name__}). Nothing was opened."
    wins = _wait_for_window(entry, OPEN_WAIT_S)
    if wins:
        bring_to_front(wins[0][0])
        _remember(entry, "opened")
        return f"OK: {name} is open; its window is up."
    if processes(entry):
        _remember(entry, "opened")
        return f"OK: {name} started (it's running), but no window has shown up yet."
    return f"FAILED: tried to open {name}, but it didn't start within {OPEN_WAIT_S:.0f} seconds."


def focus_entry(entry):
    """Bring the app's window to the front. An app that only lives in the tray is asked to show its window by starting
    it again (single-instance apps like Spotify and Discord show their window instead of a second copy)."""
    wins = app_windows(entry)
    if not wins and (entry.get("id") or entry.get("link") or entry.get("exe")):
        try:
            _launch(entry)
            wins = _wait_for_window(entry, 6)
        except Exception:
            wins = []
    return bool(wins) and bring_to_front(wins[0][0])


def focus_app(app_name):
    if not IS_WINDOWS:
        return "UNAVAILABLE: app control only works on Windows."
    entry, problem = resolve(app_name)
    if problem:
        return problem
    name = entry["name"]
    if not processes(entry):
        return f"FAILED: {name} isn't open, so there's nothing to switch to. Offer to open it."
    if focus_entry(entry):
        _remember(entry, "focused")
        return f"OK: {name} is in front now."
    return f"FAILED: {name} is running, but Windows wouldn't bring its window to the front."


def close_app(app_name):
    if not IS_WINDOWS:
        return "UNAVAILABLE: app control only works on Windows."
    entry, problem = resolve(app_name)
    if problem:
        return problem
    psutil, name = _psutil(), entry["name"]
    procs = processes(entry)
    if not procs:
        return f"OK: {name} wasn't open, so there was nothing to close."
    if {p.pid for p in procs} & _protected_pids():
        return f"FAILED: I'm running inside {name}, so closing it would shut me down too. Nothing was closed."
    started, alive, asked = time.time(), procs, {}
    while alive:
        for hwnd, _, _ in app_windows(entry, {p.pid for p in alive}):
            # The polite way, like clicking X. Asked again every second: an app that's still starting up (a splash
            # screen) ignores it. A window that's disabled has a dialog open ("save changes?"): it's left alone.
            if user32.IsWindowEnabled(hwnd) and time.time() - asked.get(hwnd, 0) > 1.0:
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                asked[hwnd] = time.time()
        _, alive = psutil.wait_procs(alive, timeout=0.5)
        alive = [p for p in alive if p.is_running()]
        if not alive or time.time() - started > CLOSE_WAIT_S:
            break
        if time.time() - started > 1.5 and not app_windows(entry, {p.pid for p in alive}):
            break  # window gone but still running: it went to the tray, no point waiting longer
    tray = ""
    if alive:
        left = app_windows(entry, {p.pid for p in alive})
        if any(not user32.IsWindowEnabled(w[0]) for w in left):
            return f"FAILED: {name} is still open; it's asking something (like saving changes). Nothing was forced closed."
        if left:
            return f"FAILED: {name} didn't close when asked, so it wasn't forced closed."
        for p in alive:  # no window left, but still running in the background / tray
            try:
                p.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(alive, timeout=3)
        tray = " (it kept running in the background after its window closed, so it was ended)"
    deadline = time.time() + 3
    while (left := processes(entry)) and time.time() < deadline:  # (an app still starting up spawns new helpers)
        if tray:
            for p in left:
                try:
                    p.terminate()
                except psutil.Error:
                    pass
        time.sleep(0.3)
    if processes(entry):
        return f"FAILED: tried to close {name}, but it's still running."
    _remember(entry, "closed")
    return f"OK: {name} is closed{tray}."


def list_running_apps():
    if not IS_WINDOWS:
        return "UNAVAILABLE: app control only works on Windows."
    psutil, names = _psutil(), []
    entries = apps()["entries"]
    by_proc = {}
    for e in entries:
        for p in e.get("procs", []):
            by_proc.setdefault(p, e["name"])
    for _, pid, title in windows():
        try:
            pname = psutil.Process(pid).name().lower()
        except psutil.Error:
            continue
        if pname in SYSTEM_PROCS - {"applicationframehost.exe"}:
            continue
        label = title if pname == "applicationframehost.exe" else by_proc.get(pname) or next(
            (e["name"] for e in entries if not e.get("procs") and _matches(e, pname)), Path(pname).stem)
        if label not in names:
            names.append(label)
    return "OK: open apps: " + ", ".join(names) + "." if names else "OK: no app windows are open."
