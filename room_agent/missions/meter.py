"""Per-mission cost accounting and the hard budget: reserve before, settle after, for every paid call.

    with meter.paid(estimate_usd, "what", provider=..., model=...) as charge:
        response = <the paid call>                 # (no hidden SDK retries: see llm.py)
        charge.actual(usd, tokens_in, tokens_out)   # what the provider's usage numbers say it cost

    - reserve: ATOMIC in SQLite (one UPDATE ... WHERE spent + reserved + estimate <= budget): two steps, or an
      abandoned step and a new one, can't both squeeze under the budget. The UPDATE and its ledger row are one
      transaction (rolled back together). Refused -> BudgetExceeded (the engine pauses the mission: "paused_budget").
      The PC's daily model budget (DAILY_BUDGET_USD) is checked too: DailyBudgetExceeded ("paused_daily"; raising the
      mission budget doesn't help).
    - "never sent" vs "uncertain": a stop before sending (runctx.CancelledBeforeSend), a request refused before it went
      out, or a connection that never opened (NotSent) releases the reservation. Anything that may have reached the
      provider is counted (see below).
    - settle: the reservation is replaced by the actual cost. If the call failed in a way where the provider may
      still have charged (timeout, dropped connection, server error, no usage numbers), the FULL estimate is recorded
      as an "uncertain" charge: the budget never assumes a free call that might not have been free.
      Released (nothing charged) only when the request provably wasn't billed: it was never sent, or the provider
      rejected it outright (4xx: bad request, auth, rate limit).
    - after a crash, reservations still open are settled as uncertain at their full estimate (engine.load).
Estimates are upper bounds: input ~3 characters a token (pessimistic), the output at its full cap, the model's price
(unknown models at llm/budget.py's expensive UNKNOWN price). Free work (OpenStreetMap, fetching public pages, templates)
costs nothing and never touches the ledger.
"""

import json
import logging
import threading
from contextlib import contextmanager

from room_agent.missions import runctx

log = logging.getLogger("room-agent")
_local = threading.local()


class BudgetExceeded(Exception):
    def __init__(self, spent, budget, estimate, what):
        super().__init__(f"{what} would cost up to ${estimate:.4f}; spent / reserved ${spent:.4f} of ${budget:.2f}")
        self.spent, self.budget, self.estimate, self.what = spent, budget, estimate, what


class DailyBudgetExceeded(BudgetExceeded):
    """Jarvis's whole-PC daily model budget (DAILY_BUDGET_USD) is the limit, not the mission's own budget: raising the
    mission budget can't help; it resets the next day."""


class NotSent(Exception):
    """Raise (or wrap) inside paid() when the request provably never reached the provider: the reservation is released."""


def bind(mission_id, step_key=""):
    """The mission (and step) the current thread is working for (the engine sets it for each step run)."""
    _local.mission = mission_id
    _local.step = step_key


def current():
    return getattr(_local, "mission", None)


class _Charge:
    def __init__(self, cid, estimate):
        self.id, self.estimate = cid, estimate
        self._actual = None
        self.result = None  # (what the provider returned, kept with the settlement when the call has an idem_key)

    def actual(self, usd, tokens_in=0, tokens_out=0):
        self._actual = (max(0.0, float(usd or 0.0)), int(tokens_in or 0), int(tokens_out or 0))

    def not_billed(self):
        """The provider answered and it's known not to be billed (e.g. a 4xx rejection)."""
        self._actual = ("released", 0, 0)


def _daily_check(estimate, what):
    from room_agent.llm.budget import budget

    if budget.limit > 0 and budget.total() + estimate > budget.limit:
        raise DailyBudgetExceeded(budget.total(), budget.limit, estimate, f"{what} (today's model budget, DAILY_BUDGET_USD)")


def daily_exhausted(estimate=0.0):
    """True if today's model budget can't take another call of this size (for resume decisions)."""
    from room_agent.llm.budget import budget

    return budget.limit > 0 and budget.total() + float(estimate) > budget.limit


@contextmanager
def paid(estimate_usd, what, provider="", model="", mission_id=None, daily=True, idem_key=None):
    """Reserve -> run the block -> settle. Outside a mission (no mission bound) the call runs unmetered here (Jarvis's
    own daily budget still applies through llm/budget.py).
    Each paid call is an operation (store.operations) created with its reservation and closed with its settlement, in
    the same transactions. idem_key: the logical identity of this call (e.g. "this step's edit"): its result is stored
    with the settlement so a retry can reuse it (see completed_result) instead of paying again."""
    from room_agent.missions.store import store

    runctx.check()
    mid = mission_id or current()
    est = max(0.0, float(estimate_usd))
    if daily:
        _daily_check(est, what)
    if not mid:
        c = _Charge(None, est)
        yield c
        return
    s = store()
    cid = s.reserve(mid, est, what, provider, model, getattr(_local, "step", "") or "", idem_key=idem_key)
    if cid is None:
        m = s.mission(mid) or {"spent_usd": 0, "reserved_usd": 0, "budget_usd": 0}
        raise BudgetExceeded(m["spent_usd"] + m.get("reserved_usd", 0), m["budget_usd"], est, what)
    c = _Charge(cid, est)
    try:
        yield c
    except (NotSent, runctx.CancelledBeforeSend) as e:  # (provably never reached the provider: nothing to count)
        s.settle(cid, 0, state="released", note=f"not sent: {e.__class__.__name__}: {e}"[:300])
        raise
    except BaseException as e:
        if c._actual is not None and c._actual[0] == "released":
            s.settle(cid, 0, state="released", note=f"not billed: {e.__class__.__name__}")
        elif c._actual is not None:  # (the provider answered with usage; something after it failed)
            usd, tin, tout = c._actual
            s.settle(cid, usd, tin, tout, note=f"error after the call: {e.__class__.__name__}")
        else:
            s.settle(cid, est, state="uncertain", note=f"{e.__class__.__name__}: the provider may have charged; "
                                                     "counted at the full estimate")
            log.warning("mission %s: %s ended uncertain (%s): counted $%.4f", mid, what, e.__class__.__name__, est)
        raise
    if c._actual is None:
        s.settle(cid, est, state="uncertain", note="no usage reported: counted at the full estimate")
    elif c._actual[0] == "released":
        s.settle(cid, 0, state="released", note="not billed")
    else:
        usd, tin, tout = c._actual
        row = s.settle(cid, usd, tin, tout, result=json.dumps({"text": c.result}) if c.result is not None else None)
        if row and usd > est * 1.05 + 1e-6:
            log.warning("mission %s: %s cost $%.5f, above its estimate $%.5f (estimate too low?)", mid, what, usd, est)
            s.event(mid, f"{what} cost ${usd:.4f}, more than its ${est:.4f} estimate", "warn")


class OperationUncertain(Exception):
    """An earlier attempt of this exact paid operation may have been billed but its result was lost (Jarvis stopped
    mid-call). It isn't repeated automatically: that could pay twice."""


def completed_result(idem_key):
    """The stored result of a completed paid operation with this key, or None. Raises OperationUncertain if an earlier
    attempt's outcome is unknown (still open, or settled as uncertain)."""
    from room_agent.missions.store import store

    if not idem_key:
        return None
    op = store().op_by_key(idem_key)
    if op is None or op["state"] == "failed":
        return None
    if op["state"] == "completed" and op["result"]:
        r = op["result"]  # (stored as {"text": ...}: the result column is JSON-decoded on read)
        return r.get("text") if isinstance(r, dict) else r
    raise OperationUncertain(f"an earlier attempt of this paid operation ({op['what']}) ended {op['state']}: it may have "
                             "been billed and its result wasn't kept, so it isn't repeated automatically")


def model_estimate(model, prompt_chars, max_out_tokens):
    """An upper bound for one model call: ~3 characters a token (pessimistic), +200 tokens of message overhead, the
    output at its full cap, at the model's list price (expensive default for an unknown model)."""
    from room_agent.llm.budget import price

    p_in, _, _, p_out = price(model)
    tokens_in = prompt_chars / 3 + 200
    return (tokens_in * p_in + max_out_tokens * p_out) / 1e6


def settle_orphans():
    """At startup: reservations left open by a crash -> uncertain, at their full estimate. -> how many."""
    from room_agent.missions.store import store

    s = store()
    rows = s.open_charges()
    for r in rows:
        s.settle(r["id"], r["estimate_usd"], state="uncertain", note="Jarvis stopped during the call: counted at the full "
                                                                     "estimate")
    return len(rows)


def count_request(what=""):
    """A free request (OpenStreetMap, a web page): counted for the dashboard, never charged."""
    from room_agent.missions.store import store

    mid = current()
    if mid:
        runctx.check()
        store().add_spend(mid, requests=1)
