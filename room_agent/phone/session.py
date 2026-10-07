"""One phone call with Jarvis. Twilio turns your voice into text and Jarvis's text into voice (ConversationRelay); in
between it's the normal brain: the same turn code, tools, memory, email, calendar and checks as in the room. Only the
output differs: sentences go back down the line instead of to the room speaker."""

import logging
import threading

from room_agent import runtime as rt
from room_agent.conversation.turn import take_turn

log = logging.getLogger("room-agent")


class CallSession:
    def __init__(self, send, greeting="", item=None, hang_up=None):
        """send(text, last): hands Jarvis's words to Twilio. greeting / item: why Jarvis called (if it did).
        hang_up(): ends the call (after the goodbye has been sent)."""
        self.send = send
        self.hang_up = hang_up
        self.history = []
        self._lock = threading.Lock()
        if greeting:  # (Jarvis called: it already said why, so "read it" / "who's it from" follow on naturally)
            self.history = [{"role": "user", "content": "[Jarvis phoned them]"}, {"role": "assistant", "content": greeting}]
        if item:
            from room_agent.actions.context import env

            env.remember_item(item["kind"], {k: v for k, v in item.items() if k not in ("kind", "label")}, item.get("label", ""))

    def _out(self, sentence):
        self.send(sentence + " ", False)

    def answer(self, text):
        """What they said -> Jarvis's reply, spoken by Twilio as it's written (one turn at a time)."""
        with self._lock:
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
