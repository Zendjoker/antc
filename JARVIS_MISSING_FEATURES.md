# Jarvis: missing and incomplete

**Status words:**

- **Missing:** no code.
- **Unverified:** code and offline tests, never run for real.
- **Blocked:** needs you or an external account.

## Blocked on you (code can't do these)

| Item | Why it matters | What to do |
|---|---|---|
| Restart Jarvis on the current code | The live Jarvis is older than every fix since 00:31 last night | Stop it; `.\.venv\Scripts\python.exe -m room_agent.service --install`, then `--start` |
| Voiceprint | Without it, echo and other voices can interrupt (live log: "speaker verification not active") | `.\.venv\Scripts\python.exe main.py --enroll-voice` |
| Google sign-in | 16 email and calendar tools idle; meeting heads-ups and email watches need it | `.\.venv\Scripts\python.exe main.py --connect google` |
| The 20 real-world tasks | The only real measure of reliability | `.\.venv\Scripts\python.exe -m tests.real_world_eval` |
| Real-model tool-choice test | Nobody knows how often the AI picks the wrong tool | Approve ~$0.50 |
| FP1E presence sensor | Better arrival and "busy" detection | Pair it in Zigbee2MQTT |
| Twilio texting to US numbers | May need A2P 10DLC registration | Twilio console |

## Unverified (built, offline-tested only)

- Turn-taking, corrections, the follow-up-question policy, echo handling changes
- Browser actions: open, search, tabs, click, type, scroll, copy
- Research end to end on the real web; research reports
- Screen vision (no real vision call yet)
- Files: read on real documents; save, move, delete
- Spotify playlist and search playback
- Windows settings changes (dark mode, brightness, radios, lock, sleep, shutdown)
- Lists, moment reminders, break / rain / meeting / plan nudges, daily review
- Texts to your phone, VIPs by voice
- The supervisor, auto-start, single-instance lock, background Whisper preload
- The emergency stop (hotkey and voice), the audit log
- The Ollama fallback when the daily budget runs out

## Missing (no code)

| Item | Notes |
|---|---|
| Background watchers wired in | `room_agent/watchers.py` (price drops, page changes, email searches) is written but not connected or tested. It was held back on purpose: no new features until the live tests pass |
| Voice commands while Jarvis is busy | During a long silent action the mic isn't checked; only the hotkey or dashboard can stop it |
| Multi-step tasks that survive a restart | The journal records interrupted actions, but a half-done plan isn't resumed |
| Several users / guest mode | One voiceprint, one memory |
| "Look at my screen and fix this error" end to end | The parts exist (screen → research → actions with confirmation); the chain is untested and fixes beyond the existing tools aren't possible |
| Do-not-disturb / night light switches | Windows gives apps no supported way; Jarvis opens the Settings page instead |
| Native phone companion app | Phone calls and texts only |
| Firefox / Opera GX real testing | Not installed on this PC |

## Deliberately not built

- **Generic "press any keyboard shortcut":** its effect can't be verified.
- **Clicking by screen position as a first choice:** it's last resort only.
- **Browser automation sessions (Playwright etc.):** they control a separate browser, not yours.
