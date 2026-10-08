"""Keyboard, mouse, window focus and clipboard (Win32 SendInput). Every function says whether it was SENT; whether it
WORKED is for the caller to check (an address change, a value read back...).
"""

import ctypes
import os
import time
from ctypes import wintypes as wt

IS_WINDOWS = os.name == "nt"
VK = {"back": 0x08, "tab": 0x09, "enter": 0x0D, "shift": 0x10, "ctrl": 0x11, "alt": 0x12, "escape": 0x1B, "esc": 0x1B,
      "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27,
      "down": 0x28, "delete": 0x2E, "f5": 0x74, "f6": 0x75, "win": 0x5B,
      **{chr(c).lower(): c for c in range(0x41, 0x5B)}, **{str(d): 0x30 + d for d in range(10)}}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2E}
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_WHEEL, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = (
    0x1, 0x2, 0x4, 0x800, 0x8000, 0x4000)
CF_UNICODETEXT = 13

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("u", _U)]

    user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.GetForegroundWindow.restype = wt.HWND
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
    user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    user32.IsIconic.argtypes = [wt.HWND]
    kernel32.GlobalAlloc.restype = wt.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
    user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    user32.SetClipboardData.restype = wt.HANDLE
    user32.GetClipboardData.restype = wt.HANDLE
    user32.OpenClipboard.argtypes = [wt.HWND]


def _send(items):
    arr = (INPUT * len(items))(*items)
    return user32.SendInput(len(items), arr, ctypes.sizeof(INPUT)) == len(items)


def _key(vk, up=False):
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0)
    return INPUT(INPUT_KEYBOARD, _U(ki=KEYBDINPUT(vk, 0, flags, 0, 0)))


def parse_combo(combo):
    """'ctrl+shift+t' -> [vk...], or None if a key isn't known."""
    keys = [k.strip().lower() for k in str(combo).replace(" ", "").split("+") if k.strip()]
    keys = ["ctrl" if k in ("control", "ctl") else "escape" if k == "esc" else "enter" if k == "return" else k for k in keys]
    vks = [VK.get(k) for k in keys]
    return None if not vks or None in vks else vks


def hotkey(combo):
    """Press a key combination ('alt+left', 'f5', 'ctrl+t'). -> True if sent."""
    vks = parse_combo(combo)
    if not vks:
        return False
    return _send([_key(v) for v in vks] + [_key(v, up=True) for v in reversed(vks)])


def type_text(text, delay=0.0):
    """Type Unicode text into whatever has the keyboard focus. -> True if sent."""
    items = []
    for ch in str(text):
        if ch == "\n":
            items += [_key(VK["enter"]), _key(VK["enter"], up=True)]
            continue
        code = ord(ch)
        units = [code] if code < 0x10000 else [0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF)]
        for u in units:
            items.append(INPUT(INPUT_KEYBOARD, _U(ki=KEYBDINPUT(0, u, KEYEVENTF_UNICODE, 0, 0))))
            items.append(INPUT(INPUT_KEYBOARD, _U(ki=KEYBDINPUT(0, u, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0))))
    if not delay:
        return _send(items) if items else True
    ok = True
    for i in range(0, len(items), 2):
        ok &= _send(items[i:i + 2])
        time.sleep(delay)
    return ok


def _virtual_screen():
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77), user32.GetSystemMetrics(78) or 1,
            user32.GetSystemMetrics(79) or 1)


def click_at(x, y):
    """Left-click at physical screen coordinates (the vision fallback only). -> True if sent."""
    left, top, w, h = _virtual_screen()
    ax, ay = int((x - left) * 65535 / max(w - 1, 1)), int((y - top) * 65535 / max(h - 1, 1))
    flags = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    return _send([INPUT(INPUT_MOUSE, _U(mi=MOUSEINPUT(ax, ay, 0, flags | MOUSEEVENTF_MOVE, 0, 0))),
                  INPUT(INPUT_MOUSE, _U(mi=MOUSEINPUT(ax, ay, 0, flags | MOUSEEVENTF_LEFTDOWN, 0, 0))),
                  INPUT(INPUT_MOUSE, _U(mi=MOUSEINPUT(ax, ay, 0, flags | MOUSEEVENTF_LEFTUP, 0, 0)))])


def wheel(clicks):
    """Mouse wheel where the pointer is: positive = up. -> True if sent."""
    return _send([INPUT(INPUT_MOUSE, _U(mi=MOUSEINPUT(0, 0, ctypes.c_ulong(int(clicks * 120)).value, MOUSEEVENTF_WHEEL, 0, 0)))])


def foreground():
    return user32.GetForegroundWindow() if IS_WINDOWS else None


def focus_window(hwnd, wait=1.5):
    """Bring a window to the front. -> True only if it really is in front afterwards."""
    if not hwnd:
        return False
    if foreground() == hwnd:
        return True
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = foreground()
    me = kernel32.GetCurrentThreadId()
    other = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(other) and other != me and user32.AttachThreadInput(me, other, True)
    try:
        hotkey("alt")  # (Windows only lets the process that got the last input change the foreground window)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, other, False)
    deadline = time.time() + wait
    while time.time() < deadline:
        if foreground() == hwnd:
            return True
        time.sleep(0.05)
    return foreground() == hwnd


def set_clipboard(text):
    data = ctypes.create_unicode_buffer(str(text))
    size = ctypes.sizeof(data)
    for _ in range(5):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(0x0042, size)  # GMEM_MOVEABLE | GMEM_ZEROINIT
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, size)
        kernel32.GlobalUnlock(h)
        return bool(user32.SetClipboardData(CF_UNICODETEXT, h))
    finally:
        user32.CloseClipboard()


def get_clipboard():
    if not user32.OpenClipboard(None):
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()
