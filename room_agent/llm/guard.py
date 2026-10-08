"""Shared by both model backends: only claims backed by a tool result get spoken, and interruptions stop the reply."""

import logging
import re

from room_agent import runtime as rt
from room_agent import social
from room_agent.actions import pending
from room_agent.audio.speaker import say
from room_agent.audio.styles import split_style
from room_agent.config import USER_NAME
from room_agent.truth import ClaimGuard
from room_agent.tools.validate import SCHEMAS, current_pending

log = logging.getLogger("room-agent")
_SIGNAL = re.compile(r"<\s*(listen|silent)\s*>", re.I)
_NEED = re.compile(r"<\s*need\s*:\s*(\w+)\s*>", re.I)


def interrupted_now():
    return rt.turn.cancel.is_set() or bool(not rt.turn.output and rt.engine and rt.engine.interrupted.is_set())


def stop_for_interruption(history, spoken):
    """You talked over the reply: stop generating, keep only what was actually said, hand over to listening."""
    said = " ".join(spoken)
    if said:
        history.append({"role": "assistant", "content": said})
        print("Agent:", said, "[interrupted]", flush=True)
    log.info("stopped answering: you interrupted")
    rt.turn_interrupted = True


def new_guard():
    memory = rt.memory
    return ClaimGuard(memory_count=lambda: memory.count() if memory.available else 0,
                      home_known=lambda: bool(memory.available and memory.home_location()))


def speak_checked(guard, sentence, spoken):
    """Say a sentence only if every claim in it is backed by a tool result (or hold it until it is)."""
    signal = _SIGNAL.search(sentence)
    need = _NEED.search(sentence)
    if need:  # a tool request that lacks a required detail: let them finish the thought before anyone asks
        sentence = _NEED.sub("", sentence)
        if (SCHEMAS.get(need.group(1), {}).get("required") and not rt.must_answer and not spoken
                and not current_pending()):
            if pending.collectable(need.group(1)):
                rt.turn_signal = "listen"  # (no hold: a finished request is asked again at once and collected turn by turn)
            else:
                rt.turn_signal, rt.control["hold"] = "listen", True
    if signal:  # the model decided no spoken reply is needed (<silent>) or they're mid-sentence (<listen>)
        rt.turn_signal = signal.group(1).lower()
        sentence = _SIGNAL.sub("", sentence)
    sentence, style = split_style(sentence)  # a leading [soft]-style tag sets the delivery, and is never spoken
    if style:
        rt.turn_style = style
    sentence = social.scrub(sentence) if sentence.strip() else sentence  # (no "Certainly!", no "Let me know if...")
    if sentence.strip() and social.over_cap(spoken):
        return  # a tiny-reply turn ("whatever" -> "Alright.") has said enough
    if sentence.strip() and guard.admit(sentence):
        spoken.append(sentence)
        say(sentence)


def release_checked(guard, spoken):
    for sentence in guard.release():
        spoken.append(sentence)
        say(sentence)


def correction_note(bad):
    from room_agent.truth import PERMISSION_Q

    if any(PERMISSION_Q.search(s) for s in bad):
        return (f"(Automatic check from the system, not from {USER_NAME}: they already told you to do it, so asking "
                f"{' '.join(bad)!r} was NOT spoken. Call the right tool now and confirm in a few words; if it isn't possible, "
                "say so plainly. Don't mention this check.)")
    return (f"(Automatic check from the system, not from {USER_NAME}: your reply said {' '.join(bad)!r}, but no tool "
            "result in this turn backs that up, so it was NOT spoken. Don't claim it. If you can actually do it, call "
            "the right tool now; if it isn't possible, say so plainly. Answer again in one or two sentences, as if for the "
            "first time: don't apologize, don't mention this check.)")
