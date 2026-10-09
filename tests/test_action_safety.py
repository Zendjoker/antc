"""Action safety, offline: a tab request never closes the whole browser, confirmations understand plain yes / no (and
still need one), a confirmed action runs once, nothing is confirmed about a mission that doesn't exist, and an action
is never announced while it waits for a yes. Real executor, made-up probe capability, fake browser.

Run:  .venv\\Scripts\\python -m tests.test_action_safety
"""

import re

from tests.harness import setup_env

setup_env()

from room_agent import runtime as rt  # noqa: E402
from room_agent import truth  # noqa: E402
from room_agent.actions import core, pending  # noqa: E402
from room_agent.actions.core import Capability, Risk, register  # noqa: E402
from room_agent.actions.executor import execute, said_yes  # noqa: E402
from room_agent.cognition import understand  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
core.ensure_loaded()


def turn(text, n=None):
    rt.new_turn(text)
    rt.turn_no = n if n is not None else rt.turn_no + 1
    rt.turn_text = text
    rt.turn.intent = understand.read_turn(text)


# ---------------------------------------------------------------- a probe: an action that can't be taken back
RAN = []
register(Capability(
    name="stop_job", description="Cancel the test job for good.", group=None, risk=Risk.SENSITIVE,
    parameters={"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]},
    intent=re.compile(r"\bjob\b", re.I), describe=lambda a: f"cancel the {a.get('job')} job for good",
    execute=lambda a: (RAN.append(dict(a)), f"OK: cancelled the {a['job']} job.")[1],
    verification="internal", verified_by="test"))
STOP = core.get("stop_job")
WHAT = "cancel the backup job for good"

print("\nPlain yes / no")
for text, want in (("Oh my god, yes.", True), ("I said yes", True), ("Yes, cancel the job for good.", True),
                   ("Cancel the job.", True), ("yeah do it", True), ("Of course.", True), ("For good.", True),
                   ("No.", False), ("Stop. No.", False), ("yes... wait, no", False), ("Yes, but not now", False),
                   ("Don't.", False), ("yes, but what's the weather?", False), ("what time is it", False),
                   ("Never mind.", False)):
    t.check(f"{text!r} -> {'yes' if want else 'not a yes'}", said_yes(text, STOP, WHAT) == want)
send = core.get("send_text") or core.get("gmail_send") or core.get("text_me")
if send is not None:
    t.check("for an action that isn't a cancel, 'yes, cancel' is not a yes", not said_yes("Yes, cancel", send, "send it"))

print("\nA confirmation, answered in plain words")
rt.pending = None
turn("cancel the backup job", 10)
r = execute("stop_job", {"job": "backup"})
t.check("an action that can't be taken back asks first", r.message.startswith("NEEDS_CONFIRMATION") and not RAN, r.message)
turn("Oh my god, yes.")
t.check("'Oh my god, yes.' isn't treated as a cancel of the request", pending.on_utterance("Oh my god, yes.") is None
        and rt.pending is not None)
r = execute("stop_job", {"job": "backup"})
t.check("...and the action runs", r.success and RAN == [{"job": "backup"}], r.message)
turn("yes")
r = execute("stop_job", {"job": "backup"})
t.check("a confirmed action runs at most once (the same call later needs a new yes)", len(RAN) == 1
        and r.message.startswith("NEEDS_CONFIRMATION"), r.message)

RAN.clear()
rt.pending = None
turn("cancel the backup job", 20)
execute("stop_job", {"job": "backup"})
turn("Cancel the job.")
t.check("saying the action again while it waits ('Cancel the job.') is a yes, not 'never mind'",
        pending.on_utterance("Cancel the job.") is None and rt.pending is not None)
r = execute("stop_job", {"job": "backup"})
t.check("...and runs it once", r.success and len(RAN) == 1, r.message)

RAN.clear()
rt.pending = None
turn("cancel the backup job", 30)
execute("stop_job", {"job": "backup"})
turn("Yes, cancel the job for good.")
r = execute("stop_job", {"job": "nightly"})
t.check("a yes approves only the action it was asked about (other arguments: asked again)", not RAN
        and r.message.startswith("NEEDS_CONFIRMATION"), r.message)

RAN.clear()
rt.pending = None
turn("cancel the backup job", 40)
execute("stop_job", {"job": "backup"})
turn("No.")
d = pending.on_utterance("No.")
t.check("'No.' drops it in code, without claiming anything was cancelled", d is not None and rt.pending is None
        and "cancelled" not in (d.reply or "").lower(), getattr(d, "reply", None))
turn("yes... wait, no")
r = execute("stop_job", {"job": "backup"})
t.check("'yes... wait, no' never runs it", not RAN and not r.success, r.message)
rt.pending = None
turn("cancel the backup job", 45)
execute("stop_job", {"job": "backup"})
turn("No, wait.")
t.check("'No, wait.' isn't a yes, and doesn't drop it either (they're thinking)",
        pending.on_utterance("No, wait.") is None and rt.pending is not None and not said_yes("No, wait.", STOP, WHAT))

print("\nNothing to cancel")
rt.pending = None
turn("Cancel the mission.", 50)
r = execute("stop_mission", {})
t.check("no mission at all: says there's nothing to stop, asks nothing, creates no confirmation",
        r.message.startswith("OK: nothing to stop") and rt.pending is None, r.message)
turn("Stop. No.")
r = execute("stop_mission", {})
t.check("...even when their words don't name the mission", r.message.startswith("OK: nothing to stop")
        and rt.pending is None, r.message)
turn("pause the mission")
t.check("pause / resume too", execute("pause_mission", {}).message.startswith("OK: nothing to pause")
        and execute("resume_mission", {}).message.startswith("OK: nothing to resume"))

print("\nA tab is not the whole browser")
CLOSED = []
close_app = core.get("close_app")
close_app.execute = lambda a: (CLOSED.append(a["app_name"]), f"OK: {a['app_name']} is closed.")[1]
close_app.observe = close_app.verify = close_app.expect = None
close_app.available = lambda: True
for text, app, runs in (("Close the tab.", "Google Chrome", False), ("close this tab please", "Google Chrome", False),
                        ("close the chrome tab", "Google Chrome", False), ("close chrome", "Google Chrome", True),
                        ("quit the whole browser", "Google Chrome", True),
                        ("close the tab and quit Spotify", "Spotify", True)):
    CLOSED.clear()
    rt.pending = None
    turn(text)
    r = execute("close_app", {"app_name": app, "confidence": 0.95})
    ok = bool(CLOSED) == runs and (runs or ("close_tab" in r.message and not r.success))
    t.check(f"{text!r}: close_app({app}) {'runs' if runs else 'is refused, pointing to close_tab'}", ok, r.message)
t.check("a tool that closes one tab exists, scoped to a tab", core.get("close_tab") is not None
        and core.get("close_tab").scope == "tab" and core.get("close_tab").verification == "internal")

print("\nNothing is announced before it happened")
g = truth.ClaimGuard(lambda: 0, lambda: False)
g.tool_result("stop_mission", "NEEDS_CONFIRMATION: nothing was done. This can't be taken back easily")
t.check("while it waits for a yes, 'Cancelling the mission for good.' is held", bool(g.unverified(
    "Cancelling the mission for good.")))
t.check("...and 'The mission is cancelled.' too", bool(g.unverified("The mission is cancelled.")))
t.check("...but the question itself is said", not g.unverified("Cancel the mission for good?"))
g = truth.ClaimGuard(lambda: 0, lambda: False)
g.tool_result("stop_mission", "OK: nothing to stop: no mission is running or paused.")
t.check("'nothing to stop' never confirms 'I cancelled the mission'", bool(g.unverified("I cancelled the mission.")))
g = truth.ClaimGuard(lambda: 0, lambda: False)
t.check("with no tool at all, 'starting the lead mission for Daly City' is held (live 07:40)", bool(g.unverified(
    "Alright - starting the lead mission for Daly City now.")))
g.tool_result("stop_mission", "OK: stopped 'Restaurants in Daly City'.")
t.check("after the mission really stopped, 'Mission cancelled.' is said", not g.unverified("The mission is cancelled."))
g = truth.ClaimGuard(lambda: 0, lambda: False)
g.tool_result("close_app", "OK: Google Chrome is closed.")
t.check("closing the app never confirms 'I closed the tab.'", bool(g.unverified("I closed the tab.")))

print("\nclose_tab on a fake browser")
from room_agent.computer import browser_ops, browsers, uia, winput  # noqa: E402


class Tabs:
    def __init__(self, names, selected=0):
        self.names, self.sel, self.keys, self.focus, self.closes = list(names), selected, [], True, True

    def tabs(self, hwnd):
        return [(n, n, i == self.sel) for i, n in enumerate(self.names)]

    def hotkey(self, combo):
        self.keys.append(combo)
        if combo == "ctrl+w" and self.closes:
            self.names.pop(self.sel)
            self.sel = max(0, self.sel - 1)
        return True


def fake(names, **kw):
    f = Tabs(names)
    for k, v in kw.items():
        setattr(f, k, v)
    uia.tabs = f.tabs
    uia.invoke = lambda el: (setattr(f, "sel", f.names.index(el)), True)[1]
    winput.focus_window = lambda h, wait=1.5: f.focus
    winput.foreground = lambda: 7 if f.focus else 9
    winput.hotkey = f.hotkey
    browser_ops._state = lambda h: {"url": "https://example.com", "title": f.names[f.sel] if f.names else ""}
    return f


key = next(iter(browsers.KNOWN))
f = fake(["Inbox - Gmail", "Results - Google Search", "Docs"])
f.sel = 1
r = browser_ops.close_tab(7, key)
t.check("the tab showing is closed, the others stay", r.startswith("OK") and f.names == ["Inbox - Gmail", "Docs"], r)
f = fake(["Inbox - Gmail", "YouTube", "Docs"])
r = browser_ops.close_tab(7, key, "YouTube")
t.check("'close the YouTube tab' closes that one", r.startswith("OK") and "YouTube" not in f.names, r)
f = fake(["Only tab"])
r = browser_ops.close_tab(7, key)
t.check("the last tab is never closed (that would close the window)", r.startswith("FAILED") and not f.keys, r)
f = fake(["A", "B"], focus=False)
r = browser_ops.close_tab(7, key)
t.check("no keystroke when the browser can't be brought to the front", r.startswith("FAILED") and not f.keys, r)
f = fake(["A", "B"], closes=False)
r = browser_ops.close_tab(7, key)
t.check("a tab that doesn't close is never reported closed", r.startswith("UNKNOWN") and "Don't say" in r, r)
t.done("ACTION SAFETY")
