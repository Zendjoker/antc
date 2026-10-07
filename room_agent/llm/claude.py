"""Claude backend: an adapter for llm/loop.py. Streams the reply and caches the fixed part of the prompt."""

import logging

from room_agent.config import MAX_TOKENS_REPLY, MODEL, PROMPT_CACHE
from room_agent.llm.budget import budget
from room_agent.llm.client import client
from room_agent.llm.loop import Reply, Use, run_model

log = logging.getLogger("room-agent")


def claude_system(fixed, changing):
    """Tools + the fixed rules come first and carry the cache marker (Haiku 4.5 caches prefixes of 4096+ tokens; ours
    is ~4,800). Everything that changes per request goes after it, so it never breaks the cache."""
    if not PROMPT_CACHE:
        return fixed + changing
    return [{"type": "text", "text": fixed, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": changing}]


def record_claude_usage(usage, model=MODEL):
    """Bill one call from the usage Claude reported, and say whether the cache worked."""
    if usage is None:
        return 0.0
    fresh = getattr(usage, "input_tokens", 0) or 0
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    log.info("claude usage: %d in (%d from cache, %d written to cache), %d out", fresh + read + write, read, write, out)
    return budget.record("claude", model, fresh_in=fresh, cached_in=read, cache_write=write, out=out)


class ClaudeCall:
    provider = "claude"

    def __init__(self, history, fixed, changing, tools, model=MODEL):
        self.kwargs = dict(model=model, max_tokens=MAX_TOKENS_REPLY, system=claude_system(fixed, changing),
                           tools=tools, messages=history)
        self.model, self.done = model, False

    def __enter__(self):
        self._cm = client().messages.stream(**self.kwargs)
        self._stream = self._cm.__enter__()
        return self

    def deltas(self):
        return self._stream.text_stream

    def finish(self):
        msg = self._stream.get_final_message()
        self.done = True
        record_claude_usage(msg.usage, self.model)
        uses = [Use(b.id, b.name, b.input) for b in msg.content if b.type == "tool_use"]
        return Reply("tool_use" if msg.stop_reason == "tool_use" else "end", msg.content, uses)

    def __exit__(self, *exc):
        if not self.done:  # cut short (you interrupted): the input was still billed
            try:
                record_claude_usage(self._stream.current_message_snapshot.usage, self.model)
            except Exception:
                pass
        return self._cm.__exit__(*exc)


def ask_claude(history):
    run_model(history, ClaudeCall)
