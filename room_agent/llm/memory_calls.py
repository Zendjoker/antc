"""The two calls the memory writer makes to a language model: one structured, one plain text.

Background memory work goes to the cheap model (OPENAI_MEMORY_MODEL) when an OpenAI key is set, with Claude as the
fallback. When today's paid budget is used up these calls are skipped (memory simply isn't updated that day)."""

import json
import logging

import requests

from room_agent.config import (LLM_DEFAULT, LLM_PROVIDER, MAX_TOKENS_MEMORY, MAX_TOKENS_SUMMARY, MEMORY_MODEL, MODEL,
                               OLLAMA_KEEP_ALIVE, OLLAMA_MODEL, OLLAMA_URL, OPENAI_KEY, OPENAI_MEMORY_MODEL)
from room_agent.llm.budget import budget
from room_agent.llm.claude import record_claude_usage
from room_agent.llm.client import client

log = logging.getLogger("room-agent")

# Newest models reject forced tool use; for those, "auto" plus the prompt's "always call it" is used
_NO_FORCED_TOOLS = ("opus-5-5", "sonnet-5-5", "fable-5-1", "mythos-5-1")


def _use_openai():
    return bool(OPENAI_KEY) and LLM_DEFAULT == "openai"


def _ollama(system, prompt, fmt=None):
    body = {"model": OLLAMA_MODEL, "stream": False, "think": False, "keep_alive": OLLAMA_KEEP_ALIVE,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    if fmt:
        body["format"] = fmt
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=120)
    r.raise_for_status()
    return r.json()["message"]["content"]


def _openai_tool(system, prompt, tool):
    from room_agent.llm.openai_backend import create, record_openai_usage

    resp = create(OPENAI_MEMORY_MODEL,
                  [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                  tools=[{"type": "function", "function": {"name": tool["name"], "description": tool["description"],
                                                           "parameters": tool["input_schema"]}}],
                  tool_choice={"type": "function", "function": {"name": tool["name"]}},
                  max_completion_tokens=MAX_TOKENS_MEMORY * 3)  # (includes GPT-5's thinking tokens)
    record_openai_usage(resp.usage, OPENAI_MEMORY_MODEL)
    calls = resp.choices[0].message.tool_calls or []
    return json.loads(calls[0].function.arguments or "null") if calls else None


def _openai_text(system, prompt):
    from room_agent.llm.openai_backend import create, record_openai_usage

    resp = create(OPENAI_MEMORY_MODEL, [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                  max_completion_tokens=MAX_TOKENS_SUMMARY * 4)
    record_openai_usage(resp.usage, OPENAI_MEMORY_MODEL)
    return resp.choices[0].message.content or ""


def _claude_tool(system, prompt, tool):
    model = MEMORY_MODEL or MODEL
    forced = not any(m in model for m in _NO_FORCED_TOOLS)
    msg = client().messages.create(
        model=model, max_tokens=MAX_TOKENS_MEMORY, system=system, tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]} if forced else {"type": "auto"},
        messages=[{"role": "user", "content": prompt}],
    )
    record_claude_usage(msg.usage, model)
    return next((b.input for b in msg.content if b.type == "tool_use"), None)


def _claude_text(system, prompt):
    model = MEMORY_MODEL or MODEL
    msg = client().messages.create(model=model, max_tokens=MAX_TOKENS_SUMMARY, system=system,
                                   messages=[{"role": "user", "content": prompt}])
    record_claude_usage(msg.usage, model)
    return "".join(b.text for b in msg.content if b.type == "text")


def memory_call_tool(system, prompt, tool):
    """One structured call for the memory writer. Returns the tool input dict, or None."""
    if LLM_PROVIDER == "ollama":
        schema = json.dumps(tool["input_schema"], indent=1)  # local models only see the schema via the prompt
        return json.loads(_ollama(f"{system}\n\nReply with JSON matching:\n{schema}", prompt, tool["input_schema"]) or "null")
    if budget.exceeded():
        log.info("memory update skipped: today's budget is used up")
        return None
    if _use_openai():
        try:
            return _openai_tool(system, prompt, tool)
        except Exception as e:
            log.warning("OpenAI memory call failed (%s), using Claude", e.__class__.__name__)
    return _claude_tool(system, prompt, tool)


def memory_call_text(system, prompt):
    if LLM_PROVIDER == "ollama":
        return _ollama(system, prompt)
    if budget.exceeded():
        log.info("conversation summary skipped: today's budget is used up")
        return ""
    if _use_openai():
        try:
            return _openai_text(system, prompt)
        except Exception as e:
            log.warning("OpenAI summary call failed (%s), using Claude", e.__class__.__name__)
    return _claude_text(system, prompt)
