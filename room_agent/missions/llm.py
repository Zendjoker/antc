"""The only way a mission calls a model: budget-checked first, charged to the mission and the daily spend afterwards.

    tier="light"   simple wording / classification: OpenAI gpt-5-mini (or Claude Haiku without an OpenAI key)
    tier="strong"  code edits: MISSION_STRONG_MODEL (a Claude model)

Prompts carry compact structured facts, never whole pages, screenshots, histories or the tool catalog.
"""

import logging
import time

from room_agent import config
from room_agent.missions import meter

log = logging.getLogger("room-agent")


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


def complete(prompt, system="", tier="light", max_tokens=800, what="model call"):
    """-> text. Raises meter.BudgetExceeded before spending past the mission's budget, ModelUnavailable without a key."""
    from room_agent.llm.budget import budget

    provider, model = _pick(tier)
    meter.check(meter.model_estimate(model, len(prompt) + len(system), max_tokens), what)
    t0 = time.time()
    if provider == "openai":
        from room_agent.llm.openai_backend import create

        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        r = create(model, msgs, max_completion_tokens=max_tokens * 3)
        text = r.choices[0].message.content or ""
        u = r.usage
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        tin, tout = u.prompt_tokens or 0, u.completion_tokens or 0
        usd = budget.record("openai", model, fresh_in=tin - cached, cached_in=cached, out=tout)
    else:
        from room_agent.llm.client import client

        r = client().messages.create(model=model, max_tokens=max_tokens, system=system or "You are a careful assistant.",
                                     messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        u = r.usage
        tin, tout = (u.input_tokens or 0), (u.output_tokens or 0)
        usd = budget.record("claude", model, fresh_in=tin, cached_in=getattr(u, "cache_read_input_tokens", 0) or 0, out=tout)
    meter.charge(usd or 0.0, tin, tout, what=what)
    log.info("mission model call (%s, %s): %d in / %d out, %.1fs", what, model, tin, tout, time.time() - t0)
    return text
