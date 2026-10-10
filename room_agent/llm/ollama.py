"""Ollama backend: same as the Claude one, against a local model. History uses Ollama's chat format."""

import json
import logging
import re
import time

import requests

from room_agent import cognition
from room_agent import runtime as rt
from room_agent.cognition import metrics
from room_agent.audio.fillers import LOOP, filler
from room_agent.audio.speaker import say
from room_agent.config import OLLAMA_KEEP_ALIVE, OLLAMA_MODEL, OLLAMA_URL
from room_agent.llm.guard import (correction_note, interrupted_now, new_guard, release_checked, speak_checked,
                                  stop_for_interruption)
from room_agent.prompt import system_prompt
from room_agent.audio.styles import clean_args
from room_agent.tools.registry import active_tools, run_tool

log = logging.getLogger("room-agent")


def ask_ollama(history, model=None):
    """`model`: another Ollama model for this request (cognition's DEEP level), else OLLAMA_MODEL."""
    model = model or OLLAMA_MODEL
    tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
             for t in active_tools()]
    messages = [{"role": "system", "content": system_prompt()}]
    guard, corrected = new_guard(), False
    from room_agent.actions.executor import Plan

    plan, stopped = Plan(), False
    rt.current_plan = plan
    while True:
        buf, spoken, content, calls = "", [], "", []
        t_call, usage = time.time(), {}
        with requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": messages + history,
                "tools": tools,
                "stream": True,
                "think": False,
                "keep_alive": OLLAMA_KEEP_ALIVE,
            },
            stream=True,
            timeout=120,
        ) as r:
            if not r.ok:
                raise RuntimeError(f"Ollama {r.status_code}: {r.text[:200]}")
            for line in r.iter_lines():
                if interrupted_now():
                    break  # closing the response stops generation
                if not line:
                    continue
                part = json.loads(line)
                if "error" in part:
                    raise RuntimeError(f"Ollama: {part['error']}")
                if part.get("done"):
                    usage = part
                msg = part.get("message", {})
                calls += msg.get("tool_calls") or []
                text = msg.get("content", "")
                if not text:
                    continue
                content += text
                buf += text
                parts = re.split(r"(?<=[.!?])\s+", buf)
                for sentence in parts[:-1]:
                    if sentence.strip():
                        speak_checked(guard, sentence.strip(), spoken)
                buf = parts[-1]
        metrics.model_call("ollama", model, usage.get("prompt_eval_count", 0), usage.get("eval_count", 0),
                           latency_s=time.time() - t_call)
        if interrupted_now():
            return stop_for_interruption(history, spoken)
        if buf.strip():
            speak_checked(guard, buf.strip(), spoken)

        if any(c["function"]["name"] == "go_quiet" for c in calls):  # code handles it now, no 2nd model call
            history.append({"role": "assistant", "content": content, "tool_calls": calls})
            for c in calls:
                history.append({"role": "tool", "tool_name": c["function"]["name"],
                                "content": run_tool("go_quiet", c["function"].get("arguments") or {})
                                if c["function"]["name"] == "go_quiet"
                                else "FAILED: not run (going quiet)."})
            if rt.control.get("request") == "quiet":
                return
            continue  # not clear enough to go quiet: the model asks the user to confirm
        if not calls:
            release_checked(guard, spoken)
            bad = guard.unresolved()
            if bad:
                log.warning("claim check: held back unverified %r", bad)
                guard.drop_held()
                said = " ".join(spoken)
                if said:
                    history.append({"role": "assistant", "content": said})
                if not corrected:
                    corrected = True
                    history.append({"role": "user", "content": correction_note(bad)})
                    continue
                if not said:
                    said = rt.phrases.pick("unsure")
                    say(said)
                    history.append({"role": "assistant", "content": said})
                    spoken.append(said)
            else:
                history.append({"role": "assistant", "content": content})
            if spoken:
                print("Agent:", " ".join(spoken), flush=True)
            return
        history.append({"role": "assistant", "content": content, "tool_calls": calls})
        gate = cognition.round_gate(plan, len(calls))  # (limits: rounds, calls, time; REPLAN after a failure)
        if gate:
            for c in calls:
                history.append({"role": "tool", "tool_name": c["function"]["name"], "content": gate})
            if stopped:
                say(rt.phrases.pick("unsure"))
                return
            stopped = True
            continue
        if not spoken:
            filler("wait")
        if rt.tts_enabled:
            rt.speak_q.put(LOOP)  # loading loop while the tool runs and the answer is written
        for call in calls:
            fn = call["function"]
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args or "{}")
            if clean_args(args):  # (speech direction never reaches a tool's arguments, nor the history's copy of them)
                fn["arguments"] = args if isinstance(fn.get("arguments"), dict) else json.dumps(args)
                log.info("delivery tags removed from %s's arguments", fn["name"])
            log.info("tool %s %s", fn["name"], json.dumps(args))
            if interrupted_now():  # (they countermanded it: the remaining actions don't run)
                out = "FAILED: not run, they interrupted."
            else:
                t0 = time.time()
                out = plan.run(fn["name"], args).to_model()  # (a plan: a step on something that failed earlier isn't run)
                rt.turn.timing["tools"] = rt.turn.timing.get("tools", 0.0) + time.time() - t0
            log.info("tool result: %s", out[:160])
            guard.tool_result(fn["name"], out)
            history.append({"role": "tool", "tool_name": fn["name"], "content": out})
        release_checked(guard, spoken)
