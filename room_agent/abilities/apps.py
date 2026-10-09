"""Opening, closing and switching Windows apps (implementation: tools/apps.py)."""

import os
import re
import time

from room_agent import runtime as rt
from room_agent.abilities._kit import CONFIDENCE, NO_ARGS, params, recently, tool
from room_agent.actions.context import env
from room_agent.actions.core import Group, Risk, register_claim, register_context, register_group, register_line

IS_WINDOWS = os.name == "nt"
APP_HINTS = re.compile(r"open|launch|start|run|close|quit|exit|kill|shut|switch|focus|back to|bring|pull up|apps?\b|program|"
                       r"window|in front|minimi|maximi|restore|monitor|screen|display|full ?screen|move|put|here|hide|"
                       r"show me|bigger|smaller", re.I)
APP_NAME = {"type": "string", "description": "The app as they named it, e.g. 'Spotify', 'VS Code', 'Chrome'. If they say "
                                             "'it' or 'that', use the app the conversation is about."}


def app_live():
    return bool(rt.last_active_app and recently(rt.last_active_app["at"]))


register_group(Group("apps", APP_HINTS, app_live, "opening, closing and switching apps",
                     "any installed Windows app by name; says it's done only after the window really opened, closed or came "
                     "to the front", lambda: IS_WINDOWS))
register_claim("app", r"\b(opened|launched|started|closed|quit|opening|launching|closing)\b.{0,40}\b(app|application|browser|"
                      r"chrome|spotify|program|window|file|folder|youtube|netflix|steam|discord|game|code|editor|for you)\b"
                      r"|\b(i'?ve|i have|i)\s+(just\s+)?(opened|launched|closed|quit)\b"
                      r"|^(opened|launched|closed|quit)\b"
                      r"|\b(switched|switching)( you| it)?( over| back)? to\b(?!.{0,30}\b(voices?|mode|style)\b)"
                      r"|\b(it'?s|that'?s) (now )?in front\b"
                      r"|^(minimized|maximized|restored|moved)\b|\b(i'?ve|i have|i)\s+(just\s+)?(minimized|maximized|restored)\b"
                      r"|\b(i'?ve|i have|i)\s+(just\s+)?moved (it|that|this|\w+) (to|over)\b"
                      r"|\b(it'?s|that'?s|\w+'s|\w+ is) (now )?(minimized|maximized|full ?screen|on (monitor|screen|display) \w+"
                      r"|on (your|the|my) \w+ (monitor|screen|display))\b")
register_claim("pc", r"\b(shut down|shutting down|restarted|restarting|locked)\b.{0,30}\b(pc|computer|screen|laptop)\b")


def _context(user_text):
    a = rt.last_active_app
    if a and time.time() - a["at"] < 600:
        return [f"- app_in_conversation: {a['name']} (you {a['action']} it). \"It\" / \"that\" about an app means this one, "
                "unless they name another."]
    return []


register_context(_context, order=40)


# ---------------------------------------------------------------- shared by apps.py and windows.py
def app_arg(args):
    return args.get("app_name") or args.get("app") or ""


def resolve_app_name(said):
    from room_agent.tools import apps

    said = str(said or "").strip()
    if said.lower() in apps.PRONOUNS or apps.norm(said) in apps.PRONOUNS:
        return env.active_app or ""
    return said


def _state(args, before=None):
    from room_agent.tools import apps

    entry, _ = apps.find(resolve_app_name(app_arg(args)) or "")
    if entry is None:
        return None
    return {"app": entry["name"], "running": bool(apps.processes(entry))}


def _undo_open(args, before, after):
    from room_agent.tools import apps

    if before.get("running"):
        return f"FAILED: {before['app']} was already open before, so there's nothing to undo."
    return apps.close_app(before["app"])


def _call(fn_name, *keys):
    def run(args):
        from room_agent.tools import apps

        return getattr(apps, fn_name)(*[args[k] for k in keys])
    return run


def known_app(args):
    """A reflex only fires for an app that's really in the (cached) app list, or "it" with an app in conversation."""
    from room_agent.tools import apps

    said = str(app_arg(args)).strip()
    if said.lower() in apps.PRONOUNS:
        return bool(env.active_app)
    entry, _ = apps.find(said)
    return entry is not None


APP_WORDS = r"(?P<app_name>[\w][\w .+&'-]{1,40}?)"
common = dict(group="apps", claim="app", available=lambda: IS_WINDOWS)
tool("open_app", "Open (launch) an app on this Windows PC, e.g. 'open Spotify', 'launch Discord', 'start Chrome'. Finds "
     "installed apps by name; if it's already open it's brought to the front.", params({"app_name": APP_NAME}, ["app_name"]),
     _call("open_app", "app_name"), subject=app_arg, event="app.opened", observe=_state,
     verify=lambda a, b, c: c["running"], undo=_undo_open, undo_if=lambda b, c: not b["running"],
     reflex=[(r"(?:open|launch|start|fire up|pull up|bring up)\s+(?:up\s+)?" + APP_WORDS, {})], reflex_check=known_app,
     **common)
tool("close_app", "Close (quit) an app on this PC, e.g. 'close Spotify', 'quit Discord', 'close it'. A browser closes "
     "with all its windows and tabs: for one tab use close_tab.",
     params({"app_name": APP_NAME, "confidence": CONFIDENCE}, ["app_name"]), _call("close_app", "app_name"),
     subject=app_arg, event="app.closed", observe=_state, verify=lambda a, b, c: not c["running"], scope="app",
     expect=lambda a, b: {"running": False}, risk=Risk.CONFIRM, min_confidence=0.7,
     reflex=[(r"(?:close|quit|exit)\s+" + APP_WORDS, {"confidence": 0.95})], reflex_check=known_app,
     **common)  # (closing can't be undone)
tool("focus_app", "Switch to an app that's already open (bring its window to the front), e.g. 'switch to Chrome', 'go back "
     "to Spotify'.", params({"app_name": APP_NAME}, ["app_name"]), _call("focus_app", "app_name"), subject=app_arg,
     event="app.focused", reflex=[(r"(?:switch to|go back to)\s+" + APP_WORDS, {})], reflex_check=known_app, **common)
tool("list_running_apps", "List the apps that have a window open on this PC right now.", NO_ARGS,
     _call("list_running_apps"), changes_state=False, **common)
