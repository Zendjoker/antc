# Jarvis implementation roadmap

**Rule:** nothing new until the current system is proven live. Each phase ends with tests, a measurement, and a local commit.

## Done (commits on `zigbee-home-devices`, not pushed)

| Phase | Commit | Result |
|---|---|---|
| Reliability fixes, Stages 1–9 | 6bc99ce … 3e73f13 | Offline-tested |
| Validation prep: test isolation, diagnostics log | 15883ff | Tests can't touch real data or paid APIs |
| Computer interaction: browsers, screen, research | 152fd93 | L1, plus L2 read-only on your Opera |
| Daily-use gaps: supervisor, lists, files, music, texts, settings | 9ad8589 | L1, plus L2 read-only |
| Phase A/B: audit + P0 safety (Host check, emergency stop, audit log, memory hygiene, file writing, eval recorder) | cf3490d | Offline-tested; memory clean-up applied to real data |

## Phase F0: prove it (next; mostly you, about 2 hours)

1. **Restart Jarvis on the current code under the supervisor.**
   - Enable `DIAGNOSTICS=1`.
   - Enroll your voiceprint.
2. **Run the 20 real-world tasks** (`python -m tests.real_world_eval`).
3. **Send me:** `eval/results-*.json`, `logs/diagnostics-*.jsonl`, `logs/audit-*.jsonl`, and the console log.
4. **Exit criteria:**
   - Every failure has a diagnosed cause.
   - Real latency, cost and token numbers are recorded in `JARVIS_PERFORMANCE_REPORT.md`.

## Phase B2: fix what the live run shows (code)

Order by real failure frequency, not by guess. Likely candidates from the audit:

- Echo handling: tune barge-in thresholds from `barge` events; the voiceprint gate.
- Endpointing 0.88 s → test 0.6 s.
- Any false success claim: a P0 bug; add a regression test from the log.

## Phase C2: computer control hardening

- Run `python -m tests.computer_live --act` (a real browser test).
- Fix browser quirks found (Opera's address bar, page-load timing).
- Wire background watchers only if the live tests pass.

## Phase D2: research on the real web

- Task 11 (research + report) and task 12 (interrupt) live.
- Check every cited quote against its page by hand for the first 3 reports.

## Phase E2: autonomy

- Voice "stop" during long silent actions: keep the mic gate open for command words while a tool runs.
- Resume interrupted multi-step plans after a restart, only with a yes.

## Phase F: evaluation gate

- **18 of 20 real tasks passed, zero unauthorized actions, zero false success claims, twice in a row:** then it's ready for an external demo.

## Stop doing

- New personality, mood or emotion features: live data shows the social layer at "default" on 9 of 11 turns.
- New tool areas before the 20 tasks pass.
- Writing more offline tests for features nobody has tried live.
