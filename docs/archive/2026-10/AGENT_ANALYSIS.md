# Jarvis: where it stands and what would make it great

Written 2026-10-08 from the code itself: 86 tools in 24 areas, 38 automated test suites.

## The short version

- **Breadth:** large. Jarvis covers voice, memory, timers, apps, windows, media, email, calendar, phone, smart home, browsers, screen and research.
- **Real-world proof:** thin. Most of what was built in the last two days passed offline tests but has never run against your real voice, room and browser.
- **Biggest risk:** not a missing feature but unverified behavior. Turn-taking, talking over Jarvis, and browser actions need live testing before anything new.
- **Gaps that matter most day to day:**
  1. Email and calendar aren't connected (Google sign-in unfinished).
  2. Jarvis can't choose music, only control what's playing.
  3. It can't read or find your files.
  4. It isn't always running: no auto-start or restart after a crash.

---

## 1. What exists today

| Area | What it does | Status |
|---|---|---|
| Voice in | Wake word, Whisper on the GPU (~160 ms), echo rejection, voiceprint check, talking over Jarvis | Built. **The talk-over fix is not live-tested.** |
| Voice out | ElevenLabs v4 (first audio ~0.5 s) with the free Piper voice as fallback, speaking styles, "talk normal", pronunciations | Built, measured |
| Conversation | Turn types (command, question, casual, emotional…), corrections ("I didn't say that"), waits for unfinished sentences, quiet mode | Built, offline-tested |
| Personality and social | Mood reading, tone, humor level, short answers when you're tired | Built, offline-tested |
| Memory | Remembers facts and preferences, never re-learns rejected facts, handles changed preferences, skips sensitive info, summaries | Built, offline-tested |
| Learning | Learned preferences, routines and automations, "why did you do that?" | Built, offline-tested |
| Actions | One pipeline for every tool: checks, confirmation for risky things, verification, undo, a saved action history | Built, the strongest part |
| Honesty | Never says "done" unless verified; sentences it can't back up are held back | Built, offline-tested |
| Timers and alarms | Any length, daily alarms, rings until you speak, corrections replace instead of duplicating | Built, live-used |
| Apps and windows | Open, close, switch, minimize, maximize, move between monitors | Built, live-tested earlier |
| Media | Volume, play/pause, next, what's playing | Built. **It can't search or choose music.** |
| Web | Quick search snippets, research with sources, browsers (open, search, tabs, read, click, type, scroll) | Built. **Browser actions not live-tested.** |
| Screen | "What am I looking at?" with privacy checks | Built. **No real vision call made yet.** |
| Email and calendar | 16 Gmail and Calendar tools (read, draft, send with confirmation, events) | Built. **Not connected:** Google sign-in unfinished. |
| Phone | You call Jarvis or it calls you (Twilio), driving mode from iPhone Shortcuts, urgent-only calls | Built, partly live-tested |
| Smart home | Zigbee: LED strip, door, vibration and temperature sensors; arrival greeting; Home Assistant support | Built, live-used. **FP1E presence sensor not paired.** |
| Proactive | Speak / wait / stay silent / ignore, with cooldowns, quiet hours, busy detection | Built, offline-tested |
| PC health | System load, ports, processes | Built |
| Dashboard | Live status, chat, memory, activity, connections, settings, research | Built |
| Testing | 38 safe suites, hardware tests, isolation guard, JSON report, diagnostic log | Built |
| Cost control | Daily budget, cheap model by default, smart model on demand, local Ollama fallback | Built. **The fallback when the budget runs out is untested.** |

---

## 2. Weak spots in what exists

Ranked by how much they would hurt day-to-day use.

1. **Live verification debt.** The overnight stages, the talk-over fix, timer corrections, browser actions and vision are all offline-tested only. Real rooms produce echo, accents, background noise and slow pages. Fix: run `LIVE_VALIDATION.md` and `COMPUTER_INTERACTION.md` with `DIAGNOSTICS=1`, then fix what the logs show.
2. **The real AI's tool choices were never tested at scale.** The tests use a scripted model. Nobody has measured how often gpt-5-mini picks the wrong tool or asks a needless question. Roadmap item 1 already plans this: about 50 real requests, roughly $0.20–0.50, with your approval.
3. **It isn't a service.** It runs in a terminal window: no auto-start at login, no restart after a crash, no log rotation. If it crashes overnight, your morning alarm doesn't ring.
4. **Startup is slow.** Whisper loads before Jarvis can listen. Roadmap item 4: load it in the background.
5. **Proactive triggers are few.** Door arrival, timers, offline devices and urgent phone calls. Nothing yet like "your meeting is in 10 minutes" in the room, "it's going to rain, take a jacket", or "you've been at the PC for 3 hours".
6. **Memory has no projects or plans.** It remembers facts and preferences, but not ongoing things ("I'm studying for the exam on Friday") that Jarvis should follow up on.
7. **The local-model fallback is unproven.** If the daily budget runs out, the switch to Ollama has never been tested end to end.
8. **Speaker ID is single-user.** There's one voiceprint, so guests and a second person in the room aren't handled.

---

## 3. What's missing

### High value (you'd use these daily)

| Missing | Why it matters | Size |
|---|---|---|
| **Google sign-in finished** | Unlocks all 16 email and calendar tools already built | 10 minutes of your time |
| **Choosing music** ("play some lo-fi", "play Drake on Spotify") | Media is only remote-control today | Medium (Spotify Web API, one sign-in) |
| **Files**: read, summarize, find ("summarize this PDF", "where's my CV") | Asked for today; nothing reads local files | Medium |
| **Always on**: auto-start, crash restart, health checks | Alarms and greetings depend on it running | Small |
| **Reminders tied to events** ("remind me when I get home", "when I sit down") | You have door and bed sensors already | Small to medium |
| **Notes and to-do list** ("add milk to my list", "what's on my list?") | Basic assistant job; memory isn't a list | Small |

### Medium value

| Missing | Notes |
|---|---|
| Texting or notifying your phone ("send that link to my phone") | The roadmap says calls only. A text or push for low-urgency things would fit. |
| VIP callers by voice ("always call me if Sarah emails") | Already the roadmap's next phone item |
| More home devices | Smart plugs, thermostat, AC, blinds. The Zigbee hub is ready; it needs devices. |
| PC settings | Wi-Fi, Bluetooth, dark mode, brightness, do not disturb, lock / sleep (with confirmation) |
| Daily review | Evening "here's what happened today", built from memory, calendar and the action history |
| Presence from the FP1E | Knows you're at the desk vs. away: better greetings, better "busy" detection |

### For a "perfect" agent (later)

| Missing | Notes |
|---|---|
| Long-running background tasks | "Watch this page and tell me when the price drops", "check my email every hour for X" |
| Multi-step goals that survive restarts | Today a task lives within a conversation |
| Several users | Voiceprints for family or guests, a private mode when someone else talks |
| Phone companion app | GPS, notifications, talking to Jarvis from anywhere without a phone call |
| Self-evaluation | It reviews its own diagnostic logs weekly and suggests fixes ("I misheard you 12 times this week near the fan") |

---

## 4. Plan, in order

### Now: prove it works (1–2 days, mostly your time)

1. Run the live checklists with `DIAGNOSTICS=1`: voice and turn-taking, timers, the LED strip, the door, then browsers.
2. Run the real-browser test: `.\.venv\Scripts\python.exe -m tests.computer_live --act`.
3. Finish the Google sign-in.
4. Approve a small real-model test (~$0.50) to measure tool choices.
5. Fix whatever the logs show.

### Next: the daily-use gaps (about a week of work)

1. Always on: auto-start, crash restart, startup in the background.
2. Notes and to-do lists.
3. Reminders on events (home, bed, desk).
4. File reading and search.
5. Choosing music on Spotify.
6. Pair the FP1E and use presence.

### Later: the "perfect" layer

1. Background watchers and scheduled checks.
2. Goals and plans that last across days.
3. Phone texts and notifications, VIPs by voice.
4. Several users and guest mode.
5. Weekly self-review from the diagnostic log.

---

## 5. What "perfect" means here

Five tests. Today Jarvis is designed to pass 1 and 3. 2, 4 and 5 need the work above.

1. **It never lies.** It says "done" only when it checked. *(Built and enforced in code.)*
2. **It's always there.** Running, fast to answer, the alarm always rings. *(Missing: service and crash recovery.)*
3. **It knows you.** It remembers, learns, and never brings back what you corrected. *(Built.)*
4. **It does the whole job.** "Play something chill", "read my CV", "remind me when I get home" all work end to end. *(Partly: music, files and event reminders are missing.)*
5. **It speaks up at the right time, and only then.** *(The policy is built; it needs more triggers and live tuning.)*
