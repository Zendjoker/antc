"""Window management: minimize, maximize, restore, focus, move between monitors (implementation: tools/window_control.py)."""

import ctypes
import os

from room_agent.abilities._kit import NO_ARGS, params, tool
from room_agent.abilities.apps import APP_HINTS, app_arg, app_live, known_app, resolve_app_name
from room_agent.actions.core import Group, register_group

IS_WINDOWS = os.name == "nt"
WINDOW = {"type": "string", "description": "The app as they named it ('Spotify', 'Chrome'). 'it' / 'that' = the app the "
                                           "conversation is about; 'this' / 'this window' = the window in front right now."}

register_group(Group("window", APP_HINTS, app_live, "window management",
                     "minimize, maximize, restore, bring to front, move a window to another monitor, what's in front, list "
                     "monitors (no seeing the screen)", lambda: IS_WINDOWS))


def _state(args, before=None):
    """The target window's place and size. After the action it's the same window as before ("this" may have moved on)."""
    from room_agent.tools import apps
    from room_agent.tools import window_control as wc

    u = wc.user32
    hwnd = before["hwnd"] if before and u.IsWindow(before["hwnd"]) else None
    name = before["app"] if before else None
    if hwnd is None:
        said = str(app_arg(args)).strip()
        if apps.norm(said) in {apps.norm(x) for x in wc.THIS} or said.lower() in wc.THIS:
            hwnd, name = wc.front_window(), "that window"
        else:
            entry, _ = apps.find(resolve_app_name(said) or "")
            wins = apps.app_windows(entry) if entry else []
            hwnd, name = (wins[0][0], entry["name"]) if wins else (None, None)
    if not hwnd:
        return None
    r = wc.wt.RECT()
    with wc._dpi_aware():
        u.GetWindowRect(hwnd, ctypes.byref(r))
        mon = wc._monitor_of(hwnd, wc.monitors())
    return {"app": name, "hwnd": hwnd, "rect": (r.left, r.top, r.right - r.left, r.bottom - r.top),
            "state": wc._state(hwnd), "monitor": {"num": mon["num"], "where": mon["where"]} if mon else None}


def put_window(snap, keep_state):
    """Put a window back to a saved place (and size state, unless keep_state). Read back before saying it worked."""
    from room_agent.tools import apps
    from room_agent.tools import window_control as wc

    u, hwnd = wc.user32, snap["hwnd"]
    if not u.IsWindow(hwnd):
        return f"FAILED: {snap['app']}'s window isn't there anymore, so it can't be put back."
    with wc._dpi_aware():
        now = wc._state(hwnd)
        want = now if keep_state else snap["state"]
        if now != "normal":
            u.ShowWindow(hwnd, wc.SW_RESTORE)
            wc._wait(lambda: wc._state(hwnd) == "normal", 1.0)
        x, y, w, h = snap["rect"]
        u.SetWindowPos(hwnd, None, x, y, w, h, wc.SWP_NOZORDER | wc.SWP_NOACTIVATE)
        if want == "maximized":
            u.ShowWindow(hwnd, wc.SW_MAXIMIZE)
        elif want == "minimized":
            u.ShowWindow(hwnd, wc.SW_MINIMIZE)
        mons = wc.monitors()
        same_monitor = lambda: (not snap["monitor"] or (wc._monitor_of(hwnd, mons) or {}).get("num") == snap["monitor"]["num"])
        ok = wc._wait(lambda: same_monitor() and wc._state(hwnd) == want)
        if want != "minimized":
            apps.bring_to_front(hwnd)
    if not ok:
        return f"FAILED: tried to put {snap['app']} back, but it didn't end up where it was."
    where = f" on monitor {snap['monitor']['num']}" if snap["monitor"] and len(mons) > 1 else ""
    return f"OK: {snap['app']} is back{where}" + (f", {want}." if want != "normal" else ", as it was.")


VERIFY = {"minimize_window": lambda a, b, c: c["state"] == "minimized",
          "maximize_window": lambda a, b, c: c["state"] == "maximized",
          "restore_window": lambda a, b, c: c["state"] != "minimized",
          "move_window_to_monitor": lambda a, b, c: c["monitor"] is not None}


def _call(fn_name, *keys):
    def run(args):
        from room_agent.tools import window_control

        return getattr(window_control, fn_name)(*[args[k] for k in keys])
    return run


def _expect_monitor(args, before):
    """Where "move it to <monitor>" must end up, worked out from where it was (the same rules as the tool)."""
    from room_agent.tools import window_control as wc

    if not before or not before.get("monitor"):
        return None
    mons = wc.monitors()
    current = next((m for m in mons if m["num"] == before["monitor"]["num"]), None)
    dest, problem = wc.pick_monitor(args.get("monitor", ""), mons, current, None)
    return {"monitor.num": dest["num"]} if dest else None


WIN = r"(?P<app>[\w][\w .+&'-]{1,40}?)"
common = dict(group="window", claim="app", available=lambda: IS_WINDOWS)
resize = dict(subject=app_arg, observe=_state, undo=lambda a, b, c: put_window(b, keep_state=False), undo_is_symmetric=True,
              **common)
tool("minimize_window", "Minimize an app's window: 'minimize Spotify', 'minimize this', 'hide it'.",
     params({"app": WINDOW}, ["app"]), _call("minimize_window", "app"), event="window.minimized",
     verify=VERIFY["minimize_window"], expect=lambda a, b: {"state": "minimized"},
     reflex=[(r"minimi[sz]e\s+" + WIN, {})], reflex_check=lambda a: known_app({"app_name": a["app"]}), **resize)
tool("maximize_window", "Maximize an app's window (full size on its monitor): 'maximize Spotify', 'make it full screen'.",
     params({"app": WINDOW}, ["app"]), _call("maximize_window", "app"), event="window.maximized",
     verify=VERIFY["maximize_window"], expect=lambda a, b: {"state": "maximized"},
     reflex=[(r"maximi[sz]e\s+" + WIN, {})], reflex_check=lambda a: known_app({"app_name": a["app"]}), **resize)
tool("restore_window", "Restore an app's window to normal size (un-minimize or un-maximize) and bring it up.",
     params({"app": WINDOW}, ["app"]), _call("restore_window", "app"), event="window.restored",
     verify=VERIFY["restore_window"], **resize)
tool("focus_window", "Bring a window to the front: 'bring Discord up', 'show me that window'.",
     params({"app": WINDOW}, ["app"]), _call("focus_window", "app"), subject=app_arg, event="app.focused", **common)
tool("move_window_to_monitor", "Move an app's window to another monitor: 'put Chrome on my second monitor', 'move it to the "
     "left screen', 'bring Discord here' (monitor 'here' = the monitor of the window that's in front).",
     params({"app": WINDOW, "monitor": {"type": "string", "description": "As they said it: a number ('2', 'second'), "
                                                                         "'primary', 'left', 'right', 'middle', 'other', or "
                                                                         "'here'."}}, ["app", "monitor"]),
     _call("move_window_to_monitor", "app", "monitor"), subject=app_arg, event="window.moved", observe=_state,
     verify=VERIFY["move_window_to_monitor"], undo=lambda a, b, c: put_window(b, keep_state=True), undo_is_symmetric=True,
     expect=_expect_monitor,
     reflex=[(r"(?:move|put|send)\s+" + WIN + r"\s+(?:to|on|onto)\s+(?:the\s+|my\s+)?(?P<monitor>other|next|left|right|"
              r"middle|primary|main|first|second|third|\d)(?:\s+one)?\s*(?:monitor|screen|display)?", {})],
     reflex_check=lambda a: known_app({"app_name": a["app"]}), **common)
tool("get_active_window", "Which window is in front right now (app, title, monitor, size).", NO_ARGS,
     _call("get_active_window"), changes_state=False, **common)
tool("list_monitors", "The monitors on this PC: numbers, which is primary, left/right, size.", NO_ARGS,
     _call("list_monitors"), changes_state=False, **common)
