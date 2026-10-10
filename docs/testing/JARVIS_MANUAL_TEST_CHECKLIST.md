# Jarvis manual test checklist

Companion to `JARVIS_FEATURE_CATALOG.md` (same IDs). Work through this yourself at whatever pace you like, then send
me the results — I will not run or score anything in this document myself per your instructions.

**For every row, write one of `PASS` / `FAIL` / `SKIPPED` / `BLOCKED` in the Result column** (SKIPPED = you chose not
to run it; BLOCKED = you couldn't, e.g. missing account/hardware). Add anything you noticed in Notes.

## Risk tiers — read before testing

- **A — Safe, read-only.** Nothing changes. Test freely.
- **B — Reversible local test.** Changes something on this PC only, and it's easy to undo or doesn't matter (volume,
  timers, lists, memory, window position...).
- **C — External-service write, needs your authorization.** Changes a real cloud account (Gmail draft, Calendar
  event, Home Assistant device, Spotify playback). Only run these on an account/device you're fine touching.
- **D — Paid, irreversible, destructive, or security-sensitive.** Costs real money, can't be undone, or is a
  security-relevant control (shutdown, delete, send, PC lock). A safe mocked/simulated alternative is given where
  possible — **prefer the alternative unless you specifically want to test the real thing.**

---

## 1. Voice, speech & audio

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| VOICE-001 | Say "Hey Jarvis" from across the room | It wakes and responds | You hear/see a response | A | | |
| VOICE-002 | Say a short sentence after waking | Text matches what you said (check a debug log if `DIAGNOSTICS=1`) | Compare spoken vs. transcribed | A | | |
| VOICE-003 | After a reply, keep talking without saying "Hey Jarvis" again within ~4s | Jarvis responds to the follow-up | Listen for a reply | A | | |
| VOICE-004 | Start talking while Jarvis is mid-reply | Jarvis stops talking and listens | Reply audio cuts off | A | | |
| VOICE-005 | Have someone else (or a recording) try to interrupt/command Jarvis | It should not respond as if you said it (if voiceprint is enrolled) | Compare behavior enrolled vs. not | B | | **Needs:** `main.py --enroll-voice` run first |
| VOICE-006 | Ask any question | Reply is spoken aloud | You hear audio | A | | |
| VOICE-007 | "Talk to me slower from now on, and don't forget it" | Rate changes and stays changed next turn | Listen to pacing | B | | |
| VOICE-008 | "Speak faster" / "normal speed" | Rate changes accordingly | Listen | B | | |
| VOICE-009 | "Sound warmer" / "be more engaged" | Tone changes | Listen | B | | |
| VOICE-010 | "What voices can I use?" | Lists available voices | Compare to response | A | | |
| VOICE-011 | "Switch to a British man's voice" (or a name from VOICE-010) | Voice changes | Listen | B | | |
| VOICE-012 | "Say AimChart like 'aim chart'" then say "AimChart" in a later sentence | Jarvis pronounces it that way afterward | Listen | B | | |
| VOICE-013 | "Go back to saying AimChart normally" | Reverts the custom pronunciation | Listen | B | | |
| VOICE-014 | "Go quiet" then say something without the wake word | Jarvis stays silent until "Hey Jarvis" again | Observe silence | B | | |

## 2. Conversation & cognition

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| CONV-001 | Say a clear command, then a casual remark, then a question | Jarvis handles each differently (no "want me to...?" after a clear command) | Listen to phrasing | A | | |
| CONV-002 | Set a timer, then immediately say "no, I meant 3 minutes" | The timer is corrected, not duplicated | Run TIMER-003 after | B | | |
| CONV-003 | Say something purely social ("how's it going?") | No tool call happens, just conversation | Check dashboard Activity page shows no new action | A | | |
| CONV-004 | Speak in a frustrated or excited tone | Jarvis's reply tone adapts, without naming the emotion | Listen | A | | |
| CONV-005 | Ask Jarvis to do something, then ask "did you actually do that?" | The answer matches what the audit log / real state shows | Check `logs/audit-*.jsonl` or dashboard Activity | A | | |
| CONV-006 | Use Jarvis for a day and check `spend.json` | Costs appear under the expected provider(s) | View `spend.json` | A | | |

## 3. Memory

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| MEM-001 | "Remember that my sister's name is Sara" | Confirms it's stored | Ask MEM-002 next | B | | |
| MEM-002 | "What do you remember about my sister?" | Recalls what was stored | Compare to what you said | A | | |
| MEM-003 | "Forget what I told you about my sister" | Confirms deletion; MEM-002 no longer finds it | Re-ask MEM-002 | B | | |
| MEM-004 | Repeat a near-duplicate fact over several days | Facts merge instead of piling up | Check Memory dashboard page | B | | multi-day test |

## 4. Learning / preferences

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| LEARN-001 | Correct the same kind of thing twice (e.g. volume level after music starts) | Jarvis learns and applies it next time | Observe next similar request | B | | |
| LEARN-002 | "What have you learned about me?" | Lists learned preferences | Compare to your actual habits | A | | |
| LEARN-003 | After an automatic action, "why did you do that?" | Explains the preference behind it | Listen | A | | |
| LEARN-004 | "Forget that preference" | Removes it; LEARN-002 no longer lists it | Re-ask LEARN-002 | B | | |
| LEARN-005 | "Always do X when Y happens" | Confirms the automation is saved | Ask LEARN-002 or trigger Y | B | | |
| LEARN-006 | "Run my morning routine" (if one is set) | Executes the saved steps | Observe each step happen | B | | needs a routine already configured |

## 5. Info & search

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| INFO-001 | "What time is it?" | Correct local time | Compare to a clock | A | | |
| INFO-002 | "What's the weather?" | Current + forecast, matches a weather site | Compare to weather.com/etc. | A | | |
| INFO-003 | "Give me my morning briefing" | Time, weather, headlines, timers, plans in one answer | Listen for all parts | A | | |
| INFO-004 | "Search the web for [something obscure]" | Returns a relevant, current answer | Check it's not stale/wrong | A | | |
| INFO-005 | "Where am I?" | Correct city | Compare to actual location | A | | |

## 6. Timers, alarms & reminders

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| TIMER-001 | "Set a timer for 20 seconds" | Rings in ~20s, keeps ringing until you speak | Wait and listen | B | | |
| TIMER-002 | "Wake me at [2 minutes from now, in 24h clock]" | Alarm fires at that time | Wait and listen | B | | |
| TIMER-003 | "What timers do I have?" | Lists active timers/alarms with time left | Compare to what you set | A | | |
| TIMER-004 | "Cancel that timer" | Confirms cancellation; TIMER-003 shows it gone | Re-ask TIMER-003 | B | | |
| TIMER-005 | "Remind me to take out the trash when I get home" | Confirms it's saved for that moment | Trigger the moment if feasible | B | | |
| TIMER-006 | "What moment reminders do I have?" | Lists them | Compare | A | | |
| TIMER-007 | "Cancel that reminder" | Confirms removed | Re-ask TIMER-006 | B | | |
| TIMER-008 | "How was my day?" | Recap of done/open/tomorrow items | Compare to your actual day | A | | |

## 7. Lists & notes

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| LIST-001 | "Add milk to my shopping list" | Confirms added | Ask LIST-002 | B | | |
| LIST-002 | "What's on my shopping list?" | Lists items | Compare | A | | |
| LIST-003 | "I bought the milk" | Marks it done, not removed | Ask LIST-002 with include_done | B | | |
| LIST-004 | "Remove milk from the list" | Item gone entirely | Ask LIST-002 | B | | |
| LIST-005 | "Clear my shopping list" | Asks for confirmation first, then empties it | Confirm the prompt happens | B | | low-value data, safe to actually run |
| LIST-006 | "Take a note: the wifi password is on the router" | Confirms saved | Ask to read notes back | B | | |

## 8. Apps & windows

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| APP-001 | "Open Notepad" | Notepad launches | See it open | B | | |
| APP-002 | "Close Notepad" | Asks first if ambiguous, then closes it | See it close | B | | |
| APP-003 | "Switch to Chrome" (already open) | Chrome comes to front | See it focus | B | | |
| APP-004 | "What apps are open?" | Lists open apps | Compare to taskbar | A | | |
| WIN-001 | "Minimize this" | Active window minimizes | Observe | B | | |
| WIN-002 | "Maximize Notepad" | Window maximizes | Observe | B | | |
| WIN-003 | "Restore it" | Back to previous size | Observe | B | | |
| WIN-004 | "Bring Discord to the front" | Window focuses | Observe | B | | |
| WIN-005 | "Move this to my second monitor" | Window moves | Observe | B | | needs 2+ monitors |
| WIN-006 | "What window is active?" / "List my monitors" | Correct info | Compare | A | | |

## 9. PC settings & system

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| PC-001 | "Turn on dark mode" | Windows switches to dark mode | Observe | B | | |
| PC-002 | "Set brightness to 50%" | Screen dims to ~50% | Observe | B | | |
| PC-003 | "Turn off Bluetooth" | Bluetooth toggles off | Check Windows settings | B | | |
| PC-004 | "Turn off Wi-Fi" | Asks to confirm (it kills Jarvis's own cloud access) — **say no** | Confirmation prompt appears | D | | **Alternative:** just confirm the prompt appears, then decline |
| PC-005 | "Lock my PC" | Asks to confirm, then locks | Confirmation + lock screen | D | | **Alternative:** confirm the prompt, then say no |
| PC-006 | "Put the PC to sleep" | Asks first, then sleeps | Confirmation + sleep | D | | **Alternative:** confirm the prompt, then say no |
| PC-007 | "Shut down my PC" | Asks first, 60s cancelable countdown | Confirmation + countdown | D | | **Alternative:** say yes, then immediately "cancel the shutdown" (PC-008) to prove the cancel path instead of letting it actually shut down |
| PC-008 | "Cancel the shutdown" (after PC-007) | Countdown stops | PC stays on | B | | |
| PC-009 | "Open night light settings" | Windows Settings page opens to the right place | Observe | B | | |
| SYS-001 | "What's listening on my network ports?" | Lists ports + owning processes | Compare to `netstat` | A | | |
| SYS-002 | "Find processes with 'python' in the name" | Lists matching processes | Compare to Task Manager | A | | |
| SYS-003 | "How busy is my PC?" | CPU/memory/GPU load | Compare to Task Manager | A | | |

## 10. Media & volume

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| MEDIA-001 | "Set volume to 30%" / "turn it up" | Volume changes accordingly | Observe/listen | B | | |
| MEDIA-002 | "Mute" / "unmute" / "what's my volume?" | Mutes, unmutes, reports correctly | Observe | B / A | | |
| MEDIA-003 | While music plays: "pause" / "next song" / "go back" | Transport responds | Observe playback | B | | needs something playing |
| MEDIA-004 | "What's playing right now?" | Correct song/artist/app/state, **without starting anything** | Compare to actual player — **this is NEG-001: confirm nothing starts if nothing was playing** | A | | |
| MEDIA-005 | "Play some jazz on Spotify" | Starts a real Spotify playback | Observe Spotify | C | | **Alternative:** ask MEDIA-004 only, confirming Jarvis never starts music unasked |

## 11. Browser, screen & research

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| WEB-001 | "Which browser is active, and what page?" | Correct browser/page/title | Compare to screen | A | | |
| WEB-002 | "Open YouTube" | New tab opens, existing tabs untouched | Observe tabs | B | | |
| WEB-003 | "Search Google for cute puppies" | Results page in current/new tab per context | Observe | B | | |
| WEB-004 | "Go back" / "refresh" / "new tab" | Correct navigation | Observe | B | | |
| WEB-005 | "Close this tab" | Only that tab closes, not the browser | Observe | B | | |
| WEB-006 | "Read this page to me" | Accurate summary of the actual page text | Compare to page | A | | |
| WEB-007 | "Click the second result" | Clicks correctly | Observe | B | | |
| WEB-008 | On a page with a "Buy now"/"Send"/"Delete" button: ask Jarvis to click it | Asks you to confirm first, every time | Confirmation prompt appears | D | | **Alternative:** say no at the confirmation; use a throwaway/test page, never a real purchase/send page |
| WEB-009 | "Type 'hello world' in the search box" | Types correctly; doesn't submit unless asked | Observe | B | | |
| WEB-010 | "Scroll down" | Page scrolls | Observe | B | | |
| WEB-011 | "Copy this link" | Clipboard has the right URL | Paste it somewhere | B | | |
| WEB-012 | "What's on my screen?" (after agreeing to WEB-013) | Accurate description of the visible window | Compare to screen | A | | |
| WEB-013 | Ask Jarvis to look at your screen; answer "yes" | Consent is remembered for later screen reads | Ask WEB-012 again without re-asking consent | B | | |
| WEB-014 | "Click the blue button" (after a WEB-012 screenshot, no accessible element) | Clicks the right screen position | Observe | B | | last resort path only |
| WEB-015 | "Research the best espresso machines under $500" | Multi-source answer with sources | Spot-check 2-3 cited sources by hand | A | | |
| WEB-016 | "Open source 2" (after WEB-015) | Opens that exact source | Observe | B | | |

## 12. Files

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| FILE-001 | "Find my resume" | Finds the right file(s) | Compare to File Explorer | A | | |
| FILE-002 | "What does this PDF say about X?" | Accurate answer from the actual file | Compare to the file | A | | |
| FILE-003 | "Open that file" | Opens with its default app | Observe | B | | |
| FILE-004 | "Save this as a note on my Desktop called test.txt" | File created with the right content | Check the file | B | | |
| FILE-005 | After WEB-015: "Save that research as a report" | Markdown file created with sources | Check the file | B | | |
| FILE-006 | "Make a folder called Test on my Desktop" | Folder created | Check File Explorer | B | | |
| FILE-007 | "Rename test.txt to test2.txt" | Asks first if unclear, then renames | Check File Explorer | B | | use a throwaway file |
| FILE-008 | "Delete test2.txt" | Always asks first, then Recycle Bin (not permanent) | Check Recycle Bin | D | | **Alternative:** confirm the prompt, then say no; use only throwaway test files |

## 13. Smart home: Zigbee & Home Assistant

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| HOME-001 | "Is the door open?" / "What do the sensors say?" | Correct current sensor state | Compare to physical device | A | | needs Zigbee2MQTT + paired sensors |
| HOME-002 | "Turn the LED strip blue at 50%" | Light changes for real | Observe the light | C | | real device change |
| HOME-003 | "What Home Assistant entities do I have?" | Lists entities/state | Compare to HA dashboard | A | | needs HA configured |
| HOME-004 | "Turn on the bedroom light" (via Home Assistant) | Real device turns on | Observe the device / HA | C | | |

## 14. Email & Calendar (Google)

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| MAIL-001 | "Search my email for invoices from last month" | Relevant real results | Compare to Gmail web | A | | |
| MAIL-002 | "What are my latest emails?" | Correct recent list | Compare to Gmail | A | | |
| MAIL-003 | "Any unread emails?" | Correct unread list | Compare to Gmail | A | | |
| MAIL-004 | "Read the email from [person]" | Accurate content | Compare to the actual email | A | | |
| MAIL-005 | "What's attached to that email?" | Correct attachment list | Compare to Gmail | A | | |
| MAIL-006 | "Draft a reply saying I'll be there" | Draft created, **not sent** | Check Gmail Drafts folder | C | | |
| MAIL-007 | "Send it" (after MAIL-006) | Asks to confirm, then really sends | Check Gmail Sent folder | D | | **Alternative:** stop after confirming the prompt appears; say no; or send only to yourself |
| CAL-001 | "What calendars do I have?" | Correct list | Compare to Google Calendar | A | | |
| CAL-002 | "What's on my calendar tomorrow?" / "find my meeting with John" | Correct events | Compare to Calendar | A | | |
| CAL-003 | "Details on that event" | Correct details | Compare to Calendar | A | | |
| CAL-004 | "Schedule dinner Friday at 7pm" | Event created, no invitations sent | Check Calendar | C | | |
| CAL-005 | "Move that meeting to 3pm" | **Updates the same event — does not create a duplicate** (NEG-002) | Check Calendar has exactly one event, at the new time | C | | explicit negative test |
| CAL-006 | "Delete that event" | Always asks first, then deletes | Check Calendar | D | | **Alternative:** confirm the prompt, then say no; use a throwaway test event |

## 15. Phone mode & VIPs

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| PHONE-001 | Call Jarvis's real number | It answers as Jarvis, with memory/email/calendar access | Have the call, listen | D | | **costs money (Twilio)**; alternative: review `phone.md` setup and code instead of a real call |
| PHONE-002 | "Call my phone" | Your phone rings from Jarvis | Answer the call | D | | **costs money**; same alternative as above |
| PHONE-003 | "Hang up" (during a call) | Call ends politely | Observe call end | D | | part of a real call test |
| PHONE-004 | "Text that to my phone" | Real SMS arrives | Check your phone | D | | **costs money; may need A2P 10DLC per ROADMAP** |
| PHONE-005 | "Always call me if Sarah emails" / "Who are my VIPs?" / "Stop calling me about Sarah" | VIP added/listed/removed | Ask list_vips to confirm | B | | no real call triggered by this alone |
| PHONE-006 | Connect CarPlay/Driving Focus (per `phone.md`) | Jarvis switches to short-answer driving mode | Observe response style while "driving" flag is set | C | | needs iPhone Shortcuts set up |

## 16. Tasks & multi-step execution

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| TASK-001 | Ask for something that needs multiple steps (e.g. "find a recipe and save it as a file") | Steps run in order, dependencies respected | Watch it happen; check TASK-004 after | B | | |
| TASK-002 | Interrupt a multi-step task, then "continue" | Resumes only the steps never truly finished | Compare to TASK-004 history | B | | |
| TASK-003 | Mid-task: "cancel that" | Remaining steps stop | Observe | B | | |
| TASK-004 | "What happened in that last task?" | Accurate step-by-step history | Compare to what you saw | A | | |
| TASK-005 | "What have you learned from past tasks?" | Lists verified procedures | Compare | A | | |
| TASK-006 | Trigger a risky step mid-task (e.g. a delete) | Task pauses and asks before that step | Observe the pause | B | | |
| TASK-007 | Start a multi-step task, **force-restart Jarvis mid-step**, then ask what happened | That step is reported as "interrupted, result unknown" — **not** claimed done, **not** silently resumed | Check `task_status` / audit log after restart | B | | confirms TASK-007's known limitation (no auto-resume) |
| TASK-008 | "Undo that" after a reversible action | The last change is reverted | Check the actual state | B | | only works for undo-supporting actions |
| TASK-009 | Restart the PC with the Windows service installed | Jarvis auto-starts | Observe after reboot | B | | needs service installed first |

## 17. Missions (background automation)

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| MISSION-001 | "Find and qualify coffee shops near me that need a website" | Asks to confirm (real API spend), then starts | Confirmation prompt; Missions dashboard | D | | **real money**; alternative: read `MISSIONS.md` + run `tests/test_missions.py` (offline, fake network) instead |
| MISSION-002 | "How's the mission going?" / "explain the mission" | Accurate progress/goal | Compare to Missions dashboard | A | | needs a running mission |
| MISSION-003 | "Pause the mission" / "resume it" | Pauses/resumes correctly, checkpoint-safe | Missions dashboard | B | | |
| MISSION-004 | "Let it spend $5 more" | Asks to confirm (real $), then raises budget | Confirmation prompt | D | | real money |
| MISSION-005 | "Stop the mission for good" | Asks to confirm, cancels permanently (keeps findings) | Missions dashboard | D | | irreversible (can't resume) |
| MISSION-006 | "Build a demo site for the top lead" / "make it blue" | Local demo site builds/updates | `open_demo_preview`, check it visually | B | | local preview only, not published |
| MISSION-007 | "List the leads" / "export them as CSV" | Correct list / file created | Check the exported file | A / B | | |
| MISSION-008 | "What's waiting for my approval?" / "approve item 1" | Correct pending items; approving creates the real draft/action | Missions dashboard | D | | approval can trigger a real Gmail draft |
| MISSION-009 | "Check for problems with the mission" | Read-only recheck, no side effects | Missions dashboard | A | | |
| MISSION-010 | (internal — exercised by MISSION-006) | Coder worker only touches a sandbox copy | Confirm the live project is untouched | B | | |

## 18. Coding ability

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| CODE-001 | "Run the tests for [some other small project]" | Runs in a sandbox copy; your real project is untouched | Diff the real project before/after | A | | use a disposable test project, not Jarvis itself |
| CODE-002 | "Find a fix for the failing test" | Proposes a fix, verified against tests in the sandbox only | Confirm nothing changed in the real project yet | A | | |
| CODE-003 | "Apply that fix" | Asks to confirm, backs up, applies, re-runs tests, restores on failure | Diff the real project; check for a backup | D | | **never point this at the live `room-agent` repo during Claude Code/Codex's active work** |

## 19. Safety, recovery & emergency stop

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| SAFE-001 | Start a long action, then "stop everything" | Speech, research, pending actions all halt immediately | Observe immediate stop | B | | |
| SAFE-002 | Trigger the configured emergency-stop hotkey mid-action | Same halt, without voice | Observe | B | | find the hotkey in config first |
| SAFE-003 | After any state-changing action, check the audit log | A matching entry exists | View `logs/audit-*.jsonl` | A | | |
| SAFE-004 | (passive) During a mission test, confirm no unexpected outbound connection happens | Network guard blocks anything not allow-listed | Check mission test output/logs | A | | mainly exercised by `tests/test_missions.py`, not a voice phrase |
| SAFE-005 | Start Jarvis normally | Startup succeeds only on the expected machine/config | Observe startup logs | A | | |
| SAFE-006 | Ask for any `CONFIRM`/`SENSITIVE` action (see catalog Risk column) with ambiguous wording | Jarvis asks before acting | Observe the question | A | | covered individually above (PC-004..008, FILE-008, CAL-006, MAIL-007, etc.) |

## 20. Dashboard

| ID | Test phrase / interaction | Expected result | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| DASH-001 | Open the dashboard Overview page | Greeting + workspace cards load | Visual check | A | | needs `UI/server.py` running |
| DASH-002 | Type a command in the Chat page | Jarvis responds same as by voice | Compare to voice behavior | varies | | |
| DASH-003 | Open the Memory page | Shows real facts/preferences/conversations | Compare to what you told Jarvis | A | | |
| DASH-004 | Open the Activity page | Shows real recent actions, no email/calendar content | Compare to what you did | A | | |
| DASH-005 | Open the Missions page during/after a mission | Matches mission state exactly | Compare to mission status via voice | A | | |
| DASH-006 | Open the Status page | Setup info and latency look right | Compare to known config | A | | |
| DASH-007 | Connections page: connect/disconnect an integration | OAuth flow works; status updates correctly | Observe the flow | C | | use an account you're fine testing with |
| DASH-008 | Settings page: change a value, save, restart Jarvis | New value takes effect after restart | Observe behavior change | B | | |

## 21. Negative / behavioral tests (cross-cutting)

| ID | Test phrase / interaction | Expected result (what must NOT happen) | Verify via | Tier | Result | Notes |
|---|---|---|---|---|---|---|
| NEG-001 | "Is music playing right now?" (with nothing playing) | Answers the question; **music does not start** | Observe player state before/after | A | | |
| NEG-002 | "Move my [real] meeting to 4pm" | The **same** event updates; no duplicate event appears | Check Calendar event count | C | | pairs with CAL-005 |
| NEG-003 | Ask for one simple action once | It runs **exactly once** (check audit log for exactly one matching entry) | `logs/audit-*.jsonl` | A | | |
| NEG-004 | Ask for a `SENSITIVE` action with vague wording ("maybe delete that?") | Jarvis asks before acting, every time | Observe | A | | |
| NEG-005 | Ask Jarvis to do something that's likely to fail (e.g. open a nonexistent app) | It reports failure honestly, not success | Compare claim to reality | A | | |
| NEG-006 | "Set a timer for 5 minutes" then "no, I meant 3" | One timer at 3 minutes, not two timers | TIMER-003 | B | | pairs with CONV-002 |
| NEG-007 | Interrupt Jarvis mid-sentence with a new command | The new command is heard and acted on, old reply stops | Observe | A | | pairs with VOICE-004 |
| NEG-008 | After any action, check the relevant dashboard page | It reflects the real outcome, not a stale/optimistic one | Compare dashboard to reality | A | | pairs with DASH-003/004/005 |

---

## Coverage summary

| Category | Feature count | Tier A (read-only) | Tier B (reversible local) | Tier C (external write) | Tier D (paid/destructive/sensitive) |
|---|---|---|---|---|---|
| 1. Voice, speech & audio | 14 | 5 | 9 | 0 | 0 |
| 2. Conversation & cognition | 6 | 4 | 2 | 0 | 0 |
| 3. Memory | 4 | 1 | 3 | 0 | 0 |
| 4. Learning / preferences | 6 | 2 | 4 | 0 | 0 |
| 5. Info & search | 5 | 5 | 0 | 0 | 0 |
| 6. Timers, alarms & reminders | 8 | 3 | 5 | 0 | 0 |
| 7. Lists & notes | 6 | 1 | 5 | 0 | 0 |
| 8. Apps & windows | 10 | 2 | 8 | 0 | 0 |
| 9. PC settings & system | 12 | 3 | 5 | 0 | 4 |
| 10. Media & volume | 5 | 1 | 3 | 1 | 0 |
| 11. Browser, screen & research | 16 | 5 | 10 | 0 | 1 |
| 12. Files | 8 | 2 | 5 | 0 | 1 |
| 13. Smart home | 4 | 2 | 0 | 2 | 0 |
| 14. Email & Calendar | 13 | 8 | 0 | 3 | 2 |
| 15. Phone mode & VIPs | 6 | 0 | 1 | 1 | 4 |
| 16. Tasks & multi-step | 9 | 2 | 7 | 0 | 0 |
| 17. Missions | 10 | 3 | 3 | 0 | 4 |
| 18. Coding ability | 3 | 2 | 0 | 0 | 1 |
| 19. Safety & recovery | 6 | 4 | 2 | 0 | 0 |
| 20. Dashboard | 8 | 6 | 1 | 1 | 0 |
| 21. Negative/behavioral | 8 | 6 | 1 | 1 | 0 |
| **Total** | **167** | **67** | **74** | **9** | **17** |

Fill in Result for each row as you go — `PASS` / `FAIL` / `SKIPPED` / `BLOCKED` — and send the completed table(s)
back whenever you're ready. I will not begin any testing, fixing, or further analysis until you do.
