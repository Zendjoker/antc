"""System-tray icon with its own hidden window, so the launcher can outlive the visible UI."""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from pathlib import Path

WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_TRAY = 0x8001
NIF_MESSAGE = 0x1
NIF_ICON = 0x2
NIF_TIP = 0x4
NIF_INFO = 0x10
NIM_ADD = 0x0
NIM_MODIFY = 0x1
NIM_DELETE = 0x2
MF_STRING = 0x0
TPM_RIGHTALIGN = 0x8
TPM_BOTTOMALIGN = 0x20
LR_LOADFROMFILE = 0x10
IMAGE_ICON = 1
WM_QUIT = 0x0012
HWND_MESSAGE = -3

MENU = (
    (1, "Open Dashboard"),
    (2, "Start All"),
    (3, "Restart All"),
    (4, "Restart Backend"),
    (5, "Restart Frontend"),
    (6, "Stop All"),
    (7, "View Logs"),
    (8, "Exit Launcher"),
)


class TrayIcon:
    def __init__(self, icon_path: Path, on_action):
        self.icon_path = Path(icon_path)
        self.on_action = on_action
        self.hwnd = None
        self._icon = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="zend-tray", daemon=True)

    def start(self) -> bool:
        if __import__("os").name != "nt":
            return False
        TrayIcon._callback = self.on_action
        self._thread.start()
        return self._ready.wait(3) and bool(self.hwnd)

    def notify(self, text: str) -> None:
        if not self.hwnd:
            return
        self._notify(self.hwnd, text)

    def stop(self) -> None:
        user32 = ctypes.windll.user32
        if self.hwnd:
            _icon(self.hwnd, NIM_DELETE, self._icon, "")
            user32.PostMessageW(self.hwnd, WM_QUIT, 0, 0)

    def _run(self) -> None:
        try:
            user32 = _user32()
            class_name = _register()
            self.hwnd = user32.CreateWindowExW(
                0, class_name, "ZendAgent", 0,
                0, 0, 0, 0, HWND_MESSAGE, None, None, None,
            )
            if not self.hwnd:
                return
            self._icon = _load_icon(self.icon_path)
            if not _icon(self.hwnd, NIM_ADD, self._icon, "ZendAgent"):
                self.hwnd = None
                return
            self._ready.set()
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
            _icon(self.hwnd, NIM_DELETE, self._icon, "")
        finally:
            self._ready.set()

    def _notify(self, hwnd, text: str) -> None:
        data = _data(hwnd, self._icon, "ZendAgent")
        data.uFlags |= NIF_INFO
        data.szInfo = text[:255]
        data.szInfoTitle = "ZendAgent"
        data.dwInfoFlags = 1
        shell = _shell32()
        shell.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))


_PROC = None
_CLASS = None


class _WndClass(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


def _user32():
    user32 = ctypes.windll.user32
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.RegisterClassW.argtypes = [ctypes.POINTER(_WndClass)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    return user32


def _register():
    global _PROC, _CLASS
    user32 = _user32()
    wndproc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    def proc(hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                _dispatch("show")
            elif lparam == WM_RBUTTONUP:
                _popup(hwnd)
            return 0
        if msg == WM_COMMAND:
            action = {item[0]: item[1] for item in MENU}.get(wparam & 0xFFFF)
            if action:
                _dispatch(action)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    _PROC = wndproc(proc)
    klass = _WndClass()
    klass.lpfnWndProc = ctypes.cast(_PROC, ctypes.c_void_p)
    klass.lpszClassName = "ZendAgentLauncherTray"
    klass.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
    atom = user32.RegisterClassW(ctypes.byref(klass))
    _CLASS = klass
    if not atom and ctypes.windll.kernel32.GetLastError() != 1410:  # class already registered
        raise OSError("tray window class was not registered")
    return klass.lpszClassName


def _dispatch(action: str) -> None:
    # The tray module stores the latest callback on the class object via a closure set in start.
    callback = getattr(TrayIcon, "_callback", None)
    if callback:
        callback(action)


def _popup(hwnd) -> None:
    user32 = ctypes.windll.user32
    menu = user32.CreatePopupMenu()
    for ident, label in MENU:
        user32.AppendMenuW(menu, MF_STRING, ident, label)
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    user32.SetForegroundWindow(hwnd)
    user32.TrackPopupMenu(menu, TPM_RIGHTALIGN | TPM_BOTTOMALIGN, point.x, point.y, 0, hwnd, None)
    user32.PostMessageW(hwnd, WM_NULL, 0, 0)
    user32.DestroyMenu(menu)


class _NotifyData(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


def _data(hwnd, icon, tip: str) -> _NotifyData:
    data = _NotifyData()
    data.cbSize = ctypes.sizeof(_NotifyData)
    data.hWnd = hwnd
    data.uID = 1
    data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    data.uCallbackMessage = WM_TRAY
    data.hIcon = icon
    data.szTip = tip[:127]
    return data


def _shell32():
    shell32 = ctypes.windll.shell32
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NotifyData)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    return shell32


def _icon(hwnd, action, icon, tip: str) -> bool:
    if not hwnd:
        return False
    data = _data(hwnd, icon, tip or "ZendAgent")
    return bool(_shell32().Shell_NotifyIconW(action, ctypes.byref(data)))


def _load_icon(path: Path):
    if not path.is_file():
        return ctypes.windll.user32.LoadIconW(None, 32512)  # IDI_APPLICATION
    return ctypes.windll.user32.LoadImageW(None, str(path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE)


# The tray thread calls TrayIcon._callback. app.py assigns it before start().
TrayIcon._callback = None
