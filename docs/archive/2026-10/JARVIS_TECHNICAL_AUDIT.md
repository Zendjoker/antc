# Jarvis technical audit

**Date:** 2026-10-08. **Code audited:** commit `9ad8589`.

**Evidence used (and nothing else):**

- **The code:** 24,673 lines in `room_agent/`, 162 files.
- **Tests:** 12,047 lines, 44 safe suites, all passing.
- **One real voice session:** the console log in your terminal from 2026-10-07 23:50–23:54, 11 voice turns.
- **Your real data:**
  - 21 conversation summaries and 23 memory rows in `memory.db`
  - `zigbee_events.json`, `spend.json`
  - the running Jarvis process, measured live
- **Read-only checks on this PC:** browsers, Spotify, monitors, radios, Windows Search.

**Passing tests are not evidence that a feature works in your room.** Each feature below is marked by the strongest evidence that exists for it.

## 0. The most important finding

**The Jarvis running on your PC right now is not the code being audited.**

- It started at 00:31 last night (process 67452, up about 10 hours, no crash).
- That was after the Fixes.md commit (00:19) and Stage 1 (00:24), but before Stages 2–9 and everything built since.

So the live-session evidence below is about older code. Everything committed after 00:31 has never run against your microphone, room or browser. The first real action is to restart Jarvis on the current code and run the supervised tests in `JARVIS_REAL_WORLD_TESTS.md`.

## 1. Evidence levels

| Level | Meaning |
|---|---|
| **L3: real use** | Seen working in a real session log or real data |
| **L2: real read-only** | Exercised read-only against the real PC (no voice loop) |
| **L1: offline** | Passes automated tests with fakes or mocks only |
| **L0: none** | Written, never exercised |

## 2. Feature status

| Feature | Level | Evidence | Problems found |
|---|---|---|---|
| Wake word + Whisper STT (large-v3-turbo, CUDA) | **L3** | Session log: hearing 0.11–0.19 s per turn | Echo still reaches recognition: "dropped doubtful speech 'But honestly,'" and "'If you want.'" were Jarvis's own words |
| Echo rejection while speaking | **L3, partial** | 30+ "barge-in rejected: too short" during replies; "rejected: likely echo" works | Constant false candidates during every reply. No voiceprint enrolled ("speaker verification not active") |
| Interrupting Jarvis | **L3** | 4 real "barge-in confirmed ... TTS cancelled" | The fix to stop choppy barge-in (commit 09a5693) is untested live |
| Unfinished sentences, corrections | L1 | test_turn_taking, test_reliability_fixes | The live "I didn't say that" case (the "dailies" turn) predates the fix |
| False memory from misheard speech | **L3 bug, fixed L1** | Live: "learned: +Wants a dailies list" from a misheard word | Fixed in code and in your data (row 21 rejected); the fix itself is offline-only |
| Unnecessary follow-up questions | **L3 bug, fixed L1** | Live: "Want me to cool it down or open the window?", "Want me to switch back to regular?", "turn it off or just dim it?" | Stage 1 policy is offline-only |
| Claiming capabilities it lacks | **L3 bug, fixed L1** | Live offer to "open the window"; past summaries: "only communicate in English", "60-second timer minimum" | Capability-aware offers are offline-only |
| Timers / alarms | **L3** | Most common real use (8 of 21 summaries) | Corrections ("no, I said 3") only L1 |
| Spotify / media control, windows across monitors | **L3** | Summaries 10-06 19:42, 10-08 00:54 | — |
| Web search snippets, weather, news | **L3** | Summaries: Bitcoin price, weather, news | — |
| Zigbee lights, sensors, arrival greeting | **L3** | Session: dim, blue, off verified; greeting spoken; door events | The greeting fired, then the door kept firing "busy" every 10 s; it's a noisy sensor |
| Phone mode (Twilio) | L3 partial | Summary 10-06 18:52 (a call); tunnel up in the log | Exposes a public URL (see security) |
| Gmail / Calendar (16 tools) | **L3 (connection)** | `connections.json`: connected 10-07 21:28, token refreshed successfully 10-08 09:02; refresh token in the Windows vault | The Gmail/Calendar tools themselves are untested live. If the Google Cloud app is in 'Testing' mode, sign-ins expire after 7 days (around 10-14) |
| Browser control (open/search/tabs/click/type/scroll/read) | **L2 read-only, L1** | Address read 125 ms, page text 36 ms, 11 tabs listed on your Opera | No real click, type, scroll or open done by Jarvis |
| Screen vision | L1 | Stub model only | No real vision call made |
| Research | L1 | Local test server | Never run against the real web through Jarvis |
| Files (find / read / open) | **L2 + L1** | Windows Search returned 5 PDFs in 0.24 s | Reading tested on generated files only |
| Music choice (Spotify playlists/search) | **L2 + L1** | Spotify's "Play <playlist>" buttons visible through accessibility | No real play by Jarvis |
| Lists, moment reminders, nudges, daily review | L1 | test_lists | — |
| Windows settings | **L2 + L1** | Radios read (2 Wi-Fi, 1 BT); DDC brightness read (80/58; a third monitor doesn't support it) | No real change made |
| Texts to own phone, VIPs | L1 | Stubbed Twilio | US numbers may need A2P 10DLC registration; untested |
| Supervisor / auto-start / single instance | L1 | test_service with a stand-in process | Not installed; your Jarvis runs unsupervised |
| Cost tracking and daily budget | **L3** | "turn cost: 0.19c ... today: 11.4c" lines | Budget-exceeded → Ollama fallback never exercised |

## 3. Broken or wrong right now (with evidence)

1. **Live Jarvis is stale.** See §0.
2. **Memory holds duplicates and junk** (`memory.db`):
   - Rows 10, 11, 12 and 15 are the same preference ("rotating wake-up roasts").
   - Rows 16, 19 and 20 are the same preference ("swearing style").
   - Row 17 contradicts itself (George, then Adam).
   - Row 23, "On the bed right now", is a momentary state stored as a permanent fact.
   - Duplicate detection (Stage 3) only stops new duplicates; old ones stay.
3. **Garbage summaries:** two summaries are literally "(Summary for memory): SKIP". The filter checks `startswith("SKIP")`, so that wording gets through. Still broken in current code (`memory/writer.py:330`).
4. **Echo leaking into recognition:** the live log shows Jarvis's own phrases transcribed as user speech ("But honestly,", "If you want.").
   - They were dropped as "doubtful", which is correct here. But that only works when Whisper is unsure, so confident echo would pass.
   - A voiceprint would help, and none is enrolled.
5. **Dashboard exposure (DNS rebinding):** neither local web server checks the `Host` header.
   - A malicious website could use DNS rebinding to read `GET /live` (your conversation, timers, location) and `GET /api/env` (settings, secrets masked).
   - State-changing requests are already protected (header + origin check).
6. **Possible thread growth:** the live Jarvis has 164 threads after 10 hours. Not yet proven to be a leak.
7. **Three dashboard servers are running** (since 23:08, from earlier starts). No supervisor covers the dashboard.

## 4. Architecture assessment (critical)

### Strong

- **One action pipeline** (`actions/executor.py`): validation, intent gate, confirmation, verification, undo, journal and claim checking in one place.
  - This is the core that makes "never claim success without checking" enforceable.
  - New tools inherit it.
- **The capability registry:** tools, prompt rules and claim checks are declared per area, not scattered.

### Weak or over-built

| Layer | Lines | Evidence it changes outcomes | Verdict |
|---|---|---|---|
| `social/` (mood, prosody, meaning embeddings) + `speech/director` | ~1,600 | Live log: 9 of 11 turns "confidence 0.09–0.16 -> default"; the rest only "wants it short" | **Mostly inert.** Keep it running (cheap: a 23 MB model, a few ms), but stop investing in it |
| `cognition/` levels and goals | ~800 | Live log: "DELIBERATE ... goal #1" on simple questions ("Why are you talking like that?"); changed nothing visible | Adds log noise and one more layer to debug; candidate to simplify after live data |
| `learning/` (preferences, routines, experience DB) | ~1,000 | No real routine or learned preference visible in your data | Unproven; freeze |
| Prompt context providers (15+ areas) | — | Live: 7,200–9,900 input tokens per call, 0–20% cached | Now 3,400–5,700 (offline measurement); still the main cost driver |
| Two model calls per tool turn | — | Live: every tool turn = 2 calls (tool, then reply) | Reflexes and verified-done shortcuts remove some (offline-tested) |

### Missing foundations

- **No emergency stop:** nothing stops everything at once (speech, pending actions, research, shutdown timer).
- **No audit log:** no append-only record of state-changing actions. The journal is a rolling recent-actions file.
- **No real-world evaluation loop:** no way to record supervised task results and compare runs.
- **No file write or report saving:** "save a report on my desktop" isn't possible.

## 5. Baseline measurements

### Real (live log, old code, 11 turns)

| What | Value |
|---|---|
| Speech recognition | 0.11–0.19 s |
| Endpointing (waiting for you to stop) | 0.88 s, every turn (fixed setting) |
| Model's first words | 1.09–3.08 s |
| First sound after you stopped | 1.58–3.64 s (median ~2.0 s) |
| Simple command via reflex | 0.58 s ("Turn the light off.") |
| Input tokens per call | 7,179–9,885; cache hits 0–8,192 |
| Cost per turn | 0.07–0.36 ¢ (gpt-5-mini); 11.4 ¢ for the evening |

### Real (live process now)

| What | Value |
|---|---|
| Idle CPU | 8.0% of one core |
| Memory | 1,295 MB |
| Threads | 164 |
| Startup to "Listening" | 15 s (23:50:15 → 23:50:30) |

### Offline (current code)

- **Input tokens per call:** 3,352 (chat) to 5,695 (with many tools offered).
- **Safe suite:** 44 of 44 suites pass.
- **Not measured:** real latency, cost or token counts of the current code.

## 6. Security summary

Full details are in `JARVIS_SECURITY_REVIEW.md`.

**Good:**

- Sensitive actions confirmed (send email, delete, buy, shutdown).
- Your own words required for actions triggered from untrusted content.
- Private tools are never logged.
- Screenshots restricted.
- Tests isolated.
- Secrets masked in the dashboard.

**Gaps:**

- No `Host` header check (DNS rebinding).
- No emergency stop.
- No audit log.
- The phone tunnel publishes a public URL while phone mode is on (signed and token-checked, but still a public surface).

## 7. Prioritized improvement plan

Order: reliability before features. The full roadmap is in `JARVIS_IMPLEMENTATION_ROADMAP.md`.

### P0: now (no user involvement needed)

1. Close the DNS-rebinding gap with a `Host` header check on both local servers.
2. Emergency stop: voice ("stop everything"), a global hotkey, and a dashboard button.
3. Append-only audit log of every state-changing action.
4. Memory hygiene:
   - transient states never stored as facts
   - the SKIP filter fixed
   - a reversible clean-up of existing duplicates (dry run first, with backup)
5. A thread-growth check, to confirm or rule out a leak.
6. Save reports and files (needed for research workflows), with verification.
7. A real-world evaluation recorder: 20 supervised tasks scored from the diagnostic log.

### P1: needs you (cannot be done by code)

1. Restart Jarvis on current code. Install the supervisor (`python -m room_agent.service --install`).
2. Enroll your voiceprint (`python main.py --enroll-voice`).
3. Run the 20 real-world tasks with `DIAGNOSTICS=1`.
4. Finish the Google sign-in.
5. Approve (or not) ~$0.50 of real model calls to measure tool choice.

### P2: after real data

1. Simplify the cognition levels if the logs show they add nothing.
2. Tune echo handling from diagnostics.
3. Cut tokens further where the logs show waste.
