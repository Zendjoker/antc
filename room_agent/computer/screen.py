"""On-demand screenshots, only when they ask about the screen, checked for privacy and freshness.

    capture()     the window in front by default (not every monitor), in physical pixels (DPI-aware), scaled down to
                  at most MAX_SIDE for the vision model, PNG-encoded in memory (numpy + zlib, nothing written to disk)
    private()     refuse windows that look private: password managers, sign-in / 2FA pages, banking and payment pages,
                  private messages; and any window whose focused field is a password box
    fresh()       a screenshot is only used while it's recent AND the same window is still in front at the same place;
                  coordinates from an old or moved screenshot are never clicked

Permission (SCREEN_VISION in .env): off = never; ask (default) = once, the first time, they're asked whether screenshots
may go to the vision model; allow = whenever they ask about the screen. Never continuous, never stored.
"""

import ctypes
import os
import re
import struct
import time
import zlib
from dataclasses import dataclass

IS_WINDOWS = os.name == "nt"
MAX_SIDE = 1568           # (the vision models' sweet spot: larger is scaled down by them anyway, costing more)
FRESH_S = 8.0
PRIVATE_APPS = re.compile(r"1password|bitwarden|keepass|lastpass|dashlane|nordpass|keeper|authenticator|authy|"
                          r"whatsapp|signal|telegram|messenger|wechat|imessage|outlook mail", re.I)
PRIVATE_TITLES = re.compile(r"password|passcode|sign[ -]?in|log[ -]?in|verification code|two[- ]factor|2fa|one[- ]time|"
                            r"\bbank|banking|paypal|checkout|payment|credit card|wallet|private browsing|incognito|inprivate|"
                            r"web\.whatsapp|messages? -|inbox \(|\bdm\b|direct messages?", re.I)
PRIVATE_HOSTS = re.compile(r"(^|\.)(mail\.google|outlook\.live|outlook\.office|web\.whatsapp|messenger|"
                           r"accounts\.google|login\.|signin\.|auth\.|paypal|bank)", re.I)


@dataclass
class Shot:
    png: bytes
    width: int               # image size sent to the model
    height: int
    scale: float             # image pixels per screen pixel (<= 1)
    left: int                # screen position of the image's top-left (physical pixels)
    top: int
    hwnd: int
    title: str
    rect: tuple              # window rect at capture time
    at: float


def foreground():
    return ctypes.windll.user32.GetForegroundWindow() if IS_WINDOWS else 0


def front_info():
    """{"hwnd", "title", "process", "url"} of the window in front (url only for a browser)."""
    from room_agent.computer import browsers

    hwnd = foreground()
    info = {"hwnd": hwnd, "title": browsers.title_of(hwnd) if hwnd else "", "process": "", "url": ""}
    if hwnd:
        try:
            import psutil

            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            info["process"] = psutil.Process(pid.value).exe()
        except Exception:
            pass
        if browsers.key_for_exe(info["process"]):
            info["url"] = browsers.read_state(hwnd)["url"]
    return info


# ---------------------------------------------------------------- privacy
def private(hwnd, title, process="", url=""):
    """-> the reason this window shouldn't be captured, or ''."""
    if PRIVATE_APPS.search(process or "") or PRIVATE_APPS.search(title or ""):
        return "it's a password manager or a private messaging app"
    if PRIVATE_TITLES.search(title or ""):
        return "it looks like a sign-in, payment, banking or private page"
    if url:
        from room_agent.computer.browsers import host

        if PRIVATE_HOSTS.search(host(url)):
            return "it's a mail, messaging, sign-in or banking site"
    try:
        from room_agent.computer import uia

        f = uia.focused()
        if f is not None and f.password:
            return "a password field has the focus"
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------- capture (GDI, DPI-aware)
def window_rect(hwnd):
    from ctypes import wintypes as wt

    r = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def _grab(left, top, width, height):
    """Raw RGB pixels (numpy, height x width x 3) of a screen area."""
    import numpy as np
    from ctypes import wintypes as wt

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC, ctypes.c_int,
                             ctypes.c_int, wt.DWORD]
    gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    user32.GetDC.restype = wt.HDC
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, width, height)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, width, height, screen, left, top, 0x00CC0020 | 0x40000000):  # SRCCOPY|CAPTUREBLT
            raise OSError("screen copy failed")

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                        ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                        ("biClrImportant", wt.DWORD)]

        hdr = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = (ctypes.c_ubyte * (width * height * 4))()
        if not gdi32.GetDIBits(mem, bmp, 0, height, buf, ctypes.byref(hdr), 0):
            raise OSError("reading the screen copy failed")
        bgra = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 4)
        return bgra[:, :, 2::-1].copy()
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)


def downscale(rgb, max_side=MAX_SIDE):
    """-> (image, scale). Area-averaged by an integer factor, then nearest for the remainder (no Pillow needed)."""
    import numpy as np

    h, w = rgb.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale >= 1.0:
        return rgb, 1.0
    f = int(1 / scale)
    if f >= 2:
        hh, ww = (h // f) * f, (w // f) * f
        rgb = rgb[:hh, :ww].reshape(hh // f, f, ww // f, f, 3).mean(axis=(1, 3)).astype(np.uint8)
    h2, w2 = rgb.shape[:2]
    tw, th = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    if (w2, h2) != (tw, th):
        ys = (np.arange(th) * h2 / th).astype(int)
        xs = (np.arange(tw) * w2 / tw).astype(int)
        rgb = rgb[ys][:, xs]
    return rgb, tw / w


def png(rgb):
    """Encode an RGB numpy image as PNG bytes."""
    h, w = rgb.shape[:2]
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def capture(scope="window", hwnd=None):
    """A fresh screenshot of the window in front (or `scope`='screen': its whole monitor). -> Shot."""
    from room_agent.tools import window_control as wc

    with wc._dpi_aware():
        hwnd = hwnd or ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            raise OSError("no window is in front")
        rect = window_rect(hwnd)
        if scope == "screen":
            mon = wc._monitor_of(hwnd, wc.monitors())
            if mon:
                rect = tuple(mon["rect"]) if "rect" in mon else rect
        left, top, right, bottom = rect
        vl, vt = ctypes.windll.user32.GetSystemMetrics(76), ctypes.windll.user32.GetSystemMetrics(77)
        vr = vl + ctypes.windll.user32.GetSystemMetrics(78)
        vb = vt + ctypes.windll.user32.GetSystemMetrics(79)
        left, top, right, bottom = max(left, vl), max(top, vt), min(right, vr), min(bottom, vb)  # (on screen only)
        if right - left < 10 or bottom - top < 10:
            raise OSError("the window is minimized or off screen")
        rgb = _grab(left, top, right - left, bottom - top)
    small, scale = downscale(rgb)
    from room_agent.computer.browsers import title_of

    return Shot(png=png(small), width=small.shape[1], height=small.shape[0], scale=scale, left=left, top=top, hwnd=hwnd,
                title=title_of(hwnd), rect=window_rect(hwnd), at=time.time())


# ---------------------------------------------------------------- freshness and coordinates
def fresh(shot, max_age=FRESH_S):
    """-> '' if the screenshot still shows what's on screen, else why not."""
    if shot is None:
        return "there's no screenshot"
    if time.time() - shot.at > max_age:
        return f"the screenshot is {time.time() - shot.at:.0f}s old"
    if foreground() != shot.hwnd:
        return "a different window is in front now"
    if window_rect(shot.hwnd) != shot.rect:
        return "the window moved or changed size"
    return ""


def to_screen(shot, x, y):
    """Image pixel (from the vision model) -> physical screen pixel."""
    return int(round(shot.left + x / shot.scale)), int(round(shot.top + y / shot.scale))
