"""One phone call with Jarvis. Twilio turns your voice into text and Jarvis's text into voice (ConversationRelay); in
between it's the normal brain: the same turn code, tools, memory, email, calendar and checks as in the room. Only the
output differs: sentences go back down the line instead of to the room speaker."""

import collections
import hmac
import logging
import re
import threading
import time

from room_agent import config
from room_agent import runtime as rt
from room_agent.conversation.turn import take_turn

log = logging.getLogger("room-agent")
PIN_TRIES = 3              # wrong PINs on one call before it's ended
PIN_LOCKOUT = (10, 3600)   # this many wrong PINs across all calls within this many seconds: no PIN accepted until it passes
_pin_failures = collections.deque(maxlen=PIN_LOCKOUT[0])
_DIGIT_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "to": "2", "too": "2", "three": "3",
                "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def spoken_digits(text):
    """'1 2 3 4' / 'one two three four' / '1234.' -> '1234'."""
    out = []
    for tok in re.findall(r"[a-z]+|\d+", str(text or "").lower()):
        out.append(tok if tok.isdigit() else _DIGIT_WORDS.get(tok, ""))
    return "".join(out)


class _Out:
    """Where Jarvis's words go on this call. The executor reads `unverified_caller` (actions/executor.py): nothing
    runs for a caller whose identity isn't established, even if a turn got through."""

    def __init__(self, session):
        self.session = session

    def __call__(self, sentence):
        self.session.send(sentence + " ", False)

    @property
    def unverified_caller(self):
        return not self.session.verified


class CallSession:
    def __init__(self, send, greeting="", item=None, hang_up=None, verified=True):
        """send(text, last): hands Jarvis's words to Twilio. greeting / item: why Jarvis called (if it did).
        hang_up(): ends the call (after the goodbye has been sent). verified: the caller is known to be you (a call
        Jarvis placed, or a carrier-attested caller ID); otherwise they must say PHONE_PIN before anything else."""
        self.send = send
        self.hang_up = hang_up
        self.history = []
        self._lock = threading.Lock()
        self.verified = bool(verified)
        self.pin_tries = 0
        self._out = _Out(self)
        if greeting:  # (Jarvis called: it already said why, so "read it" / "who's it from" follow on naturally)
            self.history = [{"role": "user", "content": "[Jarvis phoned them]"}, {"role": "assistant", "content": greeting}]
        if item:
            from room_agent.actions.context import env

            env.remember_item(item["kind"], {k: v for k, v in item.items() if k not in ("kind", "label")}, item.get("label", ""))

    def _check_pin(self, text):
        """An unverified call: the only thing that's listened to is the PIN. Checked in code: the words never reach the
        model, the tools or the logs."""
        said = spoken_digits(text)
        locked = len(_pin_failures) >= PIN_LOCKOUT[0] and time.time() - _pin_failures[0] < PIN_LOCKOUT[1]
        if locked:  # (someone is guessing: even the right PIN isn't accepted for now, so guessing can't pay off)
            log.warning("phone: PIN entry is locked after %d wrong PINs; declined", PIN_LOCKOUT[0])
            self.send("Sorry, I can't verify calls right now. Goodbye.", True)
            if self.hang_up:
                self.hang_up()
            return
        if config.PHONE_PIN and said and hmac.compare_digest(said.encode(), config.PHONE_PIN.encode()):
            self.verified = True
            log.info("phone: caller verified by PIN")
            self.send("Thanks, you're verified. What's up?", True)
            return
        self.pin_tries += 1
        _pin_failures.append(time.time())
        log.warning("phone: wrong or missing PIN on an unverified call (%d of %d)", self.pin_tries, PIN_TRIES)
        if self.pin_tries >= PIN_TRIES or not config.PHONE_PIN:
            self.send("Sorry, I can't verify this call. Goodbye.", True)
            if self.hang_up:
                self.hang_up()
            return
        self.send("That's not it. Please say your PIN.", True)

    def answer(self, text):
        """What they said -> Jarvis's reply, spoken by Twilio as it's written (one turn at a time)."""
        with self._lock:
            if not self.verified:
                return self._check_pin(text)
            log.info("phone: they said %r", text[:200])
            try:
                take_turn(self.history, text, final=True, output=self._out)
            except Exception as e:
                log.error("phone turn failed: %s", e)
                self._out(rt.phrases.pick("missed"))
            ending = rt.turn.output == self._out and rt.control.get("end_call")
            self.send("", True)  # (end of this reply)
            if ending and self.hang_up:
                log.info("phone: Jarvis is hanging up")
                self.hang_up()

    def interrupt(self):
        """They talked over Jarvis: stop the reply that's being written."""
        if rt.turn.output == self._out:
            rt.turn.cancel.set()
