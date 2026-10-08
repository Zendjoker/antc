"""Windows settings, offline: the registry, monitors, radios, lock and power are simulated. Nothing on this PC changes.

Run:  .venv\\Scripts\\python -m tests.test_pcsettings
"""

from tests.harness import setup_env

setup_env()

from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor  # noqa: E402
from room_agent.tools import pcsettings as pc  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

t = Checker()
core.ensure_loaded()

reg = {"apps_light": 1, "system_light": 1}
pc.theme = lambda: dict(reg)
pc._write_theme = lambda a, s: reg.update(apps_light=a, system_light=s)
monitors = {1: [80, 0, 100], 2: [58, 0, 100], 3: None}  # (monitor 3 doesn't allow DDC/CI)
pc._physical_monitors = lambda: [(h, f"Monitor {h}") for h in monitors]
pc._release = lambda pms: None
pc._ddc_get = lambda h: tuple(monitors[h]) if monitors[h] else None
pc._ddc_set = lambda h, v: (monitors[h].__setitem__(0, v), True)[1] if monitors[h] else False
pc._wmi = lambda cmd: ""
radio = {"wifi": True, "bluetooth": True}
allowed = {"ok": True}
pc.radios = lambda: dict(radio)


convo = Conversation()


def run(name, args, said):
    rt.new_turn(said)
    return executor.execute(name, args)


print("Dark mode:")
convo.say("turn on dark mode", reply="SHOULD NOT BE NEEDED")
t.check("'turn on dark mode' -> instant, read back", reg == {"apps_light": 0, "system_light": 0} and not convo.requests, reg)
r = run("set_dark_mode", {"on": True}, "dark mode on")
t.check("already on -> says so, nothing changed", "already" in r.message)
r = run("undo_last_action", {}, "undo that")
t.check("'undo that' -> back to light", reg == {"apps_light": 1, "system_light": 1}, (r.message, reg))

print("Brightness:")
r = run("set_brightness", {"percent": 40}, "brightness to 40")
t.check("an exact percent -> every screen that allows it, read back", r.success and monitors[1][0] == 40 and monitors[2][0] == 40,
        r.message)
t.check("...and it says one screen can't be changed by apps", "1 other screen can't" in r.message, r.message)
r = run("set_brightness", {"change": 20}, "make the screen brighter")
t.check("'brighter' -> +20 on each", monitors[1][0] == 60 and monitors[2][0] == 60, monitors)
r = run("set_brightness", {"change": 90}, "max brightness please")
t.check("never past 100", monitors[1][0] == 100)
pc._ddc_set = lambda h, v: True  # (the monitor says yes but nothing changes)
r = run("set_brightness", {"percent": 10}, "brightness to 10")
t.check("a monitor that ignores the change -> 'not confirmed'", not r.success and "not confirmed" in r.message, r.message)

print("Wi-Fi and Bluetooth:")


def apply_radio(which, on):
    def run_async(make):
        if not allowed["ok"]:
            return "denied (2)"
        radio[which] = on
        return "ok"
    pc._run_async = run_async


apply_radio("bluetooth", False)
r = run("set_bluetooth", {"on": "off"}, "turn off bluetooth")
t.check("'turn off bluetooth' -> off, read back ('off' really means off)", r.success and radio["bluetooth"] is False, r.message)
apply_radio("bluetooth", True)
r = run("undo_last_action", {}, "undo that")
t.check("'undo' -> Bluetooth back on", radio["bluetooth"] is True, r.message)
allowed["ok"] = False
r = run("set_bluetooth", {"on": False}, "turn off bluetooth")
t.check("Windows doesn't allow apps to switch radios -> says where to allow it, nothing claimed", not r.success
        and "Privacy > Radios" in r.message, r.message)
allowed["ok"] = True
apply_radio("wifi", False)
r = run("set_wifi", {"on": False, "confidence": 0.5}, "maybe turn off the wifi")
t.check("Wi-Fi off when unsure -> asks first (it cuts Jarvis's internet)", r.message.startswith("NEEDS_CONFIRMATION")
        and radio["wifi"] is True, r.message)
rt.pending = None
r = run("set_wifi", {"on": False, "confidence": 0.95}, "turn off the wifi")
t.check("...clearly asked -> off, with the warning about Jarvis's internet", r.success and "internet" in r.message)

print("Lock, sleep, power:")
import ctypes  # noqa: E402

locked = []
ctypes.windll.user32.LockWorkStation = lambda: (locked.append(1), 1)[1]
r = run("lock_pc", {"confidence": 0.95}, "lock the pc")
t.check("'lock the PC' -> locked", r.success and locked)
r = run("sleep_pc", {}, "put the computer to sleep")
t.check("sleep -> asks first (always)", r.message.startswith("NEEDS_CONFIRMATION"), r.message)
rt.pending = None
ran = []
pc.subprocess.run = lambda cmd, **kw: (ran.append(cmd), type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())[1]
r = run("shutdown_pc", {"action": "shutdown"}, "shut down the computer")
t.check("shut down -> asks first, nothing scheduled", r.message.startswith("NEEDS_CONFIRMATION") and not ran, r.message)
rt.pending = None
t.check("when confirmed, it waits 60 s and can be cancelled", pc.power("shutdown").endswith("to stop it.") and ran[-1][:4] == [
    "shutdown", "/s", "/t", "60"])
r = run("cancel_shutdown", {}, "cancel the shutdown")
t.check("'cancel the shutdown' -> shutdown /a", r.success and ran[-1] == ["shutdown", "/a"])
opened = []
pc.os.startfile = lambda u: opened.append(u)
r = run("open_settings_page", {"page": "do not disturb"}, "turn on do not disturb")
t.check("do-not-disturb -> the settings page opens, and it says plainly they have to flip it", r.success
        and opened == ["ms-settings:notifications"] and "need to click it" in r.message, r.message)
t.done("PC SETTINGS")
