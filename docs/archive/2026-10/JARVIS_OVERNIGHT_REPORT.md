# Jarvis overnight report (2026-10-08)

Branch `zigbee-home-devices`. Everything is in local commits; **nothing was pushed.**

## What changed

| Commit | Stage | What changed |
|---|---|---|
| 6bc99ce | Fixes.md | Corrections ("no, I said…") cancel the misheard action; unsure speech never triggers irreversible actions; sensor history; offers only for things Jarvis can actually do; fewer model calls; clean shutdown; arrival greeting |
| 3e154a9 | 1 Conversation | Every turn is classified (command / question / casual / emotional / correction / clarification / continuation / ending). No "Want me to…?" after a clear command. Openers vary. |
| 78880ce | 2 Latency | Speech starts on the first clause. Prompt opening is stable so it can be cached. Latency profiler added. |
| ff2cfdf | 3 Memory | Rejected facts never come back. A changed preference replaces the old one. Near-duplicates are merged. Plans expire. Sensitive info is not stored. Ranking uses trust and recency. |
| 066f237 | 4 Proactive | Every unprompted remark goes through one policy: speak now, wait, stay silent or ignore. Includes dedupe, cooldowns kept across restarts, quiet hours and busy detection. |
| 900188a | 5 Actions | Each action has a lifecycle (requested → … → completed / failed / canceled / waiting), saved to disk. After a restart, interrupted actions are reported. No double execution. Never says "done" after a partial failure. |
| 9eaf03b | 6 Voice | "Talk normal" works instantly and is remembered. Delivery tags are never stored. A weak mood guess doesn't change the voice. |
| e6a3933 | 7 Smart home | Device nicknames (`ZIGBEE_ALIASES`). "It" means the light you just used. Offline devices fail at once. Rapid commands each check their own result. The door sensor never claims who came in. |
| (this) | 8 QA, 9 Audit | Test categories, one safe command, a JSON report, `test_turn_taking` repaired, this report |

Across the stages: 50 files, about 3,300 lines added.

### New files

- `room_agent/conversation/policy.py`, `corrections.py`, `greet.py`
- `room_agent/proactive.py`
- `room_agent/actions/journal.py`
- `room_agent/speech/chunking.py`
- New test suites: reliability_fixes, conversation_policy, latency, memory_lifecycle, proactive, tasks, voice_delivery, smart_home, greet, live_fixes, zigbee
- Measurement scripts: `tests/latency_profile.py`, `tests/token_measure.py`

### New settings in `.env`

All optional, with safe defaults:

- `ZIGBEE_ALIASES`, `ZIGBEE_EVENTS_FILE`
- `GREET_*`
- `MEMORY_SENSITIVE`
- `QUIET_HOURS`
- `PROACTIVE_STATE_FILE`
- `ACTIONS_JOURNAL_FILE`

## Tests

The safe command (free and offline; never touches the PC, real devices or a paid API):

```
.\.venv\Scripts\python.exe -m tests
```

- Results are written to `tests/report.json`, with secrets redacted.
- `--hardware` adds the PC tests.
- `--live-api` adds the suites that call a real model.
- `--list` shows the categories.

**Result: 32 of 32 suites passed** (12 unit, 17 integration, 3 audio).

- Each stage's own tests passed before its commit.
- The 3 suites that used to fail depending on your `.env` (test_fixes, test_learning, test_social) are now isolated and pass.
- `test_turn_taking` had been broken since a refactor. It is fixed (28 checks).
- **Not run:**
  - `--hardware`: those tests really change the PC.
  - `--live-api`: those suites call a real model and cost money.

## Performance (measured)

| What | Before | After |
|---|---|---|
| Tokens for "ok thanks" | 7,185 | 5,061 |
| Timer request through the AI | 2 calls, 9,676 tokens | 1 call, 4,739 tokens |
| LED commands | AI call | 0 AI calls (instant) |
| Cacheable prompt opening | 1,317 tokens | 1,633 tokens |
| Piper first audio, long sentence (median) | 1,773 ms | 1,184 ms |
| Whisper large-v3-turbo on CUDA | | p50 160 ms |
| ElevenLabs v4 Turbo | | first audio 462 / 589 ms, cancel under 1 ms |
| Code overhead per turn | | basic turn about 33 ms, LED command about 94 ms |

Not measured: AI time to first token (that needs paid calls).

## Audit

- **Data and code checks:**
  - No corrupted characters in any source file.
  - No real key, token or phone number in tracked files, logs, traces or data files. All 10 credential values from `.env` were checked.
  - Every new setting has a default, so an old `.env` still works.
- **Test isolation:**
  - Every suite now uses temp files; none writes to the real memory, journal, spend or events.
  - Real Zigbee and the phone tunnel are always off in tests.
- **Memory change made during this work:**
  - One false fact (id 21, "Wants a dailies list…") was deactivated and recorded as rejected.
  - Backup: `memory.db.bak-20261008-003519`.
- **Zigbee2MQTT config:**
  - `retain` and `availability` were turned on.
  - Backup: `configuration.yaml.bak-*`.

### Honest notes

- **Paid calls during this work:** while repairing `test_turn_taking` I ran it twice before noticing that its last part calls the real model.
  - That made a handful of small real calls; today's total in `spend.json` is about $0.14, including your own use.
  - It also wrote 9 test entries (fake alarms and a timer) to the real `actions_journal.json`. No real alarm was set, because the reminders went to a temp file.
  - The test is now isolated, and its paid part only runs with `--live-api`.
  - The running Jarvis replaces that file on its next action. To clear it now, stop Jarvis and set the file's contents to `[]`.
- **`test_ring_ack`:** it also calls the real model, so it is only in the `--live-api` group and wasn't run.

## Not done or blocked

- **Not tested on real hardware:** the barge-in fix and the voice and turn-taking changes passed offline tests, but still need a live test.
- **Google sign-in (email and calendar):** client ID and secret are in `.env`, but the sign-in in the browser was never finished.
- **Aqara FP1E presence sensor:** not paired yet.
- **AI time to first token:** not measured.

## Live test checklist

Start Jarvis:

```
.\.venv\Scripts\python.exe main.py
```

Start the dashboard (http://localhost:8765):

```
.\.venv\Scripts\python.exe UI\server.py
```

1. Say "turn on the LED strip", then "make it blue", then "turn it off". It should be instant, with no "want me to…".
2. Walk in through the door. You get one short greeting, and nothing repeated within the cooldown.
3. Say "set a timer for 2 minutes", then "no, I said 3". The first timer is canceled and a 3-minute timer is set.
4. Talk over Jarvis mid-sentence. It stops at once.
5. Say "talk normal". It answers "Okay, normal voice" and keeps that after a restart.
6. Ask "what's the temperature in here?". You get a real reading, flagged if it's old.
7. Ask "what happened when I came in?". You get door times, never who it was.
8. Say "ok thanks". You get a short reply or none, with no follow-up question.
9. Stop with Ctrl+C. It shuts down within a few seconds, and the mic is released.

## Next priorities

1. Run the live checklist above, especially barge-in and turn-taking on real audio.
2. Finish the Google sign-in so email and calendar work.
3. Pair the FP1E and use presence for the greeting and the proactive "busy" check.
4. Measure AI time to first token with a few approved calls.
5. Push this branch and update PR #1 when you're happy with it.
