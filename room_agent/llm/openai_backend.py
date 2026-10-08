"""OpenAI backend: an adapter for llm/loop.py, for the cheap default model.

The conversation history is kept in Claude's format (both models take turns in the same conversation); it's converted
to OpenAI messages for each call, and OpenAI's reply is stored back in Claude's format. Tool calls and their results
always stay paired. The fixed part of the prompt goes first and never changes, so OpenAI's automatic prompt caching
bills it at a tenth of the price."""

import json
import logging
import re
import time

from room_agent import runtime as rt
from room_agent.config import MAX_TOKENS_REPLY, OPENAI_KEY, OPENAI_MODEL, OPENAI_REASONING
from room_agent.llm.budget import budget
from room_agent.llm.loop import Reply, Use, run_model

log = logging.getLogger("room-agent")
_client = None
_reasoning_ok = {}  # model -> False once it refused reasoning_effort (older/other models)


def openai_client():
    global _client
    if _client is None:
        import openai

        _client = openai.OpenAI(api_key=OPENAI_KEY, timeout=30, max_retries=1)
    return _client


def _blocks(content):
    """Content blocks as plain dicts, whether they came from the Claude SDK, OpenAI (dicts) or elsewhere."""
    out = []
    for b in content:
        if isinstance(b, dict):
            out.append(b)
        elif hasattr(b, "model_dump"):
            out.append(b.model_dump(exclude_none=True))
        else:
            out.append({k: v for k, v in vars(b).items() if v is not None})
    return out


OLD_RESULT_CHARS = 300  # a tool result from an earlier exchange (a web search, a briefing) only needs its gist


def to_openai(history):
    """Claude-format history -> OpenAI chat messages. Tool results from earlier exchanges are shortened."""
    out = []
    last_user = max((i for i, m in enumerate(history) if m["role"] == "user" and isinstance(m["content"], str)), default=0)
    for i, m in enumerate(history):
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        blocks = _blocks(content)
        if m["role"] == "assistant":
            text = " ".join(b["text"] for b in blocks if b.get("type") == "text" and b.get("text"))
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                     for b in blocks if b.get("type") == "tool_use"]
            msg = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            for b in blocks:
                if b.get("type") == "tool_result":
                    c = b.get("content")
                    c = c if isinstance(c, str) else json.dumps(c)
                    if i < last_user and len(c) > OLD_RESULT_CHARS:
                        c = c[:OLD_RESULT_CHARS] + "..."
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": c})
                elif b.get("type") == "text" and b.get("text"):
                    out.append({"role": "user", "content": b["text"]})
    return out


# Tools only some turns need: each capability's area (room_agent/abilities/ and any module that registers one) says which
# recent words make it worth offering, and when it's in use anyway ("close it" right after an app was opened). Plain
# chat doesn't pay for tools it can't need. Capabilities with no area are always offered.
RECENT_MESSAGES = 6
def _recent_text(history):
    """The words that decide which tool areas are offered: what THEY said recently, the tools used recently, and only
    Jarvis's last reply if it asked them something (so "yes" to "want me to set a timer?" still has the timer tools).
    Jarvis's other replies are left out: a reply that merely mentioned volume, apps and the door used to switch all
    those areas on for the next several turns."""
    parts = []
    recent = history[-RECENT_MESSAGES:]
    last_reply = next((i for i in range(len(recent) - 1, -1, -1) if recent[i]["role"] == "assistant"), None)
    for i, m in enumerate(recent):
        if isinstance(m["content"], str):
            if m["role"] == "user" or (i == last_reply and m["content"].rstrip().endswith("?")):
                parts.append(m["content"])
            continue
        for b in _blocks(m["content"]):
            if b.get("type") == "tool_use":
                parts.append(b.get("name") or "")  # (a tool used recently counts as touching its group)
            elif m["role"] == "user" or (i == last_reply and str(b.get("text") or "").rstrip().endswith("?")):
                parts.append(b.get("text") or "")
    return " ".join(parts)


def relevant_tools(tools, history):
    from room_agent.actions import core

    core.ensure_loaded()
    text = _recent_text(history)
    pending = core.REGISTRY.get(rt.pending["tool"]) if rt.pending else None  # (a half-asked request keeps its area)
    wanted = {}
    for name, group in core.GROUPS.items():
        try:
            wanted[name] = (group.hints is None or bool(group.hints.search(text)) or group.live()
                            or bool(pending and pending.group == name))
        except Exception:
            wanted[name] = True
    return [t for t in tools if wanted.get(getattr(core.REGISTRY.get(t["name"]), "group", None), True)]


def openai_tools(tools):
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                               "parameters": t["input_schema"]}} for t in tools]


def create(model, messages, **kw):
    """One request, with GPT-5's thinking effort set when the model accepts it (it's retried without otherwise)."""
    import openai

    if OPENAI_REASONING and _reasoning_ok.get(model, True):
        try:
            return openai_client().chat.completions.create(model=model, messages=messages,
                                                           reasoning_effort=OPENAI_REASONING, **kw)
        except openai.BadRequestError as e:
            if "reasoning" not in str(e).lower():
                raise
            _reasoning_ok[model] = False
            log.info("%s doesn't take reasoning_effort=%s; using its default", model, OPENAI_REASONING)
    return openai_client().chat.completions.create(model=model, messages=messages, **kw)


def record_openai_usage(usage, model):
    if usage is None:
        return 0.0
    prompt = usage.prompt_tokens or 0
    details = usage.prompt_tokens_details
    cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    out = usage.completion_tokens or 0
    log.info("openai usage: %d in (%d from cache), %d out", prompt, cached, out)
    return budget.record("openai", model, fresh_in=prompt - cached, cached_in=cached, out=out)


class OpenAICall:
    provider = "openai"

    def __init__(self, history, fixed, changing, tools, model=OPENAI_MODEL):
        self.model = model
        offered = relevant_tools(tools, history)
        from room_agent.prompt import fixed_prompt

        self.messages = ([{"role": "system", "content": fixed_prompt({t["name"] for t in offered}, offered)},
                          {"role": "system", "content": changing}] + to_openai(history))
        self.tools = openai_tools(offered)
        self.text, self.calls, self.usage, self.finished = "", {}, None, False

    def __enter__(self):
        self._resp = create(self.model, self.messages, tools=self.tools or None, stream=True,
                            stream_options={"include_usage": True},
                            max_completion_tokens=MAX_TOKENS_REPLY * 3,  # (includes GPT-5's thinking tokens)
                            prompt_cache_key="jarvis")
        return self

    def deltas(self):
        for chunk in self._resp:
            if chunk.usage:
                self.usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            for tc in delta.tool_calls or []:
                c = self.calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                c["id"] = tc.id or c["id"]
                if tc.function:
                    c["name"] = tc.function.name or c["name"]
                    c["args"] += tc.function.arguments or ""
            if delta.content:
                self.text += delta.content
                yield delta.content

    def finish(self):
        for _ in self.deltas():  # (normally already consumed; makes sure the usage chunk was read)
            pass
        self.finished = True
        record_openai_usage(self.usage, self.model)
        content = [{"type": "text", "text": self.text}] if self.text.strip() else []
        uses = []
        for i in sorted(self.calls):
            c = self.calls[i]
            try:
                args = json.loads(c["args"] or "{}")
            except ValueError:
                args = {}  # (the tool checks then report what's missing)
            uses.append(Use(c["id"], c["name"], args if isinstance(args, dict) else {}))
            content.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": uses[-1].input})
        return Reply("tool_use" if uses else "end", content, uses)

    def __exit__(self, *exc):
        if not self.finished:
            record_openai_usage(self.usage, self.model)  # cut short: whatever was reported so far
        try:
            self._resp.close()
        except Exception:
            pass
        return False


def ask_openai(history):
    run_model(history, OpenAICall)
