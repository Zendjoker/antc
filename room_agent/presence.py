"""
Things the agent says on its own (not from Claude): wake acknowledgements, silence check-ins,
going-to-sleep lines, and short fillers. Each kind has its own pool, and the picker avoids anything
said recently, so it never sounds like a looping recording.

These are pre-recorded once per voice (cached in assets/voices/clips), so they play instantly.
Add or edit lines freely; new ones get recorded on the next start.
"""

import collections
import random

POOLS = {
    # "Hey Jarvis" on its own
    "wake": [
        "Yeah?",
        "I'm here.",
        "What's up?",
        "Yep, I'm listening.",
        "I'm all ears.",
        "Hey, what's going on?",
        "Yeah, go ahead.",
        "Talk to me.",
        "Hey. What's up?",
        "I'm listening.",
        "Mm-hm, what's up?",
        "Yo, what's good?",
    ],
    # unusually long silence in the middle of a conversation
    "checkin": [
        "You good?",
        "Still there?",
        "Everything alright?",
        "You wanna talk about something?",
        "I'm still here if you need me.",
        "Got something on your mind?",
        "Hey, you okay over there?",
        "Lost you for a sec. All good?",
        "Anything else you wanna get into?",
        "Quiet all of a sudden. You alright?",
    ],
    # long silence: about to go back to wake word mode
    "sleep": [
        "Alright, I'll be here if you need me.",
        "I'll go back to sleep. Just call me if you need anything.",
        "Looks like we're done for now. Wake me if you need me.",
        "I'll be around.",
        "Going quiet for now. Just say hey Jarvis.",
        "Okay, I'll leave you to it.",
        "Cool, I'll be right here.",
        "Alright, catch you later.",
        "I'll let you be. Holler if you need me.",
        "Okay, going on standby.",
    ],
    # you asked it to be quiet until you call it: said once, then total silence until the wake word
    "quiet": [
        "Got it.",
        "Okay, going quiet.",
        "Sure. I'll wait till you call me.",
        "Got it, I'll be quiet.",
        "Okay, I'll stay out of it.",
        "Say no more.",
    ],
    # wake-up calls repeat these until you say something
    "alarm": [
        "Hey, wake up.",
        "Come on, time to get up.",
        "Hey. Wake up.",
        "Rise and shine.",
        "Hello? Time to wake up.",
        "Hey sleepyhead, wake up.",
        "Come on, up you get.",
        "Wake up, wake up.",
        "Hey, you gotta get up now.",
        "Okay, seriously, wake up.",
    ],
    # a ringing timer repeats these (between repeats of what it's about) until you say something
    "timer_nudge": [
        "Hey, your timer's still going.",
        "Timer's up, just so you know.",
        "Hello? Your timer's done.",
        "Still ringing over here.",
        "Come on, your timer's up.",
        "Just a heads up, the timer's done.",
    ],
    # same for a ringing reminder
    "reminder_nudge": [
        "Hey, don't forget.",
        "Just a reminder, still here.",
        "Hello? Still waiting on you.",
        "Hey, you still there?",
        "Just checking you heard me.",
        "Don't forget, okay?",
    ],
    # you cut it off but then didn't actually say anything
    "resume": [
        "Sorry, go ahead.",
        "Go for it.",
        "Yeah? Go ahead.",
        "My bad, what were you saying?",
        "Go ahead, I'm listening.",
        "Sorry, you first.",
    ],
    # short acknowledgements when an answer is slow to start
    "ack": ["Mm.", "Hmm.", "Mhm.", "Okay.", "Yeah.", "Right."],
    # they stopped it mid-reply ("stop", "can you stop?"): it already stopped, this only confirms it heard
    "stopped": ["Okay.", "Alright.", "Sure."],
    # a tool call (weather, smart home...) needs a moment
    "wait": ["One sec.", "Let me check.", "Hang on.", "Give me a sec.", "Lemme see.", "Checking."],
    # it couldn't confirm that what it was about to say really happened
    "unsure": ["Honestly, I'm not sure that went through.", "I don't think that worked, sorry.",
               "I can't tell if that worked, so I won't pretend it did.", "That might not have gone through, sorry."],
    # it couldn't make out what you said
    "missed": ["Sorry, I missed that. Say it again?", "Didn't catch that, one more time?", "Sorry, what was that?",
               "Missed that, say it again?"],
}


class Phrases:
    def __init__(self, pools=POOLS, avoid_recent=4):
        self.pools = pools
        # never repeat any of the last few lines of the same kind (fewer for small pools)
        self.recent = {k: collections.deque(maxlen=min(avoid_recent, len(v) - 1)) for k, v in pools.items()}

    def pick(self, kind):
        options = [p for p in self.pools[kind] if p not in self.recent[kind]] or self.pools[kind]
        choice = random.choice(options)
        self.recent[kind].append(choice)
        return choice

    def everything(self):
        return [p for pool in self.pools.values() for p in pool]
