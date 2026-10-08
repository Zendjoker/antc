# Jarvis live validation

Everything below is a **real-world** test, so it has not been done yet. The automated (offline) results are in `tests/report.json`.

## Setup (once)

1. Add this line to `.env`:
   ```
   DIAGNOSTICS=1
   ```
2. Stop any running Jarvis (Ctrl+C in its window).
3. Start Zigbee2MQTT: `C:\Users\adoum\zigbee2mqtt\start.bat`
4. Start Jarvis:
   ```
   .\.venv\Scripts\python.exe main.py
   ```
5. Optional: start the dashboard (http://localhost:8765):
   ```
   .\.venv\Scripts\python.exe UI\server.py
   ```

Each run writes `logs\diagnostics-<date>-<time>.jsonl`, one line per event:

| kind | What it shows |
|---|---|
| `speech` | Accepted or rejected, why, and the recognition time |
| `state` | Conversation state changes |
| `barge` | Interruptions, and how fast the voice stopped |
| `tool` | Each action, whether it worked, and how long it took |
| `turn` | Where the time went in that turn |

## Tests

Say "Hey Jarvis" first, unless the conversation is already active.

| # | Say | Expected | If it fails, look at |
|---|---|---|---|
| 1 | "What time is it?" | Answers in about 1–2 s, no follow-up question | `turn` (first_words, first_sound) |
| 2 | "Turn on the LED strip" | Strip on at once, short confirmation, no "want me to…?" | `tool` set_light: success, seconds |
| 3 | "Make it blue" | The same strip turns blue | `tool` set_light; is Zigbee2MQTT running? |
| 4 | "Turn it off" | The strip turns off | `tool` set_light |
| 5 | "Set a timer for 2 minutes", then "No, I said 3" | Says 3 minutes; **only one** timer is left | `tool` set_timer: result says "Replaced the …". Then ask "what timers do I have?" |
| 6 | "Cancel the timer" | Cancelled (may ask "sure?" first) | `tool` cancel_timer |
| 7 | Ask "tell me a long story", then talk over it ("stop") | Stops within about half a second | `barge`: confirmed and voice_to_stop_s. If rejected, read `why` |
| 8 | Stay quiet while it talks | It doesn't interrupt itself (no echo) | `speech` lines with label AGENT_ECHO should be rejected, not answered |
| 9 | "Set an alarm…" (pause) "…for 8 AM" | One alarm at 8 AM, no reply during the pause | `state` stays LISTENING during the pause; `tool` set_alarm |
| 10 | "Talk normal" | "Okay, normal voice." The voice stays normal after a restart | `tool` set_speaking_style |
| 11 | "What's the temperature in here?" | A real reading from the sensor | `tool` home_sensors |
| 12 | Walk out, come back in through the door | One short greeting; nothing more if you repeat it within minutes | Console lines starting `door opened:` or `greeting`; `zigbee_events.json` |
| 13 | "What happened when I came in?" | Door open and close times, never who it was | Console reply; `zigbee_events.json` |
| 14 | "Ok thanks" | A short reply or none, with no question back | `turn` |
| 15 | "Do I have a daily checklist?" | Says no (that false memory stays rejected) | Console `memory:` lines |
| 16 | Ctrl+C | Stops within a few seconds; the mic is released | The last console lines |

## When something fails

1. Note the test number and the clock time.
2. Open the newest `logs\diagnostics-*.jsonl` and find the lines at that time:
   - **Speech rejected:** check `why`, `logprob` and `no_speech`.
   - **Slow:** check `turn` → `hearing`, `first_words`, `first_sound`.
   - **Action:** check `tool` → `success`, `verified` and `result`.
3. Check `actions_journal.json`: was the action COMPLETED, FAILED or WAITING?
4. For more detail, add `TRACE=1` to `.env`. This logs one decision block per turn.

**Privacy:** the log keeps your transcripts on this PC. Keys, tokens, passwords, phone numbers and emails are removed, and action arguments are logged by name only. To turn it off, set `DIAGNOSTICS=0`.
