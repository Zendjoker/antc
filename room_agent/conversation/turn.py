"""One user turn: ask the model, speak the reply, then keep or roll back the history."""

import logging
import time

from room_agent import runtime as rt
from room_agent import trace
from room_agent.audio.speaker import finish_speaking, say
from room_agent.conversation.history import remember_turn, reply_text, trim
from room_agent.llm.router import ask
from room_agent.memory import mentions

log = logging.getLogger("room-agent")


def _finished(text):
    return text.strip().rstrip("\"')")[-1:] in (".", "?", "!")


# the model follows a rule stated next to the message far more reliably than one in the system prompt
_NEED_NOTE = (" (System note: only if a REQUIRED detail of the tool is entirely absent from what they said, reply only "
              "<need:tool_name>. If it was said in any form, act normally; never use <need> for anything else.)")


def _private_turn():
    from room_agent.actions import core

    plan = rt.current_plan
    return bool(plan and any(getattr(core.get(s.capability), "private", False) for s in plan.steps))


def take_turn(history, text, raw=None, final=False, output=None):
    """(One turn at a time, from the room or a phone call: see _take_turn.)"""
    with rt.brain:
        return _take_turn(history, text, raw, final, output)


def _take_turn(history, text, raw=None, final=False, output=None):
    """One user turn. On failure, roll history back so a half-finished tool call can't poison later turns.
    `raw` is what you actually said (without internal notes), for memory. `final`: wait no more, this must be answered.
    Returns "quiet" / "quiet-silent" (it said nothing) if the model asked to go quiet, "listen" if what you said was
    unfinished, "silent" if it needed no reply, else None."""
    mark = len(history)
    history.append({"role": "user", "content": text if final else text + _NEED_NOTE})
    rt.new_turn(raw or text, must_answer=final, output=output)  # (everything per-turn starts fresh here)
    if rt.turn_start is not None and rt.stt_seconds is not None:
        rt.turn.timing["hearing"] = rt.stt_seconds
    rt.turn_no += 1
    if not trace.has("INPUT"):
        trace.note("INPUT", repr(raw or text))
    if rt.engine and not output:
        rt.engine.interrupted.clear()  # e.g. you talked over a timer announcement: that's over now
    t0 = time.time()
    try:
        ask(history)
        finish_speaking()
    except Exception:
        del history[mark:]
        trace.note("RESULT", "error")
        trace.flush()
        raise
    log.info("turn took %.1fs", time.time() - t0)
    tm = rt.turn.timing
    if tm:  # one line per turn: where the time went
        names = [("hearing", "hearing"), ("first_words", "model's first words"), ("tools", "tools"),
                 ("first_sound", "first sound after you stopped")]
        log.info("timing: " + ", ".join(f"{label} {tm[k]:.2f}s" for k, label in names if k in tm)
                 + f", whole turn {time.time() - t0:.1f}s")
    try:  # learning: what this turn did or corrected becomes evidence (never breaks the turn)
        from room_agent import learning

        learning.learner().on_turn_end(raw or text, rt.current_plan, time.time() - t0)
    except Exception as e:
        log.warning("learning skipped this turn: %s", e)
    from room_agent.llm.budget import budget

    if budget.turn_line():
        log.info(budget.turn_line())
    if len(history) > mark:
        history[mark]["content"] = text  # the note was for this one reply only
    said = reply_text(history[mark + 1 :])
    if rt.control.get("request") == "quiet":
        del history[mark:]  # not part of the conversation: it mustn't linger and keep the model "quiet"
        rt.last_reply = ""
        trace.note("RESPONSE", "(quiet mode)")
        trace.flush()
        return "quiet" if rt.spoken_count else "quiet-silent"  # (counts what was really spoken)
    wait_invalid = (rt.turn_signal == "listen" and not rt.control.get("hold")
                    and (final or _finished(raw or text)))  # a finished sentence isn't unfinished (a held request is)
    silent_invalid = rt.turn_signal == "silent" and len((raw or text).split()) > 2  # only a bare "okay" needs no reply
    if (wait_invalid or silent_invalid) and not rt.spoken_count:
        del history[mark:]
        if not final:
            return _take_turn(history, text, raw=raw, final=True, output=output)  # ask again with those options switched off
        line = rt.phrases.pick("missed")
        say(line)  # never leave them in silence
        finish_speaking()
        rt.last_reply = line
        trace.flush()
        return None
    if rt.turn_signal and not rt.spoken_count:  # no spoken reply needed, or they're mid-sentence
        del history[mark:]  # (nothing was said, so nothing is kept)
        rt.last_reply = ""
        trace.note("ACTION", "CONTINUE_LISTENING" if rt.turn_signal == "listen" else "STAY_SILENT")
        trace.flush()
        return rt.turn_signal
    rt.last_reply = said
    trace.note("RESPONSE", repr(said))
    trace.flush()
    if rt.control.get("forgot_everything"):
        history.clear()
    else:
        for term in rt.control.get("forgot_terms", []):  # what was just forgotten leaves the live context too
            history[:] = [m for m in history if not (isinstance(m["content"], str) and mentions(m["content"], term))]
        remember_turn(raw or text, reply_text(history[mark + 1 :]), private=_private_turn())  # (other things said this turn still count)
    trim(history)
    return None
