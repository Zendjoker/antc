"""Window management: minimize, maximize, restore, focus, move to another monitor, what's in front, which monitors.

The model resolves the words ("put it on my second monitor", "minimize this"); this code finds the window, acts through
the Windows window APIs, and reads the result back (is it really minimized / maximized / on that monitor / in front)
before anything is reported as done. No mouse automation and no screen reading.

Which window:  an app name -> that app's top window (via the app finder in apps.py)
               "it" / "that" -> the app this conversation is about (last_active_app)
               "this" / "this window" / "the active window" -> the window that's in front right now
Which monitor: Windows' own numbers (Display 1, 2, 3 in Settings), or "primary", "left", "right", "middle",
               "other" / "next" (the next one after where the window is), "here" (the monitor of the window in front)
"""

import contextlib
import ctypes
import os
import re
import time
from pathlib import Path

from room_agent import runtime as rt
from room_agent.tools import apps

IS_WINDOWS = os.name == "nt"
THIS = {"this", "this window", "current window", "the current window", "active window", "the active window",
        "the window", "this app", "current app", "the current app", "here", "this one", "this thing"}
ORDINALS = {"one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3, "four": 4, "fourth": 4, "five": 5,
            "fifth": 5, "six": 6, "sixth": 6}
SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
MONITOR_DEFAULTTONEAREST = 2
DESKTOP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

if IS_WINDOWS:
    from ctypes import wintypes as wt

    user32 = apps.user32

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD),
                    ("szDevice", wt.WCHAR * 32)]

    _MonitorEnum = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
    user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.c_void_p, _MonitorEnum, wt.LPARAM]
    user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(_MonitorInfo)]
    user32.MonitorFromWindow.argtypes, user32.MonitorFromWindow.restype = [wt.HWND, wt.DWORD], wt.HMONITOR
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.IsZoomed.argtypes = [wt.HWND]
    user32.IsWindow.argtypes = [wt.HWND]
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p


@contextlib.contextmanager
def _dpi_aware():
    """Real pixel coordinates on every monitor, even with different scaling (just for this thread, just for now)."""
    old = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor aware v2
    try:
        yield
    finally:
        if old:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))


# ---------------------------------------------------------------- monitors
def monitors():
    """[{num, handle, rect, work, primary, where}] by Windows' display number. `where`: left / middle / right."""
    found = []

    def visit(handle, _dc, _rect, _):
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        user32.GetMonitorInfoW(handle, ctypes.byref(info))
        r, w = info.rcMonitor, info.rcWork
        m = re.search(r"(\d+)$", info.szDevice)
        found.append({"num": int(m[1]) if m else 0, "handle": handle, "primary": bool(info.dwFlags & 1),
                      "rect": (r.left, r.top, r.right, r.bottom), "work": (w.left, w.top, w.right, w.bottom)})
        return True

    with _dpi_aware():
        user32.EnumDisplayMonitors(None, None, _MonitorEnum(visit), 0)
    return _label(found)


def _label(found):
    """Number them (Windows' numbers when they're distinct) and say where each one sits."""
    if len({m["num"] for m in found}) != len(found) or 0 in {m["num"] for m in found}:
        for i, m in enumerate(sorted(found, key=lambda m: (not m["primary"], m["rect"][0])), 1):
            m["num"] = i
    by_x = sorted(found, key=lambda m: m["rect"][0])
    for i, m in enumerate(by_x):
        m["where"] = ("left" if i == 0 else "right" if i == len(by_x) - 1 else "middle") if len(by_x) > 1 else "only"
    return sorted(found, key=lambda m: m["num"])


def _describe(m):
    w, h = m["rect"][2] - m["rect"][0], m["rect"][3] - m["rect"][1]
    tags = [t for t in ("primary" if m["primary"] else "", m["where"] if m["where"] != "only" else "",
                        "portrait" if h > w else "") if t]
    return f"monitor {m['num']} ({', '.join(tags + [f'{w}x{h}'])})"


def _monitor_of(hwnd, mons):
    handle = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    return next((m for m in mons if m["handle"] == handle), None)


def pick_monitor(said, mons, current=None, here=None):
    """What they said about a monitor -> (monitor, None) or (None, why not). `current`: where the window is now;
    `here`: the monitor of the window in front."""
    text = str(said).lower().strip()
    text = re.sub(r"\b(this|that|other|next|another|different|left|right|middle|main|primary|big|small)\s+one\b", r"\1", text)
    text = re.sub(r"\b(my|the|monitor|monitors|screen|screens|display|displays)\b", " ", text).strip()
    listing = "; ".join(_describe(m) for m in mons)
    if len(mons) < 2:
        return None, "FAILED: this PC only has one monitor, so there's nowhere to move it."
    num = int(m[0]) if (m := re.search(r"\d+", text)) else next((n for w, n in ORDINALS.items() if re.search(rf"\b{w}\b", text)), None)
    if num is not None:
        hit = next((x for x in mons if x["num"] == num), None)
        return (hit, None) if hit else (None, f"FAILED: there's no monitor {num}. This PC has {len(mons)}: {listing}.")
    if re.search(r"\b(primary|main)\b", text):
        return next(x for x in mons if x["primary"]), None
    for where in ("left", "right", "middle"):
        if re.search(rf"\b{where}", text) or (where == "middle" and "center" in text):
            hits = [x for x in mons if x["where"] == where]
            if hits:
                return hits[0], None
    if re.search(r"\b(here|this|current|in front)\b", text):
        return (here, None) if here else (None, "FAILED: couldn't tell which monitor you're on.")
    if re.search(r"\b(other|next|another|different)\b", text) or not text:
        if current is None:
            return mons[0], None
        later = [x for x in mons if x["num"] > current["num"]]
        return (later[0] if later else mons[0]), None
    return None, f"FAILED: not sure which monitor '{said}' is. This PC has {len(mons)}: {listing}."


# ---------------------------------------------------------------- which window
def _class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _entry_for_pid(pid):
    """A running process -> the app it belongs to (for its name)."""
    try:
        pname = apps._psutil().Process(pid).name().lower()
    except Exception:
        return {"name": "that window", "id": "", "exe": "", "link": "", "procs": []}
    entries = apps.apps()["entries"]
    hit = next((e for e in entries if pname in e.get("procs", [])), None) or next(
        (e for e in entries if not e.get("procs") and apps._matches(e, pname)), None)
    return hit or {"name": Path(pname).stem.title(), "id": "", "exe": "", "link": "", "procs": [pname]}


def front_window():
    hwnd = user32.GetForegroundWindow()
    if not hwnd or _class(hwnd) in DESKTOP_CLASSES:
        return None
    return hwnd


def target(app):
    """-> (entry, hwnd, None) or (None, None, tool result)."""
    said = str(app or "").strip()
    if apps.norm(said) in {apps.norm(x) for x in THIS} or said.lower() in THIS:
        hwnd = front_window()
        if not hwnd:
            return None, None, "FAILED: no app window is in front right now (it's the desktop). Ask which app they mean."
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return _entry_for_pid(pid.value), hwnd, None
    entry, problem = apps.resolve(said)
    if problem:
        return None, None, problem
    wins = apps.app_windows(entry)
    if not wins and apps.processes(entry):
        apps.focus_entry(entry)  # only in the tray: ask it to show its window
        wins = apps.app_windows(entry)
    if not wins:
        return None, None, f"FAILED: {entry['name']} isn't open, so there's no window to change. Offer to open it."
    return entry, wins[0][0], None


def _state(hwnd):
    return "minimized" if user32.IsIconic(hwnd) else "maximized" if user32.IsZoomed(hwnd) else "normal"


def _wait(check, seconds=1.5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return check()


# ---------------------------------------------------------------- tools
def _windows_only():
    return None if IS_WINDOWS else "UNAVAILABLE: window control only works on Windows."


def minimize_window(app):
    if (no := _windows_only()):
        return no
    entry, hwnd, problem = target(app)
    if problem:
        return problem
    if user32.IsIconic(hwnd):
        apps._remember(entry, "minimized")
        return f"OK: {entry['name']} was already minimized."
    user32.ShowWindow(hwnd, SW_MINIMIZE)
    if not _wait(lambda: user32.IsIconic(hwnd)):
        return f"FAILED: tried to minimize {entry['name']}, but its window is still up."
    apps._remember(entry, "minimized")
    return f"OK: {entry['name']} is minimized."


def maximize_window(app):
    if (no := _windows_only()):
        return no
    entry, hwnd, problem = target(app)
    if problem:
        return problem
    with _dpi_aware():
        if not user32.IsZoomed(hwnd):
            user32.ShowWindow(hwnd, SW_MAXIMIZE)
        apps.bring_to_front(hwnd)
        if not _wait(lambda: user32.IsZoomed(hwnd) and not user32.IsIconic(hwnd)):
            return f"FAILED: tried to maximize {entry['name']}, but it isn't maximized (some apps don't allow it)."
        where = _monitor_of(hwnd, monitors())
    apps._remember(entry, "maximized")
    return f"OK: {entry['name']} is maximized" + (f" on {_describe(where)}." if where and len(monitors()) > 1 else ".")


def restore_window(app):
    if (no := _windows_only()):
        return no
    entry, hwnd, problem = target(app)
    if problem:
        return problem
    if _state(hwnd) == "normal":
        apps.bring_to_front(hwnd)
        apps._remember(entry, "restored")
        return f"OK: {entry['name']} was already a normal-sized window; it's in front now."
    was = _state(hwnd)
    user32.ShowWindow(hwnd, SW_RESTORE)  # (minimized: back to how it was, maybe maximized; maximized: normal size)
    done = (lambda: not user32.IsIconic(hwnd)) if was == "minimized" else (lambda: _state(hwnd) == "normal")
    if not _wait(done):
        return f"FAILED: tried to restore {entry['name']}, but it's still {_state(hwnd)}."
    apps.bring_to_front(hwnd)
    apps._remember(entry, "restored")
    if was == "minimized":
        return f"OK: {entry['name']} is back up" + (" (maximized, as it was)." if user32.IsZoomed(hwnd) else ", in front.")
    return f"OK: {entry['name']} is back to a normal-sized window, in front."


def focus_window(app):
    if (no := _windows_only()):
        return no
    entry, hwnd, problem = target(app)
    if problem:
        return problem
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if not apps.bring_to_front(hwnd):
        return f"FAILED: {entry['name']} is open, but Windows wouldn't bring it to the front."
    apps._remember(entry, "focused")
    return f"OK: {entry['name']} is in front now."


def move_window_to_monitor(app, monitor):
    if (no := _windows_only()):
        return no
    here_hwnd = front_window()  # (before anything changes: "here" is where they're working now)
    entry, hwnd, problem = target(app)
    if problem:
        return problem
    name = entry["name"]
    with _dpi_aware():
        mons = monitors()
        current = _monitor_of(hwnd, mons)
        here = _monitor_of(here_hwnd, mons) if here_hwnd and here_hwnd != hwnd else None
        if here is None and here_hwnd == hwnd and re.search(r"\b(here|this|current)\b", str(monitor).lower()):
            here = current  # it IS the window in front: "here" is where it already is
        dest, problem = pick_monitor(monitor, mons, current, here)
        if problem:
            return problem
        if current and dest["handle"] == current["handle"]:
            apps.bring_to_front(hwnd)
            apps._remember(entry, "moved")
            return f"OK: {name} is already on {_describe(dest)}; it's in front now."
        was_max = bool(user32.IsZoomed(hwnd))
        if user32.IsIconic(hwnd) or was_max:
            user32.ShowWindow(hwnd, SW_RESTORE)  # (a maximized window is moved as a normal one, then maximized again)
            _wait(lambda: _state(hwnd) == "normal", 1.0)
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        wl, wtop, wr, wb = dest["work"]
        w, h = min(r.right - r.left, wr - wl), min(r.bottom - r.top, wb - wtop)
        x, y = wl + (wr - wl - w) // 2, wtop + (wb - wtop - h) // 2  # centered on the new monitor
        user32.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
        if was_max:
            user32.ShowWindow(hwnd, SW_MAXIMIZE)
        moved = _wait(lambda: user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) == dest["handle"])
        apps.bring_to_front(hwnd)
        if not moved:
            now = _monitor_of(hwnd, mons)
            return f"FAILED: tried to move {name} to {_describe(dest)}, but it's still on " + (
                _describe(now) if now else "the same monitor") + "."
    apps._remember(entry, "moved")
    return f"OK: {name} is on {_describe(dest)} now" + (", still maximized." if was_max and user32.IsZoomed(hwnd) else ".")


def get_active_window():
    if (no := _windows_only()):
        return no
    hwnd = front_window()
    if not hwnd:
        return "OK: no app window is in front right now (the desktop is)."
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    entry = _entry_for_pid(pid.value)
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    with _dpi_aware():
        mons = monitors()
        where = _monitor_of(hwnd, mons)
    title = f" (\"{buf.value[:80]}\")" if buf.value and buf.value != entry["name"] else ""
    return (f"OK: {entry['name']}{title} is in front, {_state(hwnd)}"
            + (f", on {_describe(where)}." if where and len(mons) > 1 else "."))


def list_monitors():
    if (no := _windows_only()):
        return no
    mons = monitors()
    return f"OK: {len(mons)} monitor{'s' if len(mons) != 1 else ''}: " + "; ".join(_describe(m) for m in mons) + "."
