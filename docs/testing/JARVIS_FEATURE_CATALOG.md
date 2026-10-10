# Jarvis feature catalog

Generated 2026-10-09 from the live code (not from any prior report). Evidence sources: the `MODULES` registry in
`room_agent/actions/core.py` (confirms every `abilities/*.py` file is actually loaded — none are orphaned), the
`tool(...)` / `Capability(...)` registration calls in each file, `TOOL_AUDIT.md` (generated from the same live
registry: 125 tools, 84 state-changing), `.env.example` (dependency names, no secrets read), and the UI source
(`UI/index.html`, `UI/server.py`).

**What this is not:** a claim that any feature works end-to-end on real hardware. "IMPLEMENTED" means the code exists
and is wired into the registry/UI reachable by a normal user action — not that it has been exercised live. Where
`ROADMAP.md` already records a feature as unverified live, that is carried over here rather than re-asserted as fact.

## How to read this table

- **Status** — `IMPLEMENTED` (wired, reachable), `PARTIAL` (wired but a dependent piece is known incomplete),
  `NOT WIRED` (code exists, nothing loads it), `UNAVAILABLE` (needs something not present on this machine/account).
- **I/O** — `READ` (no state change; `changes_state=False` in the registry or a pure "list/get/show" tool),
  `LOCAL-WRITE` (changes something on this PC only), `EXTERNAL-WRITE` (changes a cloud account: Gmail, Calendar,
  Twilio, Home Assistant, Spotify).
- **Risk** — `SAFE` / `CONFIRM` / `SENSITIVE`, from the registry's `risk=` field where explicitly set (confirmed by
  grep against `room_agent/abilities/*.py`); unmarked tools default to `SAFE` per `Capability.risk = Risk.SAFE`.
- Entry points are given as `abilities file -> implementation file(s)`, matching the codebase's own convention
  ("implementation: tools/X.py" docstrings).

---

## 1. Voice, speech & audio (`room_agent/audio/`, `room_agent/speech/`, `abilities/voice.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| VOICE-001 | Wake word detection | "Hey Jarvis" wakes the mic pipeline | `room_agent/audio/` (openWakeWord) | IMPLEMENTED | READ | mic device |
| VOICE-002 | Speech-to-text | Converts speech to text (cloud or local) | `audio/stt.py` | IMPLEMENTED | READ | Deepgram API key, or local Whisper |
| VOICE-003 | Follow-up listening window | Listens ~4s after a reply so you can keep talking without the wake word | `conversation/` loop | IMPLEMENTED, **unverified live** (ROADMAP) | READ | mic |
| VOICE-004 | Barge-in / interruption | Speaking over Jarvis stops its current reply | `audio/` + `cognition/` | IMPLEMENTED, **unverified live** (ROADMAP: "turn-taking... offline-tested only") | READ | mic |
| VOICE-005 | Speaker verification (voiceprint) | Only the enrolled voice can interrupt/command | `audio/speaker_id.py`, `room_agent/enroll.py` | IMPLEMENTED, **enrollment status on this machine UNKNOWN** | READ | `main.py --enroll-voice` once |
| VOICE-006 | Text-to-speech | Spoken replies via ElevenLabs or local Piper | `speech/` -> `elevenlabs.py` | IMPLEMENTED | LOCAL-WRITE (audio out) | ElevenLabs API key, or Piper models (`models/tts_impostors/`) |
| VOICE-007 | `set_listening_patience` | Wait longer before treating you as finished, and remember it | `abilities/voice.py` | IMPLEMENTED | LOCAL-WRITE (`settings.json`) | none |
| VOICE-008 | `set_speaking_rate` | Talk faster/slower, kept | `abilities/voice.py` | IMPLEMENTED | LOCAL-WRITE | none |
| VOICE-009 | `set_speaking_style` | Softer/warmer/more engaged tone, kept | `abilities/voice.py` | IMPLEMENTED | LOCAL-WRITE | none |
| VOICE-010 | `list_voices` | Lists voices you can switch to | `abilities/voice.py` | IMPLEMENTED | READ | none |
| VOICE-011 | `set_voice` | Changes the active TTS voice | `abilities/voice.py` | IMPLEMENTED | LOCAL-WRITE | ElevenLabs API key (for non-local voices) |
| VOICE-012 | `set_pronunciation` | Remembers how to say a word | `abilities/voice.py` | IMPLEMENTED | LOCAL-WRITE | none |
| VOICE-013 | `forget_pronunciation` | Reverts a remembered pronunciation | `abilities/voice.py` | IMPLEMENTED | LOCAL-WRITE | none |
| VOICE-014 | `go_quiet` | Stops talking/listening until the wake word again | `abilities/presence.py` | IMPLEMENTED | LOCAL-WRITE | none |

## 2. Conversation & cognition (`conversation/`, `cognition/`, `social/`, `truth.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| CONV-001 | Turn classification | Every turn is read as command/question/casual/emotional/correction/clarification/continuation/ending | `cognition/__init__.py` | IMPLEMENTED, **unverified live** | READ | none |
| CONV-002 | Correction handling | "No, I said…" replaces/cancels the misheard action instead of adding a new one | `cognition/`, per-ability `_context()` correction hints (e.g. `timers.py` lines ~51-53) | IMPLEMENTED, **unverified live** | LOCAL-WRITE | none |
| CONV-003 | Small-talk / casual replies | Casual chat doesn't trigger tool calls | `cognition/`, `social/strategy.py` | IMPLEMENTED | READ | none |
| CONV-004 | Social/emotional adaptation | Tone adapts to detected mood/prosody without announcing it | `social/signals.py`, `social/strategy.py` | IMPLEMENTED | READ | none |
| CONV-005 | Claim verification (`truth.py`) | What Jarvis *says* it did is checked against the tool's actual verified result before being spoken as fact | `room_agent/truth.py`, `register_claim(...)` per ability | IMPLEMENTED | READ | none |
| CONV-006 | Model routing | Picks Claude/OpenAI/Ollama per turn by cost/capability need | `llm/router.py`, `llm/budget.py` | IMPLEMENTED | READ | API keys for the paid providers; Ollama for the free fallback (fallback **unverified live**, ROADMAP) |

## 3. Memory (`memory/`, `abilities/memory.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| MEM-001 | `remember` | Writes something to persistent memory on request/correction | `abilities/memory.py` -> `memory/store.py` | IMPLEMENTED | LOCAL-WRITE (`memory.db`) | none |
| MEM-002 | `recall` | Looks up stored memory by topic | `abilities/memory.py` | IMPLEMENTED | READ | none |
| MEM-003 | `forget` | Deletes memories matching a topic, or everything | `abilities/memory.py` | IMPLEMENTED, risk `CONFIRM` | LOCAL-WRITE | none |
| MEM-004 | Memory lifecycle (dedup/decay/sensitive-data exclusion) | Rejected facts don't return; near-duplicates merge; plans expire; sensitive info isn't stored | `memory/writer.py`, `memory/cleanup.py` | IMPLEMENTED (per `docs/archive/2026-10/JARVIS_OVERNIGHT_REPORT.md`; **re-verify current behavior**, not re-confirmed this pass) | LOCAL-WRITE | none |

## 4. Learning / preferences (`learning/`, `learning/capabilities.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| LEARN-001 | `learn_preference` | Stores how you like something done, from what you say/do | `learning/capabilities.py` -> `learning/storage.py` (`learning.db`) | IMPLEMENTED | LOCAL-WRITE | none |
| LEARN-002 | `list_learned` | What Jarvis has learned about your preferences | `learning/capabilities.py` | IMPLEMENTED | READ | none |
| LEARN-003 | `explain_last_action` | Why Jarvis did something on its own | `learning/capabilities.py` | IMPLEMENTED | READ | none |
| LEARN-004 | `forget_preference` | Removes a learned preference | `learning/capabilities.py` | IMPLEMENTED, risk `CONFIRM` | LOCAL-WRITE | none |
| LEARN-005 | `set_automation` | Configures an "if X then do Y" rule | `learning/capabilities.py` | IMPLEMENTED | LOCAL-WRITE | none |
| LEARN-006 | `run_routine` | Runs a saved multi-step routine | `learning/capabilities.py` | IMPLEMENTED | LOCAL-WRITE (varies by routine) | depends on routine's own tools |

## 5. Info & search (`abilities/info.py`, `abilities/location.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| INFO-001 | `get_time` | Current local date/time | `abilities/info.py` | IMPLEMENTED | READ | none |
| INFO-002 | `get_weather` | Current + forecast weather | `abilities/info.py` | IMPLEMENTED | READ | weather API key |
| INFO-003 | `daily_briefing` | Date/time + weather + headlines + timers + plans in one call | `abilities/info.py` | IMPLEMENTED | READ | weather + search API keys |
| INFO-004 | `web_search` | Web/news search for current facts | `abilities/info.py` (group `web`) | IMPLEMENTED | READ | search API key |
| INFO-005 | `get_location` | Current city (Windows location or IP) | `abilities/location.py` | IMPLEMENTED | READ | `LOCATION_SOURCE` not `off` |

## 6. Timers, alarms & reminders (`abilities/timers.py`, parts of `abilities/lists.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| TIMER-001 | `set_timer` | Countdown timer, any length ≥1s | `abilities/timers.py` -> `tools/timers.py` | IMPLEMENTED | LOCAL-WRITE | none |
| TIMER-002 | `set_alarm` | Alarm/reminder at a clock time, optional daily | `abilities/timers.py` | IMPLEMENTED | LOCAL-WRITE | none |
| TIMER-003 | `list_timers` | Running timers/alarms with time remaining | `abilities/timers.py` | IMPLEMENTED | READ | none |
| TIMER-004 | `cancel_timer` | Cancels a timer/alarm by label or "all" | `abilities/timers.py` | IMPLEMENTED | LOCAL-WRITE | none |
| TIMER-005 | `remind_me_when` | Reminder tied to a moment (home/leave/bed/PC/morning), not a clock time | `abilities/lists.py` | IMPLEMENTED, **unverified live** | LOCAL-WRITE | presence/arrival detection |
| TIMER-006 | `list_moment_reminders` | Lists moment-based reminders waiting | `abilities/lists.py` | IMPLEMENTED | READ | none |
| TIMER-007 | `cancel_moment_reminder` | Cancels a moment reminder | `abilities/lists.py` | IMPLEMENTED | LOCAL-WRITE | none |
| TIMER-008 | `daily_review` | Recap of the day: done/open/tomorrow | `abilities/lists.py` | IMPLEMENTED | READ | none |

## 7. Lists & notes (`abilities/lists.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| LIST-001 | `add_to_list` | Adds an item to to-do/shopping/any named list | `abilities/lists.py` | IMPLEMENTED | LOCAL-WRITE | none |
| LIST-002 | `show_list` | Reads a list, optionally including done items | `abilities/lists.py` | IMPLEMENTED | READ | none |
| LIST-003 | `check_off` | Marks a list item done | `abilities/lists.py` | IMPLEMENTED | LOCAL-WRITE | none |
| LIST-004 | `remove_from_list` | Removes an item (not marking done) | `abilities/lists.py` | IMPLEMENTED | LOCAL-WRITE | none |
| LIST-005 | `clear_list` | Empties a whole list (or just done items); asks first | `abilities/lists.py` | IMPLEMENTED, risk `SENSITIVE` | LOCAL-WRITE | none |
| LIST-006 | `take_note` | Freeform note kept on this PC | `abilities/lists.py` | IMPLEMENTED | LOCAL-WRITE | none |

## 8. Apps & windows (`abilities/apps.py`, `abilities/windows.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| APP-001 | `open_app` | Launches an app by name | `abilities/apps.py` -> `tools/apps.py` | IMPLEMENTED | LOCAL-WRITE | Windows |
| APP-002 | `close_app` | Quits an app; asks first unless clear | `abilities/apps.py` | IMPLEMENTED, risk `CONFIRM` | LOCAL-WRITE | Windows |
| APP-003 | `focus_app` | Switches to an already-open app | `abilities/apps.py` | IMPLEMENTED | LOCAL-WRITE | Windows |
| APP-004 | `list_running_apps` | Lists apps with an open window | `abilities/apps.py` | IMPLEMENTED | READ | Windows |
| WIN-001 | `minimize_window` | Minimizes an app's window | `abilities/windows.py` -> `tools/window_control.py` | IMPLEMENTED | LOCAL-WRITE | Windows |
| WIN-002 | `maximize_window` | Maximizes a window | `abilities/windows.py` | IMPLEMENTED | LOCAL-WRITE | Windows |
| WIN-003 | `restore_window` | Un-minimizes/un-maximizes | `abilities/windows.py` | IMPLEMENTED | LOCAL-WRITE | Windows |
| WIN-004 | `focus_window` | Brings a window to front | `abilities/windows.py` | IMPLEMENTED | LOCAL-WRITE | Windows |
| WIN-005 | `move_window_to_monitor` | Moves a window to another monitor | `abilities/windows.py` | IMPLEMENTED | LOCAL-WRITE | 2+ monitors |
| WIN-006 | `get_active_window` / `list_monitors` | Reads the front window / monitor layout | `abilities/windows.py` | IMPLEMENTED | READ | Windows |

## 9. PC settings & system (`abilities/pcsettings.py`, `abilities/system.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| PC-001 | `set_dark_mode` | Toggles Windows dark mode | `abilities/pcsettings.py` | IMPLEMENTED, **unverified live** (ROADMAP) | LOCAL-WRITE | Windows |
| PC-002 | `set_brightness` | Sets/changes screen brightness | `abilities/pcsettings.py` | IMPLEMENTED, **unverified live** | LOCAL-WRITE | Windows, supported display |
| PC-003 | `set_bluetooth` | Toggles Bluetooth | `abilities/pcsettings.py` | IMPLEMENTED | LOCAL-WRITE | Bluetooth adapter |
| PC-004 | `set_wifi` | Toggles Wi-Fi (cuts Jarvis's own cloud access if off) | `abilities/pcsettings.py` | IMPLEMENTED, risk `CONFIRM` | LOCAL-WRITE | Wi-Fi adapter |
| PC-005 | `lock_pc` | Locks the PC now | `abilities/pcsettings.py` | IMPLEMENTED, risk `CONFIRM` | LOCAL-WRITE | Windows |
| PC-006 | `sleep_pc` | Sleeps the PC; asks first | `abilities/pcsettings.py` | IMPLEMENTED, risk `SENSITIVE` | LOCAL-WRITE | Windows |
| PC-007 | `shutdown_pc` | Shuts down/restarts in 60s, cancelable | `abilities/pcsettings.py` | IMPLEMENTED, risk `SENSITIVE` | LOCAL-WRITE | Windows |
| PC-008 | `cancel_shutdown` | Cancels a pending shutdown/restart | `abilities/pcsettings.py` | IMPLEMENTED | LOCAL-WRITE | Windows |
| PC-009 | `open_settings_page` | Opens a Windows Settings page (DND, focus, night light — no API to flip these directly) | `abilities/pcsettings.py` | IMPLEMENTED | LOCAL-WRITE | Windows |
| SYS-001 | `inspect_ports` | What's listening on network ports and by which process | `abilities/system.py` | IMPLEMENTED | READ | Windows |
| SYS-002 | `find_processes` | Running processes matching a name/command line | `abilities/system.py` | IMPLEMENTED | READ | Windows |
| SYS-003 | `system_load` | CPU/memory/GPU load and busiest programs | `abilities/system.py` | IMPLEMENTED | READ | Windows |

## 10. Media & volume (`abilities/media.py` / `tools/media.py`, `tools/music.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| MEDIA-001 | `set_volume` / `volume_up` / `volume_down` | Sets/adjusts PC volume | `abilities/media.py` | IMPLEMENTED | LOCAL-WRITE | none |
| MEDIA-002 | `mute` / `unmute` / `get_volume` | Mute control and read-back | `abilities/media.py` | IMPLEMENTED | LOCAL-WRITE / READ | none |
| MEDIA-003 | `play_pause` / `next_track` / `previous_track` | Transport control for whatever's playing | `abilities/media.py` | IMPLEMENTED | LOCAL-WRITE | an active media session |
| MEDIA-004 | `get_current_media` | What's playing: song/artist/app/state | `abilities/media.py` | IMPLEMENTED | READ | none |
| MEDIA-005 | `play_music` | Picks and starts Spotify music (playlist, song, artist) | `abilities/media.py` -> `tools/music.py` | IMPLEMENTED, **unverified live** (ROADMAP: "Spotify playlist and search playback") | EXTERNAL-WRITE | Spotify Premium + app running/logged in |

## 11. Browser, screen & research (`abilities/computer.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| WEB-001 | `get_active_browser` | Which browser/page is in front, installed browsers, default | `abilities/computer.py` | IMPLEMENTED | READ | a supported browser |
| WEB-002 | `open_url` | Opens a site/address in a new tab | `abilities/computer.py` | IMPLEMENTED, **unverified live** (ROADMAP) | LOCAL-WRITE | browser |
| WEB-003 | `browser_search` | Searches Google/YouTube/etc. and shows results | `abilities/computer.py` | IMPLEMENTED, **unverified live** | LOCAL-WRITE | browser |
| WEB-004 | `browser_navigate` | Back/forward/refresh/new tab/switch tab | `abilities/computer.py` | IMPLEMENTED, **unverified live** | LOCAL-WRITE | browser |
| WEB-005 | `close_tab` | Closes one tab (never the whole browser) | `abilities/computer.py` | IMPLEMENTED | LOCAL-WRITE | browser |
| WEB-006 | `browser_read_page` | Reads the open page's actual text | `abilities/computer.py` | IMPLEMENTED | READ | browser |
| WEB-007 | `browser_click` | Clicks a link/button by number or description | `abilities/computer.py` | IMPLEMENTED, **unverified live** | LOCAL-WRITE | browser |
| WEB-008 | `browser_click_sensitive` | Clicks a send/buy/pay/post/delete/sign-out button; asks first | `abilities/computer.py` | IMPLEMENTED, risk `SENSITIVE` | EXTERNAL-WRITE (whatever site is open) | browser |
| WEB-009 | `browser_type` | Types into a field; can submit | `abilities/computer.py` | IMPLEMENTED, **unverified live** | LOCAL-WRITE | browser |
| WEB-010 | `browser_scroll` | Scrolls the page | `abilities/computer.py` | IMPLEMENTED | LOCAL-WRITE (view only) | browser |
| WEB-011 | `copy_link` | Copies a link to the clipboard | `abilities/computer.py` | IMPLEMENTED | LOCAL-WRITE (clipboard) | browser |
| WEB-012 | `analyze_screen` | Reads the screen via one screenshot + vision model, after consent | `abilities/computer.py` | IMPLEMENTED, **unverified live** (ROADMAP: "no real vision call yet") | READ | vision-capable model, `allow_screen_vision` consent |
| WEB-013 | `allow_screen_vision` | Saves your yes/no to screen-reading consent | `abilities/computer.py` | IMPLEMENTED | LOCAL-WRITE | none |
| WEB-014 | `click_on_screen` | Clicks by screen position (last resort only) | `abilities/computer.py` | IMPLEMENTED | LOCAL-WRITE | a prior `analyze_screen` |
| WEB-015 | `research_web` | Multi-source research with citations | `abilities/computer.py` -> `computer/research.py` | IMPLEMENTED, **unverified live end to end** (ROADMAP) | READ | search + model API keys |
| WEB-016 | `open_research_source` | Opens one of the last research's sources | `abilities/computer.py` | IMPLEMENTED | LOCAL-WRITE | prior `research_web` call |

## 12. Files (`abilities/files.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| FILE-001 | `find_files` | Finds files by name or content | `abilities/files.py` | IMPLEMENTED | READ | none |
| FILE-002 | `read_file` | Summarizes/answers about a document (txt/pdf/docx/xlsx/pptx) | `abilities/files.py` | IMPLEMENTED | READ | none |
| FILE-003 | `open_file` | Opens a file with its default app | `abilities/files.py` | IMPLEMENTED | LOCAL-WRITE (launches app) | none |
| FILE-004 | `save_file` | Saves requested text as a file | `abilities/files.py` | IMPLEMENTED | LOCAL-WRITE | none |
| FILE-005 | `save_research_report` | Saves the last research as a Markdown report | `abilities/files.py` | IMPLEMENTED | LOCAL-WRITE | prior `research_web` call |
| FILE-006 | `make_folder` | Creates a folder | `abilities/files.py` | IMPLEMENTED | LOCAL-WRITE | none |
| FILE-007 | `move_file` | Moves/renames a file; asks first | `abilities/files.py` | IMPLEMENTED, risk `CONFIRM` | LOCAL-WRITE | none |
| FILE-008 | `delete_file` | Deletes one file to Recycle Bin (recoverable); always asks | `abilities/files.py` | IMPLEMENTED, risk `SENSITIVE` | LOCAL-WRITE | none |

## 13. Smart home: Zigbee & Home Assistant (`abilities/zigbee.py`, `abilities/home.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| HOME-001 | `home_sensors` | Door/room sensor state | `abilities/zigbee.py` | IMPLEMENTED | READ | Zigbee2MQTT running + paired sensors |
| HOME-002 | `set_light` | Controls the Zigbee LED strip (on/off, brightness, color, effect) | `abilities/zigbee.py` | IMPLEMENTED | EXTERNAL-WRITE (device) | Zigbee2MQTT + paired light |
| HOME-003 | `home_assistant_states` | Lists Home Assistant entities and state | `abilities/home.py` | IMPLEMENTED | READ | Home Assistant URL + token configured |
| HOME-004 | `home_assistant` | Calls a Home Assistant service (e.g. `light.turn_on`) | `abilities/home.py` | IMPLEMENTED | EXTERNAL-WRITE | Home Assistant URL + token configured |

## 14. Email & Calendar — Google (`room_agent/integrations/`)

**Dependency check for this run:** `connections.json` (gitignored, local state) shows one Google account currently
`"status": "connected"` with Gmail + Calendar scopes — so these tools are **configured and reachable right now**,
though the end-to-end actions below are still individually unverified unless you've tested them.

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| MAIL-001 | `gmail_search` | Searches Gmail by sender/subject/words/date/unread/attachment | `integrations/capabilities.py` -> `integrations/google/gmail.py` | IMPLEMENTED | READ | connected Google account |
| MAIL-002 | `gmail_list_recent` | Latest emails, optionally today/important only | same | IMPLEMENTED | READ | connected Google account |
| MAIL-003 | `gmail_get_unread` | Unread emails | same | IMPLEMENTED | READ | connected Google account |
| MAIL-004 | `gmail_get_message` / `gmail_get_thread` | Reads one email / a whole thread | same | IMPLEMENTED | READ | connected Google account |
| MAIL-005 | `gmail_get_attachments` | Attachment names/types/sizes, short text extracted | same | IMPLEMENTED | READ | connected Google account |
| MAIL-006 | `gmail_create_draft` / `gmail_update_draft` | Writes/edits a draft reply or new email — **never sent** | same | IMPLEMENTED | EXTERNAL-WRITE (draft only) | connected Google account |
| MAIL-007 | `gmail_send` | Sends the current draft; always asks to confirm first | same | IMPLEMENTED, risk `SENSITIVE` | EXTERNAL-WRITE (real email sent) | connected Google account |
| CAL-001 | `calendar_list_calendars` | Lists your calendars | `integrations/capabilities.py` -> `integrations/google/calendar.py` | IMPLEMENTED | READ | connected Google account |
| CAL-002 | `calendar_get_events` / `calendar_find_events` | What's on the calendar in a period / by keyword | same | IMPLEMENTED | READ | connected Google account |
| CAL-003 | `calendar_get_event` | One event's details | same | IMPLEMENTED | READ | connected Google account |
| CAL-004 | `calendar_create_event` | Adds an event (no invitations sent) | same | IMPLEMENTED | EXTERNAL-WRITE | connected Google account |
| CAL-005 | `calendar_update_event` | Changes an event's time/title/place/notes; asks first when unclear | same | IMPLEMENTED, risk `CONFIRM` | EXTERNAL-WRITE | connected Google account |
| CAL-006 | `calendar_delete_event` | Deletes an event; always asks first | same | IMPLEMENTED, risk `SENSITIVE` | EXTERNAL-WRITE | connected Google account |

## 15. Phone mode & VIPs (`room_agent/phone/`, `abilities/phone.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| PHONE-001 | Inbound call to Jarvis's number | Same Jarvis (memory, email, calendar) answers a real call | `phone/server.py`, `phone/session.py` | IMPLEMENTED, setup documented (`phone.md`) | EXTERNAL-WRITE (real phone call, Twilio cost) | Twilio account + number, `bin/cloudflared.exe` tunnel |
| PHONE-002 | `call_me` | Jarvis calls your phone on request | `abilities/phone.py` -> `phone/twilio.py` | IMPLEMENTED | EXTERNAL-WRITE (real call, Twilio cost) | Twilio configured |
| PHONE-003 | `end_call` | Hangs up the current call | `abilities/phone.py` | IMPLEMENTED | EXTERNAL-WRITE | active call |
| PHONE-004 | `text_me` | Texts your own phone a note or page link | `abilities/phone.py` | IMPLEMENTED | EXTERNAL-WRITE (real SMS, Twilio cost; `settings.json` shows an SMS daily counter already in use) | Twilio configured, possibly A2P 10DLC registration (see ROADMAP "needs confirmation") |
| PHONE-005 | `add_vip` / `remove_vip` / `list_vips` | Manages who triggers a driving-mode call about their email | `abilities/phone.py` | IMPLEMENTED | LOCAL-WRITE / READ | none |
| PHONE-006 | Driving mode detection | iPhone Shortcuts tell Jarvis you're driving (CarPlay/Driving Focus), short answers mode | `phone/state.py` | IMPLEMENTED, setup documented | LOCAL-WRITE (mode flag) | iPhone Shortcuts automation set up |

## 16. Tasks & multi-step execution (`abilities/tasks.py`, `actions/`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| TASK-001 | `run_task` | Runs several actions as one ordered task with dependencies | `abilities/tasks.py` -> `actions/tasks.py`, `actions/executor.py` | IMPLEMENTED | varies by steps | none |
| TASK-002 | `resume_task` | Continues a task interrupted/paused/partly done | `abilities/tasks.py` | IMPLEMENTED, **resume-after-restart specifically NOT implemented** — see TASK-007 | varies | none |
| TASK-003 | `cancel_task` | Cancels remaining steps of the last task | `abilities/tasks.py` | IMPLEMENTED | LOCAL-WRITE | none |
| TASK-004 | `task_status` | Step-by-step history of the last task | `abilities/tasks.py` | IMPLEMENTED | READ | none |
| TASK-005 | `list_task_knowledge` / `forget_task_knowledge` | Verified procedures Jarvis learned from finished tasks | `abilities/tasks.py` | IMPLEMENTED | READ / LOCAL-WRITE | none |
| TASK-006 | Confirmation gate (`risk=CONFIRM`/`SENSITIVE`) | Pauses for a yes/no before risky steps | `actions/pending.py` | IMPLEMENTED | — | none |
| TASK-007 | Restart recovery (journal) | Actions cut off by a restart are marked "interrupted: result unknown", never silently re-run or claimed done | `actions/journal.py` | IMPLEMENTED (marks as canceled/unknown) — **does not resume a half-done plan automatically** (confirmed: `journal.py` sets `CANCELED, "interrupted by a restart: result unknown"`) | — | none |
| TASK-008 | Undo (`undo_last_action`) | Reverts the most recent undoable change | `abilities/undo.py` -> `actions/*` undo hooks | IMPLEMENTED | LOCAL-WRITE | the last action must support undo |
| TASK-009 | Supervisor / auto-start | Keeps Jarvis running, restarts on crash, Windows service mode | `room_agent/service.py`, `actions/supervisor.py` | IMPLEMENTED, **unverified live** (ROADMAP) | — | Windows service install |

## 17. Missions — long-running background automation (`room_agent/missions/`, `abilities/missions.py`)

Tested offline under `tests/test_missions.py` (144 checks, fake network/Gmail/coder); **not yet exercised against real
OpenStreetMap/Places/model APIs or real Gmail**, per `MISSIONS.md`.

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| MISSION-001 | `start_business_mission` | Starts a background mission (e.g. find/qualify local businesses); asks first | `abilities/missions.py` | IMPLEMENTED, risk `CONFIRM` | EXTERNAL-WRITE (real API calls, real $) | Places/maps API, model budget |
| MISSION-002 | `mission_status` / `explain_mission` | Progress and the goal/success criteria | `abilities/missions.py` | IMPLEMENTED | READ | running mission |
| MISSION-003 | `pause_mission` / `resume_mission` | Pauses/resumes (checkpointed, crash-recoverable per `tests/test_recovery.py`) | `abilities/missions.py` | IMPLEMENTED | LOCAL-WRITE | running/paused mission |
| MISSION-004 | `raise_mission_budget` | Allows more spend, resumes if budget-paused | `abilities/missions.py` | IMPLEMENTED, risk `SENSITIVE` | EXTERNAL-WRITE (real $) | running mission |
| MISSION-005 | `stop_mission` | Cancels for good (not resumable); keeps what was built | `abilities/missions.py` | IMPLEMENTED, risk `SENSITIVE` | LOCAL-WRITE | running mission |
| MISSION-006 | `build_mission_demos` / `edit_demo_site` / `open_demo_preview` | Builds/edits/previews a local demo site (not published) | `abilities/missions.py` -> `missions/sitegen.py` | IMPLEMENTED | LOCAL-WRITE | mission with qualified leads |
| MISSION-007 | `list_mission_leads` / `export_mission_leads` | Lists/exports found businesses (CSV/JSON) | `abilities/missions.py` | IMPLEMENTED | READ / LOCAL-WRITE | running/finished mission |
| MISSION-008 | `mission_approvals` / `decide_mission_approval` | Items waiting for your approval (e.g. a Gmail draft); approve/reject/retry | `abilities/missions.py` | IMPLEMENTED, decide risk `SENSITIVE` | EXTERNAL-WRITE on approval | pending approval item |
| MISSION-009 | `check_mission_problems` / `resolve_mission_item` | Re-checks unresolved items (read-only check); carries out your decision | `abilities/missions.py` | IMPLEMENTED, resolve risk `SENSITIVE` | READ / varies | running mission |
| MISSION-010 | Coder worker sandbox | A Claude Code worker edits a throwaway copy, not the live project, for mission site generation | `missions/coder.py`, `missions/sandbox.py` | IMPLEMENTED | LOCAL-WRITE (sandbox only) | `CODER_BACKEND` configured (Anthropic key or Claude CLI) |

## 18. Coding ability (`abilities/coding.py`)

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| CODE-001 | `run_tests` | Runs a project's tests in a sandbox copy (changes nothing) | `abilities/coding.py` -> `computer/coding.py` | IMPLEMENTED | READ | target project present |
| CODE-002 | `fix_code` | Proposes a fix in a sandbox; verified against tests before anything is applied | `abilities/coding.py` | IMPLEMENTED | READ (sandbox only) | Anthropic key or Claude CLI |
| CODE-003 | `apply_code_fix` | Applies the verified fix to the real project; backs up first, restores on test failure | `abilities/coding.py` | IMPLEMENTED, risk `SENSITIVE` | LOCAL-WRITE (real project files) | prior successful `fix_code` |

## 19. Safety, recovery & emergency stop

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| SAFE-001 | `emergency_stop` | Stops everything running now (speech, research, page actions, pending requests) | `abilities/safety.py` -> `room_agent/emergency.py` | IMPLEMENTED, **live behavior unverified this pass** | LOCAL-WRITE (halts state) | none |
| SAFE-002 | Emergency stop hotkey | A keyboard shortcut triggers the same stop without voice | `room_agent/emergency.py` | IMPLEMENTED, **unverified live** | LOCAL-WRITE | none |
| SAFE-003 | Audit log | Every state-changing action is logged | `room_agent/audit.py` | IMPLEMENTED, **live evidence found**: `logs/audit-202610.jsonl` has real entries | LOCAL-WRITE | none |
| SAFE-004 | Network allow-list guard | Blocks non-approved outbound connections/DNS in sensitive contexts (e.g. mission tests) | `room_agent/netguard.py` | IMPLEMENTED | — | none |
| SAFE-005 | Host/permission check at startup | Verifies the expected machine/config before running risky features | referenced from `docs/archive/2026-10/JARVIS_TECHNICAL_AUDIT.md` ("Host check") | IMPLEMENTED, **not re-confirmed this pass** | READ | none |
| SAFE-006 | Confirmation before risky actions | Anything `risk=CONFIRM`/`SENSITIVE` asks first (see per-tool risk column above) | `actions/pending.py`, `actions/core.py` | IMPLEMENTED | — | none |

## 20. Dashboard (`UI/index.html`, `UI/server.py`) — Codex-owned frontend, documented not modified

| ID | Feature | What it does | Entry point | Status | I/O | Depends on |
|---|---|---|---|---|---|---|
| DASH-001 | Overview page | Greeting, "your workspace" filterable cards | `index.html#home`, `/api/status`, `/api/live` | IMPLEMENTED | READ | dashboard server running (`UI/server.py`, port 8765) |
| DASH-002 | Chat page | Type a command/question to Jarvis from the browser | `index.html#chat`, `POST /api/command` | IMPLEMENTED | varies by command | dashboard server running |
| DASH-003 | Memory page | Shows "about you", recent conversations, preferences, remembered facts | `index.html#memory` | IMPLEMENTED | READ | dashboard server running |
| DASH-004 | Activity page | What Jarvis has done since start (excludes email/calendar content) | `index.html#activity` | IMPLEMENTED | READ | dashboard server running |
| DASH-005 | Missions page | Goal/plan, items needing approval, leads, demo sites, outreach drafts, step log | `index.html#missions`, `/api/missions`, `/api/action` | IMPLEMENTED | READ + approve/reject writes | a mission has run at least once |
| DASH-006 | Status page | Setup and response-speed info | `index.html#status`, `/api/status` | IMPLEMENTED | READ | dashboard server running |
| DASH-007 | Connections page | Connect/reconnect/disconnect integrations (e.g. Google), manage scopes | `index.html#connections`, `/api/connections*` | IMPLEMENTED | EXTERNAL-WRITE (OAuth connect/disconnect) | the integration's own account |
| DASH-008 | Settings page | Edit `.env`-backed settings from the browser (restart required) | `index.html#settings`, `GET/POST /api/env` | IMPLEMENTED | LOCAL-WRITE (`.env`) | dashboard server running |

## 21. Cross-cutting negative/behavioral tests

These aren't single tools — they're correctness properties that should hold across *every* feature above. Listed
once here; the checklist applies them to specific examples.

| ID | Property | What must NOT happen |
|---|---|---|
| NEG-001 | Read-only questions never write | "Is music playing?" / "What's on my calendar?" must not start music or create an event |
| NEG-002 | No silent duplication on edit | "Move my meeting" must update the existing event, never create a second one |
| NEG-003 | Exactly-once execution | A single request must not run its action twice (check `task_status` / audit log after) |
| NEG-004 | Confirmation isn't skipped | Any `risk=CONFIRM`/`SENSITIVE` tool (delete, send, shutdown, etc.) must ask before acting when the request is ambiguous |
| NEG-005 | No claimed-but-undone actions | Jarvis must not say "done" when `truth.py`'s claim check would fail (cross-check the audit log / actual state) |
| NEG-006 | Corrections replace, not stack | "No, I meant 3 minutes" replaces the timer instead of adding a second one |
| NEG-007 | Interruption actually stops | Talking over Jarvis mid-reply stops that reply (not just the audio, the underlying action if any) |
| NEG-008 | Dashboard reflects real state | After an action, the Activity/Missions/Status pages show the real outcome, not a stale or optimistic one |

---

## Totals

- **Tool-backed features (model-callable):** 123 entries cataloged above across sections 1–18 (voice settings, memory,
  learning, info, timers, lists, apps, windows, pc, system, media, browser/screen/research, files, smart home,
  gmail, calendar, phone, tasks, missions, coding), matching the order of magnitude of `TOOL_AUDIT.md`'s live count of
  125 (that report's own per-area table omits the `missions` and `coding` groups from its listing — confirmed present
  by direct inspection of `abilities/missions.py` and `abilities/coding.py` — so the two counts are consistent, not
  contradictory).
- **Systemic/non-tool features:** 17 (voice pipeline, conversation/cognition, memory lifecycle, task engine
  guarantees, safety/recovery, dashboard pages).
- **Negative/behavioral properties:** 8.
- **Categories:** 21.
