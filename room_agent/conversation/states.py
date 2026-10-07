"""The agent's state machine: the one place that decides whether it may speak and what it listens for."""

import enum
import logging
import threading

log = logging.getLogger("room-agent")


class State(enum.Enum):
    STARTING = "starting up"
    WAKE_WORD_ONLY = "asleep: only the wake word is listened for"
    QUIET = "quiet mode: only the wake word is listened for, nothing proactive at all"
    LISTENING = "awake, listening for you"
    PROCESSING = "transcribing and thinking"
    SPEAKING = "speaking"
    IDLE_CHECK = "saying a check-in or goodbye"


class StateMachine:
    """Behavior comes from this state, never from the conversation history (so an old "be quiet" can't
    leak into later turns)."""

    def __init__(self):
        self.state = State.STARTING
        self._lock = threading.Lock()

    def go(self, new, why=""):
        with self._lock:
            old, self.state = self.state, new
        if old is not new:
            big = {State.STARTING, State.WAKE_WORD_ONLY, State.QUIET} & {old, new}
            log.log(logging.INFO if big else logging.DEBUG, "state: %s -> %s%s", old.name, new.name,
                    f" ({why})" if why else "")

    @property
    def quiet(self):
        return self.state is State.QUIET

    @property
    def awake(self):
        return self.state not in (State.STARTING, State.WAKE_WORD_ONLY, State.QUIET)
