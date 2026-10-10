# Roadmap

## Improve what exists
1. Test the real model's tool choices (~50 real requests, ~$0.20–0.50).
2. Speed: read the `timing:` lines after a day of use, fix the slowest stage.
3. Small talk fast path (no tools for casual chat).
4. Faster startup (load Whisper in the background).
5. Fix or remove `tests/scenarios.py` and `test_turn_taking.py`.
6. Stop writing "SKIP" memory summaries.
7. Test the local-model fallback when the budget runs out.

## Done: Location awareness (PC)
- Windows location first, then the internet connection's city; weather and "where am I" use it. `LOCATION_SOURCE=off` turns it off.
- Next: the phone's GPS when you're out (comes with the phone companion below).

## New: Jarvis calls you (phone mode)
- Jarvis can call your phone (or you call it) and it's the same Jarvis: same memory, email, calendar, tasks.
- Proactive: "Hey, you got an email from Andrew, want me to read it or reply?"
- Voice confirmations for anything that sends or changes things.
- Likely built on a phone service like Twilio (a phone number + voice line).

## New: Knows when you're driving
- Detect driving: your phone connects to the car's Bluetooth, or phone motion detection, or a car API (e.g. Smartcar/Tesla).
- Driving mode: short answers, read things out loud, no "look at your screen", only important interruptions.
- Decide when it's worth calling you (important email, meeting changed, reminder due).
- Needs: a phone companion (or Shortcuts / Tasker automation) + Gmail/Calendar push notifications.

## Decided
- Phone: iPhone. Driving detection: iPhone Driving Focus / CarPlay connects -> Shortcuts automation -> tells Jarvis
  "driving" (no Apple Watch needed).
- Calls only for important things.

## Done: Phone mode (code + tests; setup in phone.md)
- Call Jarvis's number, or Jarvis calls you while you drive: VIP / Gmail-important email, meeting soon or moved, alarm.
- iPhone Shortcuts tell it you're driving (CarPlay / Driving Focus) and can send your GPS.
- Next: say "always call me if Sarah emails" to add VIPs by voice; quiet hours; a text first instead of a call.

## Live verification (blocking further feature work)

Reconciled on 2026-10-09 from the archived 2026-10-08 audit bundle (`docs/archive/2026-10/`) against the current
repository. Status words below describe what the *current* code supports, not what the archived documents assumed;
re-check before treating anything here as settled.

- **STILL MISSING — the 20-task live eval has not produced results.** No `results-*.json` exists anywhere in the repo.
  Run `python -m tests.real_world_eval` with `DIAGNOSTICS=1` and record the output; see `JARVIS_REAL_WORLD_TESTS.md`.
  Proposed exit bar (carried over as a **proposal, not yet approved**): 18/20 tasks passed, zero unauthorized actions,
  zero false-success claims, twice in a row, before any external demo.
- **IMPLEMENTED, partial live evidence — audit log.** `room_agent/audit.py` exists and `logs/audit-202610.jsonl` has
  live entries, so the audit log is running in practice. Not yet cross-checked against the eval above.
  `room_agent/emergency.py` (emergency stop) exists; live behavior unverified.
- **IMPLEMENTED BUT UNVERIFIED — Ollama fallback.** `room_agent/llm/ollama.py` and router wiring exist; never
  confirmed live when the daily budget runs out.
- **IMPLEMENTED BUT UNVERIFIED — voiceprint.** `room_agent/enroll.py` and `room_agent/audio/speaker_id.py` exist;
  whether a voiceprint is currently enrolled on this machine is UNKNOWN from the repo alone.
- **STILL MISSING — background watchers.** `room_agent/watchers.py` exists (untracked) but nothing imports it; still
  not wired in, same as the archived assessment.
- **STILL MISSING — multi-step plan resume after a restart.** `room_agent/actions/journal.py` explicitly marks
  actions interrupted by a restart as canceled with an unknown outcome; it does not resume them.
- **UNVERIFIED (offline-tested only, unchanged from the archive unless you've since run live tests):** turn-taking /
  corrections / echo handling, browser actions (open, search, tabs, click, type, scroll, copy), end-to-end research,
  screen vision, file read/save/move/delete, Spotify playback, Windows settings changes, lists/reminders/nudges,
  texts/VIPs by voice, the supervisor/auto-start/background Whisper preload.
- **Rule carried forward as a proposal:** no new personality/mood/emotion features, no new tool areas, and no more
  offline-only tests for features nobody has tried live, until the 20-task eval passes.

## Backlog (no code yet, from the archived gap analysis — re-confirm relevance before acting)
- Voice "stop"/commands while Jarvis is mid-action (the mic isn't checked during a long silent action).
- Multiple users / guest mode (currently one voiceprint, one memory).
- "Look at my screen and fix this error" end to end (the pieces exist; the chain is untested together).
- Native phone companion app (phone mode currently does calls/texts only).
- Firefox / Opera GX real testing (not installed on the machine used for the 2026-10-08 audit).

## Decided against (from the archived gap analysis)
- Generic "press any keyboard shortcut": its effect can't be verified.
- Clicking by screen position as a first choice: last resort only.
- Browser automation sessions (Playwright etc.): they'd control a separate browser, not yours.

## Needs your confirmation (status UNKNOWN from the repo alone — not merged as fact)
- FP1E presence sensor pairing in Zigbee2MQTT (better arrival/"busy" detection).
- Twilio texting to US numbers possibly needing A2P 10DLC registration.
- Google Cloud OAuth consent screen publishing status (affects whether the 7-day reconnect prompt still applies).
- Approval to spend ~$0.20–0.50 on a real-model tool-choice test (~50 real requests) — item 1 above already tracks
  this; not duplicated.

## Historical material
Dated audits, performance/cost/security snapshots, and the original task briefs behind past fixes now live in
`docs/archive/2026-10/`. They describe the system as of 2026-10-08 (commits `9ad8589`/`cf3490d` on a branch not yet
merged to `main` at that time) and are kept for development history — they are **not current status**.
