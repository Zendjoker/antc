"""What the paid models cost: computed from each response's own usage numbers, per provider, per day.

The total is kept in spend.json so a restart doesn't reset it. Past DAILY_BUDGET_USD the router stops calling paid
models for the rest of the day. Prices are per 1M tokens: (input, cached input, cache write, output).
"""

import datetime
import json
import logging
import threading

from room_agent.config import DAILY_BUDGET_USD, SPEND_FILE

log = logging.getLogger("room-agent")

PRICES = {  # checked against the providers' pricing pages, October 2026. Longest matching prefix wins.
    "claude-haiku-4-5": (1.00, 0.10, 1.25, 5.00),
    "claude-sonnet-5-5": (2.00, 0.20, 2.50, 10.00),
    "claude-sonnet-5": (2.00, 0.20, 2.50, 10.00),
    "claude-opus-5-5": (4.00, 0.20, 5.00, 20.00),
    "gpt-5-nano": (0.05, 0.005, 0.05, 0.40),
    "gpt-5-mini": (0.25, 0.025, 0.25, 2.00),
    "gpt-5.4-nano": (0.20, 0.02, 0.20, 1.25),
    "gpt-5.4-mini": (0.75, 0.075, 0.75, 4.50),
    "gpt-5.6-luna": (0.20, 0.02, 0.20, 1.20),
    "gpt-5": (1.25, 0.125, 1.25, 10.00),
}
UNKNOWN = (5.00, 0.50, 6.25, 30.00)  # a model not in the table is assumed expensive, so the budget stays safe


def price(model):
    key = max((k for k in PRICES if model.startswith(k)), key=len, default=None)
    if key is None:
        log.warning("no price known for %s: counting it at %s per 1M tokens to stay safe", model, UNKNOWN)
    return PRICES.get(key, UNKNOWN)


class Budget:
    def __init__(self, path=SPEND_FILE, daily_limit=DAILY_BUDGET_USD):
        self.path, self.limit = path, daily_limit
        self._lock = threading.Lock()
        self.turn = {}  # this turn's spend per model, for the per-turn log line
        self.warned_on = ""  # the day the "budget reached" line was said (said once a day)
        self.data = self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    def _today(self):
        day = datetime.date.today().isoformat()
        if self.data.get("date") != day:  # a new day starts from zero
            self.data = {"date": day, "usd": {}, "calls": 0}
        return self.data

    def record(self, provider, model, fresh_in=0, cached_in=0, cache_write=0, out=0):
        """Add one call's cost. Token counts as the API reported them: `fresh_in` is input billed at full price."""
        p_in, p_cached, p_write, p_out = price(model)
        usd = (fresh_in * p_in + cached_in * p_cached + cache_write * p_write + out * p_out) / 1e6
        with self._lock:
            d = self._today()
            d["usd"][provider] = d["usd"].get(provider, 0.0) + usd
            d["calls"] = d.get("calls", 0) + 1
            self.turn[model] = self.turn.get(model, 0.0) + usd
            try:
                self.path.write_text(json.dumps(d, indent=1), encoding="utf-8")
            except OSError as e:
                log.warning("couldn't save today's spend: %s", e)
        log.debug("call %s: %d fresh + %d cached + %d cache-write in, %d out = $%.5f", model, fresh_in, cached_in,
                  cache_write, out, usd)
        return usd

    def today(self):
        with self._lock:
            return dict(self._today()["usd"])

    def total(self):
        return sum(self.today().values())

    def exceeded(self):
        return self.limit > 0 and self.total() >= self.limit

    def start_turn(self):
        self.turn = {}

    def turn_line(self):
        """'turn cost: 0.21c (gpt-5-mini), today: 3.4c (openai 2.1c, claude 1.3c)'"""
        if not self.turn:
            return ""
        spent = sum(self.turn.values()) * 100
        by = ", ".join(f"{k} {v * 100:.1f}c" for k, v in sorted(self.today().items()))
        return f"turn cost: {spent:.2f}c ({', '.join(self.turn)}), today: {self.total() * 100:.1f}c ({by})"


budget = Budget()
