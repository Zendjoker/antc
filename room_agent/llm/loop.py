"""One model answer, the same way for every cloud model: stream sentence by sentence through the claim guard, run tool
calls, stop the moment you interrupt. The model only supplies words and tool requests (through a small adapter in
claude.py / openai_backend.py); everything that decides what may be spoken or done lives here and in guard.py, so
switching models can never weaken the truth rules."""

import json
import logging
import re
from dataclasses import dataclass, field

from room_agent import runtime as rt
from room_agent.audio.fillers import LOOP, filler
from room_agent.audio.speaker import say
from room_agent.llm.guard import (correction_note, interrupted_now, new_guard, release_checked, speak_checked,
                                  stop_for_interruption)
from room_agent.prompt import system_parts
from room_agent.actions import core
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


def run_model(history, make_call):
    """Answer the last message in `history` with the model behind `make_call`; speak the verified reply and append it
    (and any tool calls) to `history`. `make_call(history, fixed, changing, tools)` returns an adapter."""
    fixed, changing = system_parts()
    tools = active_tools()
    guard, corrected = new_guard(), False
    plan = Plan()  # this turn's actions, in order: a step on something that failed earlier isn't run
    rt.current_plan = plan
    while True:
        buf, spoken, cut = "", [], False
        with make_call(history, fixed, changing, tools) as call:
            for text in call.deltas():
                rt.turn.mark("first_words")
                if interrupted_now():
                    cut = True
                    break  # leaving the block closes the stream: no more generation, no tools
                buf += text
                parts = re.split(r"(?<=[.!?])\s+", buf)
                for sentence in parts[:-1]:
                    if sentence.strip():
                        speak_checked(guard, sentence.strip(), spoken)
                buf = parts[-1]
            reply = None if cut else call.finish()
        if cut or interrupted_now():
            return stop_for_interruption(history, spoken)
        if buf.strip():
            speak_checked(guard, buf.strip(), spoken)

        if reply.stop == "tool_use":
            history.append({"role": "assistant", "content": reply.content})
            uses = reply.uses
            if (not rt.must_answer and not spoken and not current_pending()
                    and any(missing_required(u.name, u.input) for u in uses)):
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
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": out})
            history.append({"role": "user", "content": results})
            release_checked(guard, spoken)  # e.g. "Timer's set." said before the tool ran, now confirmed
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
            history.append({"role": "assistant", "content": reply.content})
        if spoken:
            print("Agent:", " ".join(spoken), flush=True)
        return
