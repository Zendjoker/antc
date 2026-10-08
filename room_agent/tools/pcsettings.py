"""Windows settings by voice: dark mode, monitor brightness, Wi-Fi / Bluetooth, lock, sleep, shut down / restart.

Every change is read back before it's called done (the theme setting, each monitor's brightness, the radio's state).
    dark mode    the per-user theme setting (apps and Windows), with the change broadcast so open windows follow
    brightness   external monitors over DDC/CI (most modern monitors), the laptop screen through WMI
    Wi-Fi / BT   Windows' radio switches (the same as the quick settings buttons)
    lock         now; sleep / shut down / restart only after a yes (shut down and restart wait 60 s and can be cancelled)
Not available to apps (Windows has no supported way): do-not-disturb / focus and night light. For those the settings page
is opened instead, and it's said plainly that they have to flip the switch.
"""

import asyncio
import ctypes
import os
import subprocess
import threading
import time

IS_WINDOWS = os.name == "nt"
PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
SETTINGS_PAGES = {"do not disturb": "ms-settings:notifications", "focus": "ms-settings:focus",
                  "night light": "ms-settings:nightlight", "display": "ms-settings:display",
                  "bluetooth": "ms-settings:bluetooth", "wifi": "ms-settings:network-wifi", "sound": "ms-settings:sound"}


# ---------------------------------------------------------------- dark mode
def theme():
    """{"apps_light": 0/1, "system_light": 0/1}"""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE) as k:
        return {"apps_light": winreg.QueryValueEx(k, "AppsUseLightTheme")[0],
                "system_light": winreg.QueryValueEx(k, "SystemUsesLightTheme")[0]}


def _write_theme(apps_light, system_light):
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "AppsUseLightTheme", 0, winreg.REG_DWORD, int(apps_light))
        winreg.SetValueEx(k, "SystemUsesLightTheme", 0, winreg.REG_DWORD, int(system_light))
    _broadcast()


def _broadcast():
    """Tell open windows the theme changed (WM_SETTINGCHANGE 'ImmersiveColorSet')."""
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, ctypes.c_wchar_p("ImmersiveColorSet"), 0x0002, 2000,
                                             ctypes.byref(result))


def dark_mode(on):
    on = bool(on)
    before = theme()
    if before["apps_light"] == (0 if on else 1) and before["system_light"] == (0 if on else 1):
        return f"OK: nothing needed: dark mode is already {'on' if on else 'off'}."
    _write_theme(0 if on else 1, 0 if on else 1)
    after = theme()
    if after["apps_light"] != (0 if on else 1):
        return "UNKNOWN: not confirmed: the theme setting didn't change."
    return f"OK: dark mode is {'on' if on else 'off'} (Windows and apps; some open apps change when you switch to them)."


# ---------------------------------------------------------------- brightness
class _PM(ctypes.Structure):
    _fields_ = [("h", ctypes.c_void_p), ("desc", ctypes.c_wchar * 128)]


def _physical_monitors():
    """[(handle, description)] of monitors; the caller destroys them (_release)."""
    from ctypes import wintypes as wt

    dxva2, user32 = ctypes.WinDLL("dxva2"), ctypes.windll.user32
    handles = []
    cb = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)(
        lambda h, dc, r, l: (handles.append(h), True)[1])
    user32.EnumDisplayMonitors(None, None, cb, 0)
    out = []
    for h in handles:
        n = wt.DWORD()
        if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(wt.HMONITOR(h), ctypes.byref(n)) or not n.value:
            continue
        arr = (_PM * n.value)()
        if dxva2.GetPhysicalMonitorsFromHMONITOR(wt.HMONITOR(h), n.value, arr):
            out += [(pm.h, pm.desc) for pm in arr]
    return out


def _release(pms):
    dxva2 = ctypes.WinDLL("dxva2")
    for h, _ in pms:
        dxva2.DestroyPhysicalMonitor(ctypes.c_void_p(h))


def _ddc_get(h):
    from ctypes import wintypes as wt

    mn, cur, mx = wt.DWORD(), wt.DWORD(), wt.DWORD()
    if ctypes.WinDLL("dxva2").GetMonitorBrightness(ctypes.c_void_p(h), ctypes.byref(mn), ctypes.byref(cur), ctypes.byref(mx)):
        return cur.value, mn.value, mx.value
    return None


def _ddc_set(h, value):
    return bool(ctypes.WinDLL("dxva2").SetMonitorBrightness(ctypes.c_void_p(h), int(value)))


def _wmi(cmd):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=15,
                       creationflags=0x08000000)
    return r.stdout.strip() if r.returncode == 0 else ""


def brightness():
    """{"monitors": [percent per controllable screen], "laptop": percent or None}"""
    pms = _physical_monitors()
    try:
        levels = []
        for h, _ in pms:
            g = _ddc_get(h)
            if g:
                cur, mn, mx = g
                levels.append(round(100 * (cur - mn) / max(mx - mn, 1)))
    finally:
        _release(pms)
    lap = _wmi("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop).CurrentBrightness")
    return {"monitors": levels, "laptop": int(lap.split()[0]) if lap[:3].strip().isdigit() else None}


def set_brightness(percent=None, step=None):
    """An exact percent, or a step up / down (e.g. +20). Every screen that can be controlled."""
    before = brightness()
    if not before["monitors"] and before["laptop"] is None:
        return ("FAILED: none of the screens let apps change their brightness (the monitors don't support DDC/CI, or "
                "it's off in their menu). Use the monitor's buttons.")
    pms = _physical_monitors()
    changed, targets = 0, []
    try:
        for h, _ in pms:
            g = _ddc_get(h)
            if not g:
                continue
            cur, mn, mx = g
            pct = round(100 * (cur - mn) / max(mx - mn, 1))
            want = max(0, min(100, int(percent) if percent is not None else pct + int(step or 0)))
            targets.append(want)
            if _ddc_set(h, mn + round(want * (mx - mn) / 100)):
                changed += 1
    finally:
        _release(pms)
    if before["laptop"] is not None:
        want = max(0, min(100, int(percent) if percent is not None else before["laptop"] + int(step or 0)))
        targets.append(want)
        _wmi(f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{want})")
        changed += 1
    time.sleep(0.3)
    after = brightness()
    now = after["monitors"] + ([after["laptop"]] if after["laptop"] is not None else [])
    if not changed or not now or all(abs(a - b) > 3 for a, b in zip(now, targets)):
        return f"UNKNOWN: not confirmed: asked the screens to change brightness, but they still read {now}."
    skipped = _monitor_count() - len(before["monitors"])
    return (f"OK: brightness is now {', '.join(f'{x}%' for x in now)} ({len(now)} screen{'s' if len(now) != 1 else ''}"
            + (f"; {skipped} other screen{'s' if skipped != 1 else ''} can't be changed by apps" if skipped > 0 else "")
            + ").")


def _monitor_count():
    pms = _physical_monitors()
    _release(pms)
    return len(pms)


# ---------------------------------------------------------------- Wi-Fi / Bluetooth
KIND = {"wifi": 1, "bluetooth": 3}


def _run_async(make):
    out = {}

    def go():
        try:
            out["v"] = asyncio.run(make())
        except Exception as e:  # noqa: BLE001 (reported)
            out["e"] = e

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(20)
    if "e" in out:
        raise out["e"]
    return out.get("v")


def radios():
    """{"wifi": True/False, "bluetooth": True/False} for the radios that exist."""
    async def get():
        from winrt.windows.devices.radios import Radio

        return [(int(r.kind), int(r.state)) for r in await Radio.get_radios_async()]

    out = {}
    for kind, state in _run_async(get):
        name = {1: "wifi", 3: "bluetooth"}.get(kind)
        if name:
            out[name] = out.get(name, False) or state == 1
    return out


def set_radio(which, on):
    which = "wifi" if "wi" in str(which).lower() else "bluetooth" if "blue" in str(which).lower() else None
    if which is None:
        return "FAILED: I can switch Wi-Fi or Bluetooth, nothing else."
    name = "Wi-Fi" if which == "wifi" else "Bluetooth"
    before = radios()
    if which not in before:
        return f"FAILED: this PC has no {name} radio."
    if before[which] == bool(on):
        return f"OK: nothing needed: {name} is already {'on' if on else 'off'}."

    async def go():
        from winrt.windows.devices.radios import Radio, RadioState

        access = await Radio.request_access_async()
        if int(access) != 1:  # RadioAccessStatus.ALLOWED
            return f"denied ({int(access)})"
        for r in await Radio.get_radios_async():
            if int(r.kind) == KIND[which]:
                await r.set_state_async(RadioState.ON if on else RadioState.OFF)
        return "ok"

    status = _run_async(go)
    if status != "ok":
        return (f"FAILED: Windows didn't allow apps to switch radios ({status}). It's in Settings > Privacy > Radios. "
                "Nothing changed.")
    time.sleep(0.5)
    if radios().get(which) != bool(on):
        return f"UNKNOWN: not confirmed: asked Windows to turn {name} {'on' if on else 'off'}, but it's still {'off' if on else 'on'}."
    warn = " (Jarvis's own internet goes with it: cloud voices and the AI won't work until it's back on)" if which == "wifi" and not on else ""
    return f"OK: {name} is {'on' if on else 'off'}{warn}."


# ---------------------------------------------------------------- lock, sleep, power
def lock():
    if not ctypes.windll.user32.LockWorkStation():
        return "FAILED: Windows didn't lock."
    return "OK: locked the PC."


def sleep():
    threading.Timer(3.0, lambda: ctypes.WinDLL("powrprof").SetSuspendState(0, 1, 0)).start()
    return "OK: the PC goes to sleep in 3 seconds (I won't hear you until it wakes)."


scheduled_power = [False]  # (a shutdown / restart Jarvis scheduled: the emergency stop cancels it)


def power(action):
    flag = {"shutdown": "/s", "restart": "/r"}.get(action)
    if not flag:
        return "FAILED: I can shut down or restart."
    r = subprocess.run(["shutdown", flag, "/t", "60", "/c", "Jarvis: as you asked. Say 'cancel the shutdown' to stop it."],
                       capture_output=True, text=True, creationflags=0x08000000)
    if r.returncode != 0:
        return f"FAILED: Windows refused ({(r.stderr or r.stdout).strip()[:120]})."
    scheduled_power[0] = True
    return f"OK: the PC will {'shut down' if action == 'shutdown' else 'restart'} in 60 seconds. Say 'cancel the shutdown' to stop it."


def cancel_power():
    r = subprocess.run(["shutdown", "/a"], capture_output=True, text=True, creationflags=0x08000000)
    scheduled_power[0] = False
    if r.returncode != 0:
        return "OK: nothing to cancel: no shutdown or restart was waiting."
    return "OK: cancelled: the PC stays on."


def open_page(what):
    uri = SETTINGS_PAGES.get(str(what).lower().strip())
    if not uri:
        return f"FAILED: I don't know the settings page for '{what}'."
    os.startfile(uri)  # noqa: S606 (a Settings page)
    return (f"OK: opened the {what} page in Settings. Windows doesn't let apps flip that switch themselves, so they need "
            "to click it there.")
