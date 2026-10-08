"""Per-mission cost accounting and the hard budget.

Every paid thing a mission does goes through charge():
    model calls      tokens in / out, priced with llm/budget.py's table (the same prices as everything else)
    paid API calls   e.g. Google Places requests, at the configured per-request estimate
Before spending, check(estimate) refuses (BudgetExceeded) if the mission's spend plus the estimate would pass its budget;
the engine then pauses the mission ("paused: budget") instead of continuing. Free work (OpenStreetMap, fetching public
pages, generating sites from templates) costs nothing and is never blocked.
"""

import logging
import threading

log = logging.getLogger("room-agent")
_local = threading.local()


class BudgetExceeded(Exception):
    def __init__(self, spent, budget, estimate, what):
        super().__init__(f"{what} would cost ~${estimate:.4f}; spent ${spent:.4f} of ${budget:.2f}")
        self.spent, self.budget, self.estimate, self.what = spent, budget, estimate, what


def bind(mission_id):
    """The mission the current thread is working for (the engine sets it around each step)."""
    _local.mission = mission_id


def current():
    return getattr(_local, "mission", None)


def check(estimate_usd, what, mission_id=None):
    from room_agent.missions.store import store

    mid = mission_id or current()
    if not mid:
        return
    m = store().mission(mid)
    if m and m["spent_usd"] + float(estimate_usd) > m["budget_usd"]:
        raise BudgetExceeded(m["spent_usd"], m["budget_usd"], float(estimate_usd), what)


def charge(usd=0.0, tokens_in=0, tokens_out=0, requests=0, what="", mission_id=None):
    from room_agent.missions.store import store

    mid = mission_id or current()
    if not mid:
        return
    store().add_spend(mid, usd, tokens_in, tokens_out, requests)
    if usd:
        log.info("mission %s: %s cost $%.5f", mid, what or "a call", usd)


def model_estimate(model, prompt_chars, max_out_tokens):
    """An upper-bound estimate for one model call (prompt ~4 chars a token)."""
    from room_agent.llm.budget import price

    p_in, _, _, p_out = price(model)
    return (prompt_chars / 4 * p_in + max_out_tokens * p_out) / 1e6
