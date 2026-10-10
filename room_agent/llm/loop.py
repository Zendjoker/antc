"""One model answer, the same way for every cloud model: stream sentence by sentence through the claim guard, run tool
calls, stop the moment you interrupt. The model only supplies words and tool requests (through a small adapter in
claude.py / openai_backend.py); everything that decides what may be spoken or done lives here and in guard.py, so
switching models can never weaken the truth rules."""

import json
import logging
import re
import time
from dataclasses import dataclass, field

from room_agent import cognition
from room_agent import runtime as rt
from room_agent.cognition import metrics
from room_agent.audio.fillers import LOOP, filler
from room_agent.audio.speaker import say
from room_agent.llm.guard import (correction_note, interrupted_now, new_guard, release_checked, speak_checked,
                                  stop_for_interruption)
from room_agent.audio.styles import semantic_content
from room_agent.prompt import system_parts
from room_agent.speech import chunking
from room_agent.actions import core, pending
from room_agent.actions.executor import Plan
from room_agent.tools.registry import active_tools, run_tool
from room_agent.tools.validate import current_pending, missing_required

log = logging.getLogger("room-agent")


@dataclass
class Use:
    id: str
    name: str
    input: dict


@dataclass
class Reply:
    """What a model call produced. `content` is stored in the history as-is (Claude's format, which both adapters
    can read back), `uses` are the tool calls in it."""
    stop: str  # "tool_use" | "end"
    content: list
    uses: list = field(default_factory=list)


_MORE = re.compile(r"\b(and|also|then|plus|but|after that|as well|too|tell me|explain|why|how)\b|[?;]", re.I)


def _single_request(text):
    """One plain request ("make the strip pink"), nothing else to answer ("...and motivate me", a question)."""
    t = str(text or "").strip()
    return bool(t) and len(t.split()) <= 12 and not _MORE.search(t)


def verified_done(plan, n, spoken, guard):
    """After tool calls: can the turn end without asking the model again? Only when every call was a SAFE state change
    that succeeded and was verified, nothing is held back by the claim check, and there's something truthful to say:
    the model's own words that came with the call (already spoken), or each tool's confirmation built from the state
    it read back (reflex_say). -> lines still to say ([] = already said), or None (ask the model)."""
    steps = list(plan.steps)[-n:] if n else []
    if not steps or guard.held or len(steps) != n or not _single_request(rt.turn_text):
        return None
    caps = [core.get(s.capability) for s in steps]
    from room_agent.actions.executor import risk_of

    if any(c is None or not c.changes_state or risk_of(c, s.parameters) != core.Risk.SAFE for c, s in zip(caps, steps)):
        return None
    if any(not (s.success and s.verified) or s.message.rstrip().endswith("?") for s in steps):
        return None
    if spoken:
        return []
    from room_agent.conversation.policy import minimal_replies

    ack = minimal_replies()
    if ack:  # (they asked for just "Alright": verified, so nothing more to say)
        return [f"{ack}."]
    lines = []
    for cap, step in zip(caps, steps):
        line = cap.reflex_say(step) if cap.reflex_say else None
        if not line:
            return None
        lines.append(line)
    return lines


def run_model(history, make_call):
    """Answer the last message in `history` with the model behind `make_call`; speak the verified reply and append it
    (and any tool calls) to `history`. `make_call(history, fixed, changing, tools)` returns an adapter."""
    fixed, changing = system_parts()
    tools = active_tools()
    guard, corrected = new_guard(), False
    plan = Plan()  # this turn's actions, in order: a step on something that failed earlier isn't run
    rt.current_plan = plan
    stopped = False  # (the step limit was reached once: the next round must be words, not tools)
    while True:
        buf, spoken, cut = "", [], False
        if interrupted_now():  # (stopped while the last tools ran: no further model call at all)
            return stop_for_interruption(history, [])
        t_call = time.time()
        with make_call(history, fixed, changing, tools) as call:
            for text in call.deltas():
                rt.turn.mark("first_words")
                if interrupted_now():
                    cut = True
                    break  # leaving the block closes the stream: no more generation, no tools
                buf += text
                ready, buf = chunking.ready(buf, first=not spoken)  # (natural boundaries; a long first sentence at a clause)
                for sentence in ready:
                    speak_checked(guard, sentence, spoken)
            reply = None if cut else call.finish()
            metrics.last_call_latency(time.time() - t_call)
        if cut or interrupted_now():
            return stop_for_interruption(history, spoken)
        if buf.strip():
            speak_checked(guard, buf.strip(), spoken)

        if reply.stop == "tool_use":
            history.append({"role": "assistant", "content": semantic_content(reply.content)})
            uses = reply.uses
            if (not rt.must_answer and not spoken and not current_pending()
                    and any(missing_required(u.name, u.input)
                            and not pending.collectable(u.name, missing_required(u.name, u.input)) for u in uses)):
                rt.turn_signal, rt.control["hold"] = "listen", True  # still forming the request: wait before asking
                return
            if any(u.name == "go_quiet" for u in uses):  # handled by code right now: no filler, no 2nd model call
                history.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": u.id,
                     "content": run_tool(u.name, u.input) if u.name == "go_quiet" else "FAILED: not run (going quiet)."}
                    for u in uses]})
                if rt.control.get("request") == "quiet":
                    return
                continue  # not clear enough to go quiet: the model asks the user to confirm
            gate = cognition.round_gate(plan, len(uses))  # (limits: rounds, calls, time; REPLAN after a failure)
            if gate:
                history.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": u.id, "content": gate}
                                                            for u in uses]})
                if stopped:  # it asked for tools again after being told to stop: end the turn here, honestly
                    say(rt.phrases.pick("unsure"))
                    return
                stopped = True
                continue
            if not spoken:
                filler("wait")
            if rt.tts_enabled and not rt.turn.output:
                rt.speak_q.put(LOOP)  # loading loop while the tool runs and the answer is written
            results = []
            for u in uses:
                if interrupted_now():  # you countermanded it: the remaining actions don't run
                    results.append({"type": "tool_result", "tool_use_id": u.id, "content": "FAILED: not run, they interrupted."})
                    continue
                private = getattr(core.get(u.name), "private", False)
                log.info("tool %s %s", u.name, sorted(u.input) if private else json.dumps(u.input))
                out = plan.run(u.name, u.input).to_model()
                log.info("tool result: %s", f"{out.split(':')[0]} ({len(out)} chars of personal data, not logged)"
                         if private else out[:160])
                guard.tool_result(u.name, out)
                if u.name in ("run_task", "resume_task"):  # (what each step really did backs what's said about it)
                    from room_agent.actions import tasks

                    for step_tool, step_out in tasks.step_results():
                        guard.tool_result(step_tool, step_out)
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": out})
            history.append({"role": "user", "content": results})
            release_checked(guard, spoken)  # e.g. "Timer's set." said before the tool ran, now confirmed
            done = verified_done(plan, len(uses), spoken, guard)
            if done is not None:  # a simple action, verified: no second model call just to say so
                for line in done:
                    say(line)
                    spoken.append(line)
                history.append({"role": "assistant", "content": " ".join(spoken)})
                log.info("tool turn finished in code: verified simple action, no second model call")
                print("Agent:", " ".join(spoken), flush=True)
                return
            if spoken:
                print("Agent:", " ".join(spoken), flush=True)
            continue

        release_checked(guard, spoken)
        bad = guard.unresolved()
        if bad:
            log.warning("claim check: held back unverified %r", bad)
            guard.drop_held()
            said = " ".join(spoken)
            if said:  # keep only what was actually spoken in the history
                history.append({"role": "assistant", "content": said})
            if not corrected:
                corrected = True
                history.append({"role": "user", "content": correction_note(bad)})
                if spoken:
                    print("Agent:", said, flush=True)
                continue
            if not said:
                fallback = rt.phrases.pick("unsure")
                say(fallback)
                history.append({"role": "assistant", "content": fallback})
                spoken.append(fallback)
        elif reply.content:  # (an empty reply after a tool result would make every later request fail)
            history.append({"role": "assistant", "content": semantic_content(reply.content)})
        if spoken:
            print("Agent:", " ".join(spoken), flush=True)
        return
