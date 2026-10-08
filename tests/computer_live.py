"""Real browsers on this PC (hardware test: only with python -m tests --hardware, or run directly).

    .venv\\Scripts\\python -m tests.computer_live          read-only: installed / default / front browser, reading the
                                                            tab in front (address, title, page text), timings
    .venv\\Scripts\\python -m tests.computer_live --act    ...plus: opens https://example.com in a NEW tab of the browser
                                                            it picks, reads it, clicks its link, goes back. Leaves that one
                                                            tab open (never closes tabs). No screenshots, no API calls.
"""

import sys
import time

from tests.harness import setup_env

setup_env(JARVIS_TEST="0")  # (this is the one test that touches the real browser, on purpose)

from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor  # noqa: E402
from room_agent.computer import browser_ops, browsers, uia  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
core.ensure_loaded()
ACT = "--act" in sys.argv

print("Read-only:")
have = browsers.installed(refresh=True)
t.check("installed browsers found", bool(have), have)
print("     installed:", ", ".join(browsers.KNOWN[k].name for k in have), "| default:", browsers.default_key())
wins = browsers.browser_windows()
print("     open browser windows:", len(wins))
if wins:
    key, hwnd, _ = wins[0]
    t0 = time.time()
    st = browsers.read_state(hwnd)
    t_addr = time.time() - t0
    t.check(f"{browsers.KNOWN[key].name}: the address bar is readable", bool(st["url"]), "(empty)")
    print(f"     address read in {t_addr * 1000:.0f} ms ({browsers.host(st['url'])})")
    t0 = time.time()
    doc = uia.document(hwnd)
    text = uia.page_text(doc) if doc is not None else ""
    print(f"     page text: {len(text)} characters in {(time.time() - t0) * 1000:.0f} ms")
    t.check("the page in front is readable through accessibility", len(text) > 50)
    t0 = time.time()
    tabs = uia.tabs(hwnd)
    print(f"     tabs: {len(tabs)} in {(time.time() - t0) * 1000:.0f} ms")
    t.check("tabs are listed", len(tabs) >= 1)

if ACT:
    print("Acting (one new example.com tab):")

    def run(name, args, said):
        rt.new_turn(said)
        t0 = time.time()
        r = executor.execute(name, args)
        print(f"     {name}: {r.message[:110]} ({time.time() - t0:.1f}s)")
        return r

    r = run("open_url", {"site": "example.com"}, "open example.com")
    t.check("opened example.com in a new tab, verified", r.success)
    r = run("browser_read_page", {}, "read this page")
    t.check("read it: 'Example Domain'", r.success and "Example Domain" in r.message)
    r = run("browser_click", {"target": "Learn more"}, "click learn more") if "Learn more" in r.message else \
        run("browser_click", {"target": "More information"}, "click more information")
    t.check("clicked its link, navigation verified", r.success)
    r = run("browser_navigate", {"action": "back"}, "go back")
    t.check("went back, verified", r.success)
t.done("COMPUTER LIVE")
