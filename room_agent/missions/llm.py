"""The only way a mission calls a model: budget reserved first, settled from the provider's usage afterwards.

    tier="light"   simple wording: MISSION_LIGHT_MODEL (OpenAI) or Claude Haiku without an OpenAI key
    tier="strong"  demo-site edits: MISSION_STRONG_MODEL (a Claude model)

No hidden retries: the SDK clients are used with max_retries=0, so one call = at most one billed request (a default
client may silently retry a timed-out request that was already billed). A timeout is set on every call.
Prompts carry compact structured facts, never whole pages, screenshots, histories or the tool catalog.
"""

import logging
import time

from room_agent import config
from room_agent.missions import meter, runctx

log = logging.getLogger("room-agent")
TIMEOUT_S = {"light": 60, "strong": 240}


class ModelUnavailable(Exception):
    pass


def _pick(tier):
    if tier == "strong":
        if not config.ANTHROPIC_KEY:
            raise ModelUnavailable("no Anthropic API key for the strong model (ANTHROPIC_API_KEY)")
        return "claude", config.MISSION_STRONG_MODEL
    if config.OPENAI_KEY:
        return "openai", config.MISSION_LIGHT_MODEL
    if config.ANTHROPIC_KEY:
        return "claude", config.MODEL
    raise ModelUnavailable("no model API key configured")


def _not_billed(e):
    """A provider error that means the request was rejected before any work (no charge): 4xx."""
    status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    return isinstance(status, int) and 400 <= status < 500


def _never_sent(e):
    """A connection that couldn't be opened (DNS failure, refused, connect timeout): the request never reached the
    provider. A read timeout / dropped connection after sending is NOT this (the provider may have charged)."""
    kinds = _connect_errors()
    seen, cur = set(), e
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if kinds and isinstance(cur, kinds):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _connect_errors():
    """The 'connection never opened' exception classes of whichever HTTP library the SDKs use: the current openai /
    anthropic SDKs are built on httpx2 (not httpx), older ones on httpx. Both are recognized."""
    out = []
    for name in ("httpx2", "httpx"):
        try:
            mod = __import__(name)
        except ImportError:
            continue
        out += [getattr(mod, c) for c in ("ConnectError", "ConnectTimeout", "UnsupportedProtocol") if hasattr(mod, c)]
    return tuple(out)


def complete(prompt, system="", tier="light", max_tokens=800, what="model call", idem_key=None):
    """-> text. Raises meter.BudgetExceeded before spending past the mission's budget, ModelUnavailable without a key,
    runctx.Cancelled if the step was stopped (before sending; a call already sent is still charged)."""
    from room_agent.llm.budget import budget

    prior = meter.completed_result(idem_key)  # (raises OperationUncertain for an unresolved earlier attempt)
    if prior is not None:
        log.info("mission model call (%s): reusing the stored result of %s (not paid again)", what, idem_key)
        return prior
    provider, model = _pick(tier)
    out_cap = max_tokens * 3 if provider == "openai" else max_tokens  # (GPT-5 thinking tokens count as output)
    estimate = meter.model_estimate(model, len(prompt) + len(system), out_cap)
    t0 = time.time()
    runctx.check_before_send()
    with meter.paid(estimate, what, provider=provider, model=model, idem_key=idem_key) as charge:
        runctx.check_before_send()  # (released, not counted, if stopped here: nothing was sent yet)
        try:
            if provider == "openai":
                text, tin, tout, usd = _openai(model, prompt, system, out_cap, TIMEOUT_S[tier], budget)
            else:
                text, tin, tout, usd = _claude(model, prompt, system, max_tokens, TIMEOUT_S[tier], budget)
        except runctx.CancelledBeforeSend:
            raise
        except Exception as e:
            if _never_sent(e):
                raise meter.NotSent(f"{e.__class__.__name__}: the connection never opened") from e
            if _not_billed(e):
                charge.not_billed()
            raise
        from room_agent.missions import crashpoints

        crashpoints.hit("after_external_response")
        charge.actual(usd, tin, tout)
        if idem_key:
            charge.result = text  # (stored with the settlement: a retry reuses it instead of paying again)
    log.info("mission model call (%s, %s): %d in / %d out, $%.5f, %.1fs", what, model, tin, tout, usd, time.time() - t0)
    runctx.check()  # (stopped while the model was answering: the cost is recorded, the text isn't used)
    return text


def _openai(model, prompt, system, out_cap, timeout, budget):
    import openai

    from room_agent.llm.openai_backend import openai_client

    c = openai_client().with_options(max_retries=0, timeout=timeout)
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    kw = dict(model=model, messages=msgs, max_completion_tokens=out_cap)
    try:
        r = c.chat.completions.create(reasoning_effort=config.OPENAI_REASONING, **kw) if config.OPENAI_REASONING \
            else c.chat.completions.create(**kw)
    except openai.BadRequestError as e:  # (400: not billed; this model doesn't take reasoning_effort)
        if "reasoning" not in str(e).lower() or not config.OPENAI_REASONING:
            raise
        runctx.check_before_send()  # (the first request was rejected unbilled; this one isn't sent yet)
        r = c.chat.completions.create(**kw)
    u = r.usage
    cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    tin, tout = u.prompt_tokens or 0, u.completion_tokens or 0
    usd = budget.record("openai", model, fresh_in=tin - cached, cached_in=cached, out=tout)
    return r.choices[0].message.content or "", tin, tout, usd


def _claude(model, prompt, system, max_tokens, timeout, budget):
    from room_agent.llm.client import client

    c = client().with_options(max_retries=0, timeout=timeout)
    r = c.messages.create(model=model, max_tokens=max_tokens, system=system or "You are a careful assistant.",
                          messages=[{"role": "user", "content": prompt}])
    u = r.usage
    tin, tout = (u.input_tokens or 0), (u.output_tokens or 0)
    cached = getattr(u, "cache_read_input_tokens", 0) or 0
    write = getattr(u, "cache_creation_input_tokens", 0) or 0
    usd = budget.record("claude", model, fresh_in=tin, cached_in=cached, cache_write=write, out=tout)
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text"), tin + cached + write, tout, usd
