"""State shared across modules. Use it as `from room_agent import runtime as rt`.

Three kinds of state, each with an owner:

  TURN      everything about the reply being made right now. One object, replaced by new_turn() at the start of every
            turn (conversation/turn.py), so nothing from the last turn can leak into this one. The old names still
            work (rt.turn_text, rt.must_answer, rt.control...): they read and write the current Turn.
  SESSION   what carries from turn to turn while Jarvis is running (the conversation, what's pending, what's ringing,
            what it was just talking about). Owners noted per field.
  SERVICES  long-lived objects: the audio engine, the state machine, memory, the speech queue.

Things about the outside world (the app in focus, the last window moved, undo...) live in actions/context.py (env).
"""

import collections
import datetime
import queue
import sys
import threading
import time
import types

from room_agent import config
from room_agent.audio import voices
from room_agent.conversation.states import StateMachine
from room_agent.memory import Memory
from room_agent.presence import Phrases


# ---------------------------------------------------------------- TURN
class Turn:
    """The turn being answered now (new_turn() makes a fresh one)."""

    def __init__(self, text="", must_answer=False, output=None):
        self.text = text            # what was just said (to pick the relevant memories for this request)
        self.must_answer = must_answer  # it must be answered in words: <listen> and <silent> are not allowed
        self.style = ""             # delivery style the model chose for this reply (kept for its following sentences)
        self.signal = ""            # "listen" (they're mid-sentence) or "silent" (no spoken reply needed), from the model
        self.plan = None            # this turn's actions (actions/executor.Plan)
        self.interrupted = False    # they talked over the reply
        self.control = {}           # set by tools during the turn: {"request": "quiet"}, {"forgot": True}...
        self.spoken_count = 0       # sentences actually queued for speaking
        self.started = time.time()
        self.timing = {}            # stage -> seconds, for the per-turn timing line (conversation/turn.py)
        self.output = output        # where the reply goes instead of the room speaker (a phone call), or None
        self.cancel = threading.Event()  # set to stop the reply (they talked over it on the phone)
        self.strategy = None        # how to answer this turn (social/strategy.py ResponseStrategy), set before the model
        self.delivery = None        # how to say it (social/delivery.py VoiceDelivery)
        self.speech = {}            # spoken-response timestamps (speech/timing.py)
        self.last_performance = None  # the previous sentence's SpeechPerformance (speech/director.py: one speaker)

    def mark(self, stage, since=None):
        """Note how long a stage took (from `since`, or from the start of the turn); only the first mark counts."""
        if stage not in self.timing:
            self.timing[stage] = time.time() - (since if since is not None else self.started)


turn = Turn()
_TURN_FIELDS = {"turn_text": "text", "must_answer": "must_answer", "turn_style": "style", "turn_signal": "signal",
                "current_plan": "plan", "turn_interrupted": "interrupted", "control": "control",
                "spoken_count": "spoken_count"}


def new_turn(text, must_answer=False, output=None):
    """Start a fresh turn: everything per-turn is reset in one place."""
    global turn
    turn = Turn(text, must_answer, output)
    turn_speech.clear()
    return turn


# ---------------------------------------------------------------- SESSION
session_started = datetime.datetime.now()
turn_no = 0                 # counts user turns (conversation/turn.py)
recent = []                 # dialogue saved across restarts: [{"role", "text", "time"}] (conversation/history.py)
last_reply = ""             # what the agent last said, to know whether it's waiting for an answer
pending = None              # an unfinished request, or one waiting for a yes (tools/validate.py, actions/executor.py)
ringing = None              # the timer or alarm ringing right now (tools/timers.py)
last_ring = None            # the one that last went off: label, message, kind, time, stopped (tools/timers.py)
last_active_app = None      # the app this conversation is about ("close it"): name, action, time (tools/apps.py)
last_media_at = 0.0         # when volume or playback last changed (tools/media.py)
session_location = ""       # a city they named for the weather this session (tools/weather.py)
user_profile = (config.USER_PROFILE or config.USER_NAME).lower()  # whose learned preferences apply (learning/)
explaining = False          # "let me finish": collect what they say, answer only a finished thought (conversation/session.py)
turn_start = None           # when they stopped talking, to time the answer (conversation/session.py)
stt_confidence = None       # how sure speech recognition was about the last utterance (audio/stt.py)
stt_seconds = None          # how long speech-to-text took for the last utterance (conversation/session.py)
patience = float(voices.saved("patience", 1.0))  # x SILENCE_S before it treats you as finished (tools/voice.py)
speech_rate = float(voices.saved("speech_rate", 1.0))  # their speaking pace, both voices (tools/voice.py)

# ---------------------------------------------------------------- SERVICES
state = StateMachine()
memory = Memory(config.MEMORY_DB, legacy_json=config.MEMORY_FILE, legacy_recent=config.RECENT_FILE)
phrases = Phrases()         # wake / check-in / sleep / resume / ack / wait lines, never repeated back to back
speak_q: "queue.Queue[str | bytes]" = queue.Queue()  # text to speak, raw PCM bytes, or the thinking-loop marker
recent_speech = collections.deque(maxlen=12)  # last sentences the agent said out loud
turn_speech = collections.deque(maxlen=40)    # every sentence of the reply being spoken now (queued or playing)
tts_end = 0.0               # when the agent's last speech audio finishes playing (epoch seconds, estimated)
engine = None               # AudioEngine: speaker + echo-cancelled mic (set up in cli.main)
tts_enabled = True
writer = None               # MemoryWriter, for conversations (set up in cli.main)
brain = threading.RLock()   # one turn at a time, whether it came from the room or a phone call


class _Runtime(types.ModuleType):
    """Keeps the old names (rt.turn_text...) working: they read and write the current Turn."""

    def __getattr__(self, name):
        field = _TURN_FIELDS.get(name)
        if field is None:
            raise AttributeError(name)
        return getattr(self.__dict__["turn"], field)

    def __setattr__(self, name, value):
        field = _TURN_FIELDS.get(name)
        if field is None:
            super().__setattr__(name, value)
        else:
            setattr(self.__dict__["turn"], field, value)


sys.modules[__name__].__class__ = _Runtime
