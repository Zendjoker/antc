"""Lists, notes, reminders at a moment and nudges, offline (scripted model, simulated door / bed / PC activity / forecast /
calendar). No audio, no network.

Run:  .venv\\Scripts\\python -m tests.test_lists
"""

import datetime
import json
import time

from tests.harness import setup_env

setup_env(QUIET_HOURS="", GREET_AWAY_MIN="5", DESK_AWAY_MIN="10", BREAK_REMINDER_MIN="180",
          ZIGBEE_ALIASES="bed=Vibration sensor")

from room_agent import config, proactive, triggers  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor  # noqa: E402
from room_agent.cognition import reflex  # noqa: E402
from room_agent.conversation import greet  # noqa: E402
from room_agent.conversation.states import State  # noqa: E402
from room_agent.tools import lists  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

t = Checker()
core.ensure_loaded()
convo = Conversation()


def run(name, args, said):
    rt.new_turn(said)
    return executor.execute(name, args)


print("Lists:")
convo.say("add milk to my shopping list", reply="SHOULD NOT BE NEEDED")
t.check("'add milk to my shopping list' -> added instantly (no model call), confirmed", lists.snapshot().get("shopping") == ["milk"]
        and not convo.requests and convo.said() == ["Added milk to your shopping list."], (lists.snapshot(), convo.said()))
run("add_to_list", {"item": "eggs", "list": "groceries"}, "add eggs to the groceries list")
t.check("'groceries' / 'grocery' / 'shopping' are the same list", lists.snapshot()["shopping"] == ["milk", "eggs"])
r = run("add_to_list", {"item": "Milk", "list": "shopping"}, "add milk to my shopping list")
t.check("adding something already there -> says so, no duplicate", "already" in r.message and lists.snapshot()["shopping"] == ["milk", "eggs"])
run("add_to_list", {"item": "call the bank about the card"}, "put call the bank on my to-do")
r = run("show_list", {"list": "todo"}, "what's on my to-do list")
t.check("the to-do list is read back", r.success and "call the bank about the card" in r.message, r.message)
r = run("check_off", {"item": "bank"}, "check off the bank thing")
t.check("check off by a loose name ('bank' -> 'call the bank about the card')", r.success and lists.snapshot().get("to-do") is None, r.message)
r = run("show_list", {"list": "to-do", "include_done": True}, "what did I finish")
t.check("...and it still shows as done this week", "Checked off this week: call the bank" in r.message, r.message)
r = run("remove_from_list", {"item": "eggs", "list": "shopping"}, "take eggs off the shopping list")
t.check("remove an item", r.success and lists.snapshot()["shopping"] == ["milk"])
r = run("undo_last_action", {}, "undo that")
t.check("...'undo that' puts it back", lists.snapshot()["shopping"] == ["milk", "eggs"], (r.message, lists.snapshot()))
r = run("clear_list", {"list": "shopping"}, "clear my shopping list")
t.check("clearing a whole list asks first (nothing cleared yet)", r.message.startswith("NEEDS_CONFIRMATION")
        and lists.snapshot()["shopping"] == ["milk", "eggs"], r.message)
rt.pending = None
r = run("check_off", {"item": "unicorn"}, "check off unicorn")
t.check("checking off something that isn't there -> says so, nothing changed", not r.success and "nothing like" in r.message)
run("take_note", {"text": "The wifi password is on the router"}, "take a note: the wifi password is on the router")
r = run("show_list", {"list": "notes"}, "read my notes")
t.check("notes are kept with their date", "wifi password" in r.message and time.strftime("%b") in r.message, r.message)
t.check("everything is on disk (survives a restart)", "milk" in config.LISTS_FILE.read_text(encoding="utf-8"))

print("Reminders at a moment:")
said = []
greet.say_soon = lambda text: said.append(text)
rt.state.go(State.WAKE_WORD_ONLY, "test")
proactive.reset()
r = run("remind_me_when", {"text": "call mom", "moment": "home"}, "remind me to call mom when I get home")
t.check("'remind me to call mom when I get home' -> waiting for that moment", r.success and "when you get home" in r.message, r.message)
run("remind_me_when", {"text": "charge my phone", "moment": "bed"}, "when I go to bed remind me to charge my phone")
greet.last_activity = lambda: time.time() - 3600  # (the room was quiet for an hour)
triggers.on_door({"device": "Door sensor"})
t.check("door opens after the room was quiet (arrival) -> 'Reminder: call mom.'", said == ["Reminder: call mom."], said)
t.check("...said once: gone from the waiting list", "call mom" not in triggers.listing())
said.clear()
greet.last_activity = lambda: time.time() - 20  # (someone was just here: leaving)
triggers.rain_check = lambda now=None, forecast=None: False
triggers.on_door({"device": "Door sensor"})
t.check("door opens while someone was here (leaving) -> no 'home' reminder", said == [])
hour = datetime.datetime.now().hour
triggers.on_bed({"device": "Vibration sensor"})
in_bed_hours = hour >= 20 or hour < 4
t.check("the bed sensor fires 'bed' reminders only in the evening / night", (said == ["Reminder: charge my phone."]) == in_bed_hours,
        (hour, said))
said.clear()
triggers.cancel("all")
run("remind_me_when", {"text": "reply to Sam", "moment": "home"}, "remind me to reply to Sam when I get home")
rt.state.go(State.SPEAKING, "test")  # (Jarvis is in the middle of a conversation)
greet.last_activity = lambda: time.time() - 3600
triggers.on_door({"device": "Door sensor"})
t.check("mid-conversation -> not interrupted, held for after it", said == [] and proactive.pending(), proactive.pending())
rt.state.go(State.WAKE_WORD_ONLY, "test")
triggers.after_conversation(proactive.on_idle())
t.check("...and said as soon as the conversation ends", said == ["Reminder: reply to Sam."], said)
said.clear()
run("remind_me_when", {"text": "water the plants", "moment": "home"}, "remind me to water the plants when I'm home")
rt.state.go(State.QUIET, "test")
proactive.reset()
triggers.on_door({"device": "Door sensor"})
t.check("quiet mode -> not said, but kept (a reminder is never dropped)", said == [] and "water the plants" in triggers.listing())
rt.state.go(State.WAKE_WORD_ONLY, "test")
triggers.after_conversation([])
t.check("...and said at the next chance", said == ["Reminder: water the plants."], said)
r = run("remind_me_when", {"text": "x", "moment": "lunch"}, "remind me at lunch")
t.check("an unknown moment -> asks which (never guesses a time)", not r.success, r.message)

print("Back at the PC, breaks:")
said.clear()
proactive.reset()
run("remind_me_when", {"text": "send the invoice", "moment": "desk"}, "remind me to send the invoice when I'm back at my PC")
triggers._state.update(away=False, active_since=None, break_done_for=None)
now = time.time()
triggers.desk_tick(5, now)                     # (working)
triggers.desk_tick(11 * 60, now + 700)         # (away 11 min)
triggers.desk_tick(1, now + 760)               # (back)
t.check("keyboard/mouse after 10+ min away -> the PC reminder", said == ["Reminder: send the invoice."], said)
said.clear()
start = now + 1000
triggers._state.update(away=False, active_since=start, break_done_for=None)
triggers.desk_tick(2, start + 2 * 3600)
t.check("2 h of nonstop use -> no nudge yet", said == [])
triggers.desk_tick(2, start + 3 * 3600 + 5)
t.check("3 h nonstop -> one break nudge", len(said) == 1 and "3 hours" in said[0], said)
triggers.desk_tick(2, start + 3 * 3600 + 600)
t.check("...only once for that session", len(said) == 1)

print("Rain, meetings, plans:")
said.clear()
proactive.reset()
from importlib import reload  # noqa: E402

triggers = reload(triggers)
greet.say_soon = lambda text: said.append(text)
fc = "OK: Paris now ... Today (Thursday): rain, high 14C, low 9C, 80% chance of rain; Tomorrow (Friday): clear, 5% chance of rain"
t.check("leaving with 80% rain today -> 'take a jacket'", triggers.rain_check(forecast=fc) and "80% chance of rain" in said[-1], said)
t.check("...once a day", triggers.rain_check(forecast=fc) is False)
t.check("10% chance -> nothing", reload(triggers).rain_check(forecast=fc.replace("80%", "10%")) is False)
triggers = reload(triggers)
greet.say_soon = lambda text: said.append(text)
said.clear()
soon = [{"id": "e1", "title": "Standup", "start": time.time() + 8 * 60, "all_day": False},
        {"id": "e2", "title": "Lunch", "start": time.time() + 90 * 60, "all_day": False}]
n = triggers.meetings_check(events=soon)
t.check("a meeting in 8 minutes -> one heads-up; the one in 90 minutes -> not yet", n == 1 and "Standup" in said[0]
        and "8 minutes" in said[0], said)
t.check("...not repeated", triggers.meetings_check(events=soon) == 0)
said.clear()
today = datetime.date.today()
plans = [f"Dentist appointment on {today.strftime('%b')} {today.day}, {today.year}", "Exam on Jan 3, 2099"]
t.check("a plan due today is mentioned the morning of", triggers.plans_today(plans=plans) == 1 and "Dentist" in said[0]
        and "Exam" not in said[0], said)

print("The day in review:")
r = run("daily_review", {}, "how was my day")
t.check("'how was my day?' -> what was done, checked off and still open (from real records, nothing invented)", r.success
        and "checked off today: call the bank" in r.message and "add to list" in r.message, r.message[:300])
t.check("lists show on the dashboard", isinstance(lists.snapshot(), dict) and "shopping" in lists.snapshot())
t.done("LISTS")
