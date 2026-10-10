"""One user turn: ask the model, speak the reply, then keep or roll back the history."""

import logging
import re
import time

from room_agent import runtime as rt
from room_agent import livelog, trace
from room_agent import cognition
from room_agent import social
from room_agent.actions import pending, records
from room_agent.audio.speaker import finish_speaking, say
from room_agent.cognition import reflex
from room_agent.speech import timing
from room_agent.conversation import corrections, policy
from room_agent.conversation.history import remember_turn, reply_text, trim
from room_agent.llm.router import ask
from room_agent.memory import mentions

log = logging.getLogger("room-agent")


def _finished(text):
    return text.strip().rstrip("\"')")[-1:] in (".", "?", "!")


# the model follows a rule stated next to the message far more reliably than one in the system prompt
_NEED_NOTE = (" (System note: only if a REQUIRED detail of the tool is entirely absent from what they said, reply only "
              "<need:tool_name>. If it was said in any form, act normally; never use <need> for anything else.)")


def _need_note():
    """(Requests that are collected turn by turn never wait: those tools are called with what was said.)"""
    tools = pending.collectable_tools()
    return _NEED_NOTE if not tools else _NEED_NOTE[:-1] + (f" Exception: {', '.join(tools)} collect missing details "
                                                           "themselves: call those with what they said, never <need>.)")


def _private_turn():
    from room_agent.actions import core

    plan = rt.current_plan
    return bool(plan and any(getattr(core.get(s.capability), "private", False) for s in plan.steps))


def _answered_in_code(history, mark, text, raw, reply, private, what="PENDING_ACTION (answered in code)"):
    """Decided without the model: a pending action's next question or "cancelled", or a REFLEX command that ran."""
    history[mark]["content"] = text
    say(reply)
    finish_speaking()
    history.append({"role": "assistant", "content": reply})
    rt.last_reply = reply
    social.after_turn(reply, rt.current_plan)
    cognition.end_turn(reply, rt.current_plan)
    if rt.current_plan is not None:
        try:  # (a reflex action is evidence for learning too, exactly like a model's tool call)
            from room_agent import learning

            learning.learner().on_turn_end(raw or text, rt.current_plan, time.time() - rt.turn.started)
        except Exception as e:
            log.warning("learning skipped this turn: %s", e)
    trace.note("ACTION", what)
    trace.note("RESPONSE", repr(reply))
    trace.flush()
    remember_turn(raw or text, reply, private=private)  # (email / calendar details stay out of long-term memory)
    trim(history)
    return None


def _you(text):
    """A tool's result is written about them ("calling their phone"); said to them it's "your"."""
    t = re.sub(r"\btheir\b", "your", str(text), flags=re.I)
    t = re.sub(r"\bthey'?re\b", "you're", t, flags=re.I)
    t = re.sub(r"\bthem\b", "you", t, flags=re.I)
    return re.sub(r"\bthey\b", "you", t, flags=re.I)


def outcome_line(cap, result):
    """What to say about an action that ran, built from its result only (never more than it says). None: it still needs
    something (the model asks)."""
    msg = str(result.message or "")
    kind, _, body = msg.partition(":")
    body = re.sub(r"\s*\([^)]*\)", "", body).strip()
    body = re.split(r"(?<=[.!?])\s+", body)[0].strip() if body else ""
    body = _you(body).rstrip(".") + "." if body else ""
    if kind.startswith("NEEDS"):
        return None
    if result.success:
        if cap is not None and cap.reflex_say:
            try:
                line = cap.reflex_say(result)
                if line:
                    return line
            except Exception:  # noqa: BLE001
                pass
        if not result.verified:
            return f"I did it, but I couldn't check that it worked: {body}" if body else "I did it, but I couldn't check it."
        return f"Done: {body}" if body else "Done."
    if kind.startswith("UNKNOWN"):
        return f"I'm not sure that worked: {body}" if body else "I'm not sure that worked."
    return f"That didn't work: {body}" if body else "That didn't work."


def _run_confirmed(history, mark, text, raw, p, private):
    """Their clear yes to the pending action: run exactly the action they said yes to (the executor checks it all
    again: their yes, the same arguments, the risk), and say what really happened, from its result."""
    from room_agent.actions import core
    from room_agent.actions.executor import Plan

    plan = Plan()
    rt.current_plan = plan
    rt.turn.via = "confirmed yes"
    result = plan.run(p.capability, dict(p.collected))
    line = outcome_line(core.get(p.capability), result)
    log.info("their yes ran %s: %s", p.capability, result.message[:120])
    if line is None:  # (it still needs a detail: the model asks for it, knowing what happened)
        history[mark]["content"] += (f" (System note from code: they said yes, so {p.capability} was run; it returned: "
                                     f"{result.message[:300]} Ask for what's missing, in one short question.)")
        return False
    _answered_in_code(history, mark, text, raw, line, private, what=f"CONFIRMED {p.capability}")
    return True


def _question_for(p):
    """The yes/no question for a pending action, from the action itself (when the reply didn't ask it)."""
    from room_agent.actions import core

    cap = core.get(p.capability)
    what = ""
    try:
        what = cap.describe(p.collected) if cap is not None and cap.describe else ""
    except Exception:  # noqa: BLE001
        what = ""
    if not what and cap is not None:
        what = re.split(r"[.(:]", cap.description)[0].strip()
        what = what[:1].lower() + what[1:]
    return f"Should I {_you(what or p.capability.replace('_', ' '))}?"


def _deliver_confirmation(history):
    """A yes/no question about an action that came up this turn must REACH them: if the reply didn't ask it, ask it now
    (built from the action itself); then record whether it was heard (played to the end without being cut off, sent
    down the phone line, or shown in text mode). A question they never heard is never "you didn't confirm" later."""
    p = pending.current()
    if p is None or not p.confirm or p.touched_turn != rt.turn_no or rt.turn_interrupted:
        return
    from room_agent.actions.executor import NO_WORDS

    said = list(getattr(rt.turn, "said", []) or [])
    questions = [x for x in said if str(x).rstrip().endswith("?")]
    kind = getattr(getattr(rt.turn, "policy", None), "kind", "")
    asked_for = kind != policy.QUESTION and not NO_WORDS.search(rt.turn_text or "")  # (a request, not "what's the weather?")
    if not questions and asked_for:
        q = _question_for(p)
        log.info("the reply didn't ask the yes/no question for %s: asking it in code", p.capability)
        say(q)
        finish_speaking()
        print("Agent:", q, flush=True)
        last = history[-1] if history else None
        if last and last["role"] == "assistant" and isinstance(last["content"], str):
            last["content"] = (last["content"] + " " + q).strip()
        elif last and last["role"] == "assistant" and isinstance(last["content"], list):
            last["content"].append({"type": "text", "text": q})
        else:
            history.append({"role": "assistant", "content": q})
        questions = [x for x in getattr(rt.turn, "said", []) if str(x).rstrip().endswith("?")]
    p.question = str(questions[-1]) if questions else ""
    if not questions:
        return
    p.heard = any(getattr(x, "played", None) is not False for x in questions)  # (no speaker thread at all: shown)
    if not p.heard:
        log.warning("the yes/no question for %s didn't reach them (speech failed or was cut off)", p.capability)


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
    history.append({"role": "user", "content": text if final else text + _need_note()})
    rt.new_turn(raw or text, must_answer=final, output=output)  # (everything per-turn starts fresh here)
    if rt.turn_start is not None and rt.stt_seconds is not None:
        rt.turn.timing["hearing"] = rt.stt_seconds
    rt.turn_no += 1
    if not trace.has("INPUT"):
        trace.note("INPUT", repr(raw or text))
    if rt.engine and not output:
        rt.engine.interrupted.clear()  # e.g. you talked over a timer announcement: that's over now
    if rt.turn_start is not None:  # (voice: when they stopped talking, and when the silence ended the turn)
        from room_agent.audio import mic

        timing.mark("endpoint_detected", rt.turn_start)
        if mic.last_speech_end and 0 <= rt.turn_start - mic.last_speech_end < 30:
            timing.mark("user_endpoint", mic.last_speech_end)
    timing.mark("reasoning_start")
    private = pending.private_now()
    rt.turn.uncertain = bool(rt.turn_start is not None and rt.stt_uncertain)  # (typed text is never "misheard")
    rt.stt_uncertain = False
    corrections.on_user_turn(raw or text)  # ("I didn't say that": the misheard request is discarded everywhere)
    social.on_user_turn(raw or text)  # (how the conversation is going -> how to answer: social/, a few ms, no model call)
    info = cognition.begin_turn(raw or text)  # (REFLEX / FAST / DELIBERATE / DEEP, goal, constraints: cognition/, no model)
    policy.on_user_turn(raw or text, info)  # (command / question / casual / ... -> what kind of reply fits: policy.py)
    decision = pending.on_utterance(raw or text)  # (a request being filled in: answers, corrections, "never mind")
    if decision and decision.reply:
        return _answered_in_code(history, mark, text, raw, decision.reply, private)
    if decision and decision.run is not None:  # (their yes to the question just asked: that exact action, now)
        if _run_confirmed(history, mark, text, raw, decision.run, private):
            return None
        decision = None
    if not decision and info["reflex"]:  # a fully specified simple command: run it, no model call
        line, result = reflex.run(*info["reflex"])
        if line:
            return _answered_in_code(history, mark, text, raw, line, private=False, what=f"REFLEX {result.capability}")
        cognition.reflex_fell_back()
        history[mark]["content"] += (f" (System note from code: {result.capability} was already tried for this and returned: "
                                     f"{result.message} Don't run it again just like that; tell them, or do what fits.)")
    if decision and decision.note:
        history[mark]["content"] += decision.note  # (for this reply only: replaced by the plain text after the turn)
    elif not decision:
        history[mark]["content"] += pending.call_hint(raw or text)  # (a request that collects its own details: call it)
    history[mark]["content"] += records.dispute_note(raw or text)  # ("I don't see it": checked in code, now)
    t0 = time.time()
    try:
        ask(history)
        finish_speaking()
        _deliver_confirmation(history)  # (a yes/no question that came up must reach them, heard)
    except Exception:
        del history[mark:]
        cognition.end_turn(None, rt.current_plan)
        trace.note("RESULT", "error")
        trace.flush()
        raise
    log.info("turn took %.1fs", time.time() - t0)
    timing.log_summary()
    tm = rt.turn.timing
    if tm:  # one line per turn: where the time went
        names = [("hearing", "hearing"), ("first_words", "model's first words"), ("tools", "tools"),
                 ("first_sound", "first sound after you stopped")]
        log.info("timing: " + ", ".join(f"{label} {tm[k]:.2f}s" for k, label in names if k in tm)
                 + f", whole turn {time.time() - t0:.1f}s")
    livelog.event("turn", **{k: float(v) for k, v in (tm or {}).items() if isinstance(v, (int, float))},
                  whole_turn=float(time.time() - t0), interrupted=bool(rt.turn.interrupted))
    try:  # every request that did something gets a task record (a plan run by run_task records itself)
        from room_agent.actions import tasks
        from room_agent.llm.budget import budget

        plan = rt.current_plan
        names = {s.capability for s in getattr(plan, "steps", []) or []}
        if plan is not None and not names & {"run_task", "resume_task", "cancel_task"}:
            tasks.record_turn(plan, time.time() - t0, sum(budget.turn.values()))
    except Exception as e:
        log.debug("task record not written: %s", e)
    try:  # learning: what this turn did or corrected becomes evidence (never breaks the turn)
        from room_agent import learning

        learning.learner().on_turn_end(raw or text, rt.current_plan, time.time() - t0)
    except Exception as e:
        log.warning("learning skipped this turn: %s", e)
    from room_agent.llm.budget import budget

    log.info(budget.turn_line(kind=str(getattr(rt.turn, "level", "") or "turn").lower(), seconds=time.time() - t0))
    if len(history) > mark:
        history[mark]["content"] = text  # the note was for this one reply only
    said = reply_text(history[mark + 1 :])
    social.after_turn(said, rt.current_plan, rt.turn_interrupted)
    if rt.control.get("request") == "quiet":
        cognition.end_turn(said, rt.current_plan, interrupted=True)
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
        cognition.end_turn("", rt.current_plan, waiting=rt.turn_signal == "listen")
        del history[mark:]  # (nothing was said, so nothing is kept)
        rt.last_reply = ""
        trace.note("ACTION", "CONTINUE_LISTENING" if rt.turn_signal == "listen" else "STAY_SILENT")
        trace.flush()
        return rt.turn_signal
    rt.last_reply = said
    cognition.end_turn(said, rt.current_plan, rt.turn_interrupted)
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
