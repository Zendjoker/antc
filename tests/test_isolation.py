"""Test isolation: under the test runner, nothing Jarvis persists points at the real files in the project folder, API
keys are fake (a stray real call fails instead of costing money), and devices / phone / tracing are off.

Run:  .venv\\Scripts\\python -m tests.test_isolation
"""

import os
from pathlib import Path

from room_agent import config
from tests.harness import Checker

t = Checker()
root = Path(config.HERE).resolve()
print("Persistent files:")
for name in ("MEMORY_DB", "MEMORY_FILE", "RECENT_FILE", "REMINDERS_FILE", "SPEND_FILE", "SETTINGS_FILE", "ACTIONS_JOURNAL_FILE",
             "ZIGBEE_EVENTS_FILE", "PROACTIVE_STATE_FILE", "CONNECTIONS_FILE"):
    p = Path(getattr(config, name)).resolve()
    t.check(f"{name} is a throwaway file, not in the project folder", root not in p.parents, p)
for name in ("LEARNING_DB", "EXPERIENCE_DB", "APPS_CACHE"):
    p = Path(os.environ.get(name, "")).resolve()
    t.check(f"{name} is a throwaway file", root not in p.parents and p != root, p)

print("Paid APIs, devices, secrets:")
for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY"):
    v = os.environ.get(name, "")
    t.check(f"{name} is a fake value", "test" in v and "not-real" in v, "(real-looking value)")
t.check("Twilio can't place a call (no account)", not os.environ.get("TWILIO_ACCOUNT_SID") and not os.environ.get("TWILIO_AUTH_TOKEN"))
t.check("credentials go to an in-memory vault", os.environ.get("JARVIS_VAULT") == "memory")
t.check("Zigbee off (no real lights or sensors)", config.ZIGBEE is False or str(config.ZIGBEE) in ("0", "False"), config.ZIGBEE)
t.check("Home Assistant off", not config.HA_URL)
t.check("no public phone tunnel", not os.environ.get("PHONE_TUNNEL"))
t.check("tracing and audio dumps off", os.environ.get("TRACE") == "0" and os.environ.get("AUDIO_DEBUG") == "0")

print("Source files:")
root_dir = Path(__file__).resolve().parents[1]
bad = [str(p.relative_to(root_dir)) for d in ("room_agent", "tests", "UI") for p in (root_dir / d).rglob("*")
       if p.suffix in (".py", ".js", ".html", ".css")
       and any(c in p.read_text(encoding="utf-8", errors="ignore") for c in (chr(8), chr(0)))]
t.check("no source file contains a stray backspace or null byte (a mangled escape)", not bad, bad)

print("The real desktop is off-limits:")
t.check("test mode is on", config.TEST_MODE)
blocked = []
from room_agent.computer import browsers, screen, uia, winput  # noqa: E402
from room_agent.tools import music, pcsettings  # noqa: E402

for name, call in [("keyboard / mouse", lambda: winput.hotkey("f5")), ("UI Automation", lambda: uia.api()),
                   ("screenshot", lambda: screen._grab(0, 0, 10, 10)), ("window list", lambda: browsers._top_windows()),
                   ("launch a browser", lambda: browsers._launch("x.exe", ["https://example.com"])),
                   ("spotify link", lambda: music._open_uri("spotify:")), ("theme write", lambda: pcsettings._write_theme(0, 0)),
                   ("brightness", lambda: pcsettings._ddc_set(0, 50)), ("radios", lambda: pcsettings._run_async(lambda: None)),
                   ("sleep", lambda: pcsettings.sleep()), ("clipboard", lambda: winput.set_clipboard("x"))]:
    try:
        call()
        blocked.append((name, "NOT blocked"))
    except config.BlockedInTests:
        pass
    except Exception as e:  # noqa: BLE001
        blocked.append((name, f"other error {e.__class__.__name__}"))
t.check("every real-desktop primitive refuses to run under the tests", not blocked, blocked)
t.done("ISOLATION")
