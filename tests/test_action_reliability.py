"""Action reliability and the confirmation loop, replaying the live test of 10 Oct 2026, 12:03-12:10 (audit log,
actions journal and the Desktop file read back from the user's PC). Successful conversation is not successful
execution: these check what really happened on disk, in the lists, in memory and through the speaker.

    the 12:03 issue      "...you stop listening to me..." -> a yes/no about adding it to LED_strip_issue.txt, heard ->
                         "Yes, please add it." runs exactly that write, in code (no second question), checked on disk
    the 12:06 call       "can you give me a phone call on my phone?" is their own request (it was refused as "not in
                         their own words"); a confirmation the model never asked is asked by code; one that never
                         reached them (speech failed) is never "you didn't confirm"
    the 12:07-12:10 file an issue goes to the FILE, not a list named like the file; a list item / a memory is never
                         claimed as the file; "I don't see it" is answered with a read-back done in code; "Yes." to
                         Jarvis's own question isn't refused as "not their words"; "I said yes" with a complaint is a yes;
                         a re-call differing by a newline is the same action; no invented "error code 1127"
    also                 two issues in one write; a partial failure never becomes "added both"; the earlier issue is
                         recovered after the message window moved on; timeouts, cancellation, ambiguous answers; a
                         SENSITIVE action still needs its own yes

Offline: the model is scripted, the Desktop is a temp folder, the phone call is a stand-in, the speaker is silent (the
real speaker thread, so "heard" is real: played to the end, or not).
Run:  .venv\\Scripts\\python -m tests.test_action_reliability
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.harness import setup_env  # noqa: E402

HOME = Path(tempfile.mkdtemp()) / "home"
(HOME / "Desktop").mkdir(parents=True)
(HOME / "Documents").mkdir(parents=True)
TMP = setup_env(TTS_PROVIDER="piper", USERPROFILE=str(HOME), LISTS_FILE=str(Path(tempfile.mkdtemp()) / "lists.json"),
                PHONE_MODE="0", PERSONALITY="friend")
from tests.harness import Checker, Conversation  # noqa: E402

from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor, journal, pending, records  # noqa: E402
from room_agent.audio import speaker, tts  # noqa: E402
from room_agent.config import OUT_SR  # noqa: E402
from room_agent.tools import lists  # noqa: E402

t = Checker()
check = t.check
core.ensure_loaded()
DESKTOP = HOME / "Desktop"
NOTE = DESKTOP / "LED_strip_issue.txt"

# the PC-only areas, made available here (temp folder Desktop; a stand-in phone line)
for g in ("files", "phone_requests"):
    if g in core.GROUPS:
        core.GROUPS[g].available = lambda: True
CALLS = []
for name in ("append_to_file", "save_file", "read_file", "call_me"):
    core.REGISTRY[name].available = lambda: True
core.REGISTRY["call_me"].execute = lambda a: (CALLS.append(time.time()), "OK: calling their phone now (it rings in a few "
                                                                          "seconds).")[1]


class SilentSpeaker:
    def __init__(self):
        self.interrupted, self.end, self.cancel_seq = threading.Event(), 0.0, 0

    def play(self, pcm):
        self.end = max(self.end, time.time()) + len(pcm) / 2 / OUT_SR

    def queued_seconds(self):
        return max(0.0, self.end - time.time())

    def flush(self):
        self.end = time.time()

    def is_playing(self):
        return False

    def wait_drained(self):
        pass


SPOKEN, VOICE = [], {"fail": False}


def fake_piper(text):
    if VOICE["fail"]:
        raise RuntimeError("the voice engine failed")
    SPOKEN.append(str(text))
    return iter([b"\x02\x00" * 240])


tts.piper_pcm = fake_piper
Conversation._drain = lambda self: None  # (the real speaker thread plays everything: "heard" is real here)
convo = Conversation(engine=SilentSpeaker())
threading.Thread(target=speaker.speaker_worker, daemon=True).start()


def say(text, scripts=None, calls=None, reply="Okay."):
    SPOKEN.clear()
    results, system, _ = convo.say(text, calls=calls, reply=reply, scripts=scripts)
    return results, system, list(SPOKEN)


def context(system):
    return system


def note_text():
    return NOTE.read_text(encoding="utf-8") if NOTE.exists() else ""


LONG_PHRASE = ("sometimes when i talk to you on a long phrase you stopped and like you stop listening to me even though i "
               "have some poses i have to finish my phrase")
ISSUE_1 = "Assistant stops listening during long phrases; it cuts the user off when they pause mid-sentence."

# ---------------------------------------------------------------------------------------------- 12:03 the first issue
print("12:03 - an issue for the Desktop file, a yes/no, then 'Yes, please add it.':")
results, system, spoken = say(LONG_PHRASE, scripts=[
    {"tools": [("append_to_file", {"name": "LED_strip_issue.txt", "content": ISSUE_1})]},
    {"text": "Want me to add that to LED_strip_issue.txt on your Desktop?"}])
p = pending.current()
check("their words didn't ask to write anything: nothing written, a yes/no question bound to THAT write",
      results and "NEEDS_CONFIRMATION" in results[0] and not NOTE.exists() and p is not None and p.confirm
      and p.capability == "append_to_file" and p.collected.get("content") == ISSUE_1, (results, p))
check("...and the question was heard (played to the end)", p is not None and p.heard is True
      and any("LED_strip_issue.txt" in s for s in spoken), (spoken, getattr(p, "heard", None)))
n_requests = len(convo.requests)
results, system, spoken = say("Yes, please add it.", scripts=[{"text": "SHOULD NOT BE NEEDED"}])
check("'Yes, please add it.' runs exactly that write, in code: no model call, no second question",
      not convo.requests and NOTE.exists() and ISSUE_1 in note_text() and pending.current() is None, (spoken, note_text()))
check("...and says what really happened, from the write's own result", spoken and spoken[0].startswith("Done: added 1 line")
      and "LED_strip_issue.txt" in spoken[0], spoken)

# ---------------------------------------------------------------------------------------------- 12:06 the phone call
print("12:06 - 'can you give me a phone call on my phone?':")
results, system, spoken = say("Okay, can you give me a phone call on my phone?",
                              scripts=[{"tools": [("call_me", {})]}, {"text": "Calling you now."}])
check("their own request: the call is placed at once (it was refused as 'not in their own words')",
      results and results[0].startswith("OK") and len(CALLS) == 1 and pending.current() is None, results)
check("the wordings people use all count as asking for a call", all(core.REGISTRY["call_me"].intent.search(x) for x in (
    "give me a call", "call my phone", "can you call me", "ring my cell", "give me a phone call")))

print("A confirmation that never reached them:")
VOICE["fail"] = True
results, system, spoken = say("ring the phone", scripts=[{"tools": [("call_me", {})]}, {"text": ""}])
p = pending.current()
VOICE["fail"] = False
check("the model asked nothing after the refusal: code asks the question itself (from the action)",
      p is not None and p.question == "Should I call your phone now?", getattr(p, "question", None))
check("...and the speech failed, so it's recorded as NOT heard (no call placed)", p is not None and p.heard is False
      and len(CALLS) == 1, (getattr(p, "heard", None), len(CALLS)))
results, system, spoken = say("Did you call me?", reply="No, I haven't yet.")
check("next turn the model is told the question never reached them: ask again, never 'you didn't confirm'",
      "did NOT reach them" in system and "never say they didn't answer or didn't confirm" in system, system[-600:])
p = pending.current()
if p is not None:
    p.touched_turn -= 10  # (it times out unanswered)
results, system, spoken = say("so did you call or not?", reply="Not yet: you never heard me ask.")
check("a question that timed out unheard is still never 'you didn't confirm' (the model is told it was never heard)",
      pending.current() is None and "unheard_question" in system and "never heard the" in system, system[-500:])
pending.cancel("test")
rt.unheard_question = None

# ---------------------------------------------------------------------------------------------- 12:07-12:10 the file
print("12:07-12:10 - lists, memory and the file kept apart:")
results, system, spoken = say("I don't see it on the LED strip issue.", scripts=[
    {"tools": [("add_to_list", {"item": "Confirmation wasn't spoken before calling", "list": "LED_strip_issue.txt"})]},
    {"text": "Added it to LED_strip_issue.txt."}])
check("'add it to LED_strip_issue.txt' is never a LIST named like the file: refused, with the right tool named",
      results and results[0].startswith("FAILED") and "append_to_file" in results[0]
      and "led_strip_issue.txt" not in lists._load(), (results, list(lists._load())))
check("...and 'Added it to LED_strip_issue.txt' is never spoken without a file write", not any("Added it" in s for s in spoken),
      spoken)
check("'I don't see it' was checked in code first: the file is read back for the model",
      "checked just now" in convo.requests[0]["messages"][-1]["content"] if convo.requests else False)

results, system, spoken = say("remember that Twilio throws an error when it calls me", scripts=[
    {"tools": [("remember", {"content": "Twilio throws an error when it calls Adam"})]},
    {"text": "I've added the Twilio error to LED_strip_issue.txt."}])
check("a memory isn't the file: 'added ... to LED_strip_issue.txt' after only remember() is never spoken",
      results and results[0].startswith("OK") and not any("LED_strip_issue" in s for s in spoken) and "Twilio" not in
      note_text(), (results, spoken))
results, system, spoken = say("I'm not seeing the Twilio one in the LED strip issue file either.",
                              reply="Right, it's only in memory, not the file. Adding it now.")
msg = convo.requests[0]["messages"][-1]["content"] if convo.requests else ""
check("'I'm not seeing it': the check shows the file is there WITHOUT the Twilio line, and that it's only in memory",
      "LED_strip_issue.txt (Desktop" in msg and "'Twilio throws an error when it calls Adam': NO" in msg
      and "memory (not a file) has" in msg, msg[-700:])
check("...and the records line tells the model where everything went (memory and lists are not the file)",
      "recent_records" in system and "Jarvis's memory (not a file)" in system and "the file LED_strip_issue.txt" in system,
      system[-900:])

print("The 12:10 'Yes.' to Jarvis's own question:")
results, system, spoken = say("so what about the Twilio thing and the confirmation thing?", reply=(
    "They're not in the file yet. Want me to add the Twilio error and the confirmation issue to LED_strip_issue.txt?"))
two = "Twilio calls throw an error when Jarvis calls the user.\nThe yes/no question before calling wasn't spoken."
results, system, spoken = say("Yes.", scripts=[
    {"tools": [("append_to_file", {"name": "LED_strip_issue.txt", "content": two + "\n"})]},
    {"text": "Done, both are in LED_strip_issue.txt now."}])
check("'Yes.' to Jarvis's own question about adding them is their request: written at once, no 'their own words' "
      "refusal and no second question", results and results[0].startswith("OK") and "Twilio calls throw" in note_text()
      and "wasn't spoken" in note_text() and pending.current() is None, (results, note_text()[-300:]))
check("...two distinct issues, one per line, everything before kept", note_text().count("\n") >= 3
      and ISSUE_1 in note_text(), note_text())
check("...and 'both are in the file' is spoken only because the write was checked", any("both are in" in s for s in spoken),
      spoken)

print("'Why are you asking too many questions? I said yes.':")
check("a yes next to a question that only complains about being asked is a yes",
      executor.said_yes("Why are you asking too many questions?  I said yes.")
      and not executor.said_yes("Yes? Which file?") and not executor.said_yes("Can you do it later? Yes."))
results, system, spoken = say("put the dashboard flicker issue in the LED strip file too", scripts=[
    {"tools": [("append_to_file", {"name": "LED_strip_issue.txt", "content": "Dashboard flickers when it reloads."})]},
    {"text": "Okay."}])
check("(that one was asked in their own words: written at once)", results and results[0].startswith("OK"), results)
results, system, spoken = say("clear the screen notes thing", scripts=[
    {"tools": [("append_to_file", {"name": "screen notes.txt", "content": "Clean up the screen notes."})]},
    {"text": "Want me to add that to screen notes.txt?"}])
results, system, spoken = say("Why are you asking too many questions?  I said yes.", scripts=[{"text": "SHOULD NOT"}])
check("...through the real turn: the pending write runs, no model call, no loop", not convo.requests
      and (DESKTOP / "screen notes.txt").exists(), spoken)
check("a re-call that differs only by a trailing newline is the same action (not a new question)",
      executor._norm_args("append_to_file", {"content": "a\nb\n"}) == executor._norm_args("append_to_file",
                                                                                         {"content": "a\nb"}))

print("No invented details:")
results, system, spoken = say("add the twilio problem to the LED strip file", scripts=[
    {"tools": [("append_to_file", {"name": "LED_strip_issue.txt",
                                   "content": "Twilio calls fail; stack traces show error code 1127."})]},
    {"text": "Okay."}])
check("an 'error code 1127' nobody said is never written into their file", results and results[0].startswith("NEEDS")
      and "1127" in results[0] and "1127" not in note_text(), results)
results, system, spoken = say("add to the LED strip file: twilio shows error 31005 when calling", scripts=[
    {"tools": [("append_to_file", {"name": "LED_strip_issue.txt", "content": "Twilio shows error 31005 when calling."})]},
    {"text": "Okay."}])
check("...a code they said themselves is fine", results and results[0].startswith("OK") and "31005" in note_text(), results)

# ---------------------------------------------------------------------------------------------- partial failure
print("A partial write failure:")
results, system, spoken = say("add both issues to the LED strip file and to my passwords.txt", scripts=[
    {"tools": [("append_to_file", {"name": "LED_strip_issue.txt", "content": "Lights reset after a power cut."}),
               ("append_to_file", {"name": "passwords.txt", "content": "Lights reset after a power cut."})]},
    {"text": "I added both to the files."},
    {"text": "I added it to LED_strip_issue.txt, but passwords.txt was refused."}])
check("one write worked, the other was refused: the results say exactly that",
      len(results) == 2 and results[0].startswith("OK") and results[1].startswith("FAILED"), results)
check("...'I added both' is never spoken; the honest partial report is", not any("added both" in s for s in spoken)
      and any("was refused" in s for s in spoken), spoken)
check("the file holds the one that worked; nothing in the refused file", "power cut" in note_text()
      and not (DESKTOP / "passwords.txt").exists())

print("False claims about the file:")
results, system, spoken = say("did you write the file?", reply="I can't create or edit files on the PC. It's in memory.")
check("'I can't create or edit files on the PC' (false: append_to_file is right there) is never spoken",
      not any("can't create" in s for s in spoken), spoken)
results, system, spoken = say("you didn't actually add it, the file is empty", reply="Alright.")
msg = convo.requests[0]["messages"][-1]["content"] if convo.requests else ""
check("'you didn't actually add it': checked in code, the model gets what's really in the file",
      "checked just now" in msg and "LED_strip_issue.txt (Desktop" in msg, msg[-400:])

print("Minimal replies never swallow a problem:")
rt.reply_style, rt.ack_word = "minimal", "Alright"
results, system, spoken = say("it didn't work, I don't see it", reply="It's there now.")
check("in minimal mode the model is told a failure or a reported problem always gets a real answer",
      "a problem they report" in system and "always gets a real answer" in system, system[-500:])
rt.reply_style, rt.ack_word = None, ""

# ---------------------------------------------------------------------------------------------- context recovery
print("Recovering 'that issue' after the message window moved on:")
say("the wifi drops every time the microwave runs, it's really annoying", reply="That's a classic 2.4 GHz problem.")
for i in range(9):  # (each with a tool round: the window of 16 messages moves well past it)
    say(f"set a timer for {i + 2} minutes and tell me a fun fact", scripts=[
        {"tools": [("set_timer", {"seconds": 120 + i})]}, {"text": "Timer's set. Octopuses have three hearts."}])
results, system, spoken = say("add that wifi issue to the LED strip file", reply="Adding it.")
visible = json.dumps([m for m in convo.requests[0]["messages"] if m["role"] != "system"]) if convo.requests else "x"
check("the issue has left the message window (the conversation messages no longer contain it)", "microwave" not in visible)
check("...but the model still has it (earlier_points), so it never asks them to say it again",
      "earlier_points" in system and "microwave" in system, system[-800:])

# ---------------------------------------------------------------------------------------------- yes / no edge cases
print("Timeouts, cancellation, ambiguous answers:")


def ask_for_write(content="Scheduled note."):
    say("hmm the porch light one", scripts=[
        {"tools": [("append_to_file", {"name": "porch.txt", "content": content})]},
        {"text": "Want me to add that to porch.txt?"}])
    return pending.current()


p = ask_for_write()
for x in range(4):
    say(f"what's two plus {x}?", reply="Easy one.")
results, system, spoken = say("Yes.", reply="Yes to what?")
check("a yes after the question timed out (3 turns) runs nothing", not (DESKTOP / "porch.txt").exists())
p = ask_for_write()
p.touched_at -= 400
results, system, spoken = say("Yes.", reply="It timed out.")
check("...nor after it expired in time (3 minutes)", not (DESKTOP / "porch.txt").exists())
p = ask_for_write()
results, system, spoken = say("No, don't.", reply="SHOULD NOT")
check("'No, don't.' cancels it: nothing written, a plain 'I won't'", pending.current() is None
      and not (DESKTOP / "porch.txt").exists() and spoken == ["Okay, I won't."], spoken)
for answer in ("Yes... wait, no.", "Maybe later.", "Yes, but change it first.", "Yes? Which file?"):
    p = ask_for_write()
    say(answer, reply="Okay, tell me when.")
    check(f"{answer!r} is not a yes: nothing written", not (DESKTOP / "porch.txt").exists())
    pending.cancel("test")
p = ask_for_write()
say("what time is it?", reply="It's noon. Should I check the weather?")
results, system, spoken = say("Yes.", reply="It's sunny.")
check("a yes to a DIFFERENT, later question never runs the older pending write", not (DESKTOP / "porch.txt").exists())
pending.cancel("test")

print("A SENSITIVE action still needs its own yes:")
lists.add("buy milk", "to-do")
say("what's on my to-do list?", reply="Just buy milk. Want me to clear your to-do list?")
results, system, spoken = say("Yes.", scripts=[{"tools": [("clear_list", {"list": "to-do"})]},
                                               {"text": "Should I empty the to-do list? This can't be undone."}])
check("a yes to a plain-words question doesn't bypass a SENSITIVE action's own confirmation",
      results and "NEEDS_CONFIRMATION" in results[0] and any(i["text"] == "buy milk" for i in lists._load().get("to-do", [])),
      results)
results, system, spoken = say("Yes.", scripts=[{"text": "SHOULD NOT"}])
check("...its own yes, right after its own question, then empties it (in code)", not convo.requests
      and not [i for i in lists._load().get("to-do", []) if not i.get("done")], spoken)

print("The journal knows what really happened:")
rec = records.recent()
check("every write above is in the journal with its real state (done and checked / FAILED / waiting)",
      any(e["action"] == "append_to_file" and e["state"] == journal.COMPLETED for e in rec)
      and any(e["state"] == journal.FAILED for e in rec) and any(e["state"] == journal.WAITING for e in rec),
      [(e["action"], e["state"]) for e in rec][-8:])

t.done()
