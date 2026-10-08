"""Windows settings: dark mode, brightness, Wi-Fi / Bluetooth, lock, sleep, shut down (implementation: tools/pcsettings.py)."""

import os
import re

from room_agent.abilities._kit import CONFIDENCE, NO_ARGS, params, tool
from room_agent.actions.core import Group, Risk, register_claim, register_group

IS_WINDOWS = os.name == "nt"
PC_HINTS = re.compile(r"dark mode|light mode|dark theme|brightness|brighter|dimmer|dim the (screen|monitor|display)|"
                      r"screen (brighter|darker)|wi-?fi|bluetooth|\block\b|\bsleep\b|shut ?down|restart|reboot|"
                      r"do not disturb|\bdnd\b|focus (mode|assist)|night light|settings", re.I)
register_group(Group("pc", PC_HINTS, lambda: False, "Windows settings",
                     "dark mode, screen brightness, Wi-Fi / Bluetooth, lock, sleep, shut down / restart", lambda: IS_WINDOWS,
                     rules=["- 'Dim the lights' is the room's lights (set_light); 'dim the screen / brightness' is "
                            "set_brightness. Sleep, shut down and restart always ask first. Do-not-disturb and night light "
                            "can't be switched by apps: open_settings_page and say they need to flip it."]))
register_claim("pc_settings", r"\b(dark mode|light mode|brightness|wi-?fi|bluetooth)\b.{0,25}\b(on|off|now|is|set|turned|at)\b"
                              r"|\b(locked|locking) (the |your )?(pc|computer|screen)\b|\b(shutting down|restarting)\b")


def _s():
    from room_agent.tools import pcsettings

    return pcsettings


def _theme_state(args, before=None):
    return _s().theme()


def _undo_theme(args, before, after):
    _s()._write_theme(before["apps_light"], before["system_light"])
    return "OK: put the theme back the way it was."


tool("set_dark_mode", "Turn Windows dark mode on or off (apps and Windows).", params({"on": {"type": "boolean"}}, ["on"]),
     lambda a: _s().dark_mode(a["on"]), group="pc", claim="pc_settings", observe=_theme_state, undo=_undo_theme,
     undo_if=lambda b, af: b != af,
     reflex=[(r"(?:turn|switch)\s+(?P<on>on|off)\s+dark\s+mode", {}), (r"(?:turn|switch)\s+dark\s+mode\s+(?P<on>on|off)", {}),
             (r"(?:go|switch)\s+to\s+dark\s+mode", {"on": True}), (r"(?:go|switch)\s+to\s+light\s+mode", {"on": False})],
     reflex_say=lambda r: "Done." if r.success else None)


def _bright_state(args, before=None):
    return _s().brightness()


def _undo_bright(args, before, after):
    vals = before["monitors"] + ([before["laptop"]] if before["laptop"] is not None else [])
    return _s().set_brightness(percent=round(sum(vals) / len(vals))) if vals else "FAILED: nothing to put back."


tool("set_brightness", "Screen brightness of the monitors / laptop screen: an exact percent, or a change ('brighter' = "
     "+20, 'a bit dimmer' = -10). Not the room lights.",
     params({"percent": {"type": "integer", "minimum": 0, "maximum": 100},
             "change": {"type": "integer", "minimum": -100, "maximum": 100, "description": "Relative change instead"}}),
     lambda a: _s().set_brightness(a.get("percent"), a.get("change")), group="pc", claim="pc_settings",
     observe=_bright_state, undo=_undo_bright, undo_if=lambda b, af: b != af,
     reflex=[(r"(?:set\s+)?(?:the\s+)?(?:screen\s+)?brightness\s+(?:to\s+)?(?P<percent>\d{1,3})\s*(?:%|percent)?", {})],
     reflex_say=lambda r: "Done." if r.success else None)


def _radio(which):
    def run(args):
        return _s().set_radio(which, args["on"])
    return run


def _radio_state(args, before=None):
    return _s().radios()


def _undo_radio(which):
    def undo(args, before, after):
        return _s().set_radio(which, before.get(which, True))
    return undo


tool("set_bluetooth", "Turn Bluetooth on or off.", params({"on": {"type": "boolean"}}, ["on"]), _radio("bluetooth"),
     group="pc", claim="pc_settings", observe=_radio_state, undo=_undo_radio("bluetooth"), undo_if=lambda b, af: b != af,
     intent=re.compile(r"blue ?tooth", re.I),
     reflex=[(r"(?:turn|switch)\s+(?P<on>on|off)\s+(?:the\s+)?bluetooth", {}),
             (r"(?:turn|switch)\s+(?:the\s+)?bluetooth\s+(?P<on>on|off)", {})],
     reflex_say=lambda r: "Done." if r.success else None)
tool("set_wifi", "Turn Wi-Fi on or off. Turning it off also cuts Jarvis's internet (cloud voice and AI), so only when "
     "they clearly ask.", params({"on": {"type": "boolean"}, "confidence": CONFIDENCE}, ["on"]), _radio("wifi"),
     group="pc", claim="pc_settings", observe=_radio_state, undo=_undo_radio("wifi"), undo_if=lambda b, af: b != af,
     intent=re.compile(r"wi-?fi|wireless|internet|network", re.I),
     risk=Risk.CONFIRM, min_confidence=0.85, describe=lambda a: f"turn Wi-Fi {'on' if a.get('on') else 'off'}")
tool("lock_pc", "Lock the PC now (Windows lock screen).", params({"confidence": CONFIDENCE}), lambda a: _s().lock(),
     group="pc", claim="pc_settings", risk=Risk.CONFIRM, min_confidence=0.8,
     intent=re.compile(r"\block\b", re.I), reflex=[(r"lock\s+(?:the\s+|my\s+)?(?:pc|computer|screen)", {"confidence": 0.95})],
     reflex_say=lambda r: "Locked." if r.success else None)
tool("sleep_pc", "Put the PC to sleep. Asks first.", NO_ARGS, lambda a: _s().sleep(), group="pc", claim="pc_settings",
     risk=Risk.SENSITIVE, intent=re.compile(r"\bsleep\b", re.I), describe=lambda a: "put the PC to sleep")
tool("shutdown_pc", "Shut down or restart the PC (in 60 seconds, can be cancelled). Asks first.",
     params({"action": {"type": "string", "enum": ["shutdown", "restart"]}}, ["action"]),
     lambda a: _s().power(a["action"]), group="pc", claim="pc_settings", risk=Risk.SENSITIVE,
     intent=re.compile(r"shut ?down|restart|reboot|turn off the (pc|computer)", re.I),
     describe=lambda a: f"{'restart' if a.get('action') == 'restart' else 'shut down'} the PC")
tool("cancel_shutdown", "Cancel a shutdown or restart that's waiting.", NO_ARGS, lambda a: _s().cancel_power(),
     group="pc", claim="pc_settings", reflex=[(r"cancel\s+(?:the\s+)?(?:shutdown|restart|reboot)", {})],
     reflex_say=lambda r: "Cancelled, the PC stays on." if r.success else None)
tool("open_settings_page", "Open a Windows Settings page, for switches apps can't flip: do not disturb, focus, night "
     "light (and display, bluetooth, wifi, sound pages).",
     params({"page": {"type": "string", "enum": ["do not disturb", "focus", "night light", "display", "bluetooth", "wifi",
                                                  "sound"]}}, ["page"]),
     lambda a: _s().open_page(a["page"]), group="pc", claim="app")
