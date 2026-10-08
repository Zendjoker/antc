"""Per-turn and per-goal numbers, so models and changes can be compared objectively. Recorded by code (the executor,
the model loops, budget.py) and saved to the experience database (cognition/experience.py), never anything secret:
counts, names of capabilities and models, token counts, durations.

    level, model calls (provider/model, input/output tokens, latency), actions, observations, tool failures,
    verification failures, refused retries, replans, user corrections and cancellations, time to first response,
    turn and goal latency, unnecessary actions avoided ("already so")
"""

import threading
import time

_cur = {"turn": None, "thread": None}


def begin_turn(level, goal_id=None):
    _cur["turn"] = {"started": time.time(), "level": level, "goal": goal_id, "model_calls": [], "actions": 0,
                    "observations": 0, "tool_failures": 0, "verification_failures": 0, "refused_retries": 0,
                    "replans": 0, "rounds": 0, "already_satisfied": 0, "corrections": 0, "cancellations": 0,
                    "first_response_s": None, "turn_s": None}
    _cur["thread"] = threading.get_ident()
    return _cur["turn"]


def current():
    return _cur["turn"]


def _mine():
    return _cur["turn"] is not None and threading.get_ident() == _cur["thread"]  # (background memory calls don't count)


def count(field, n=1):
    if _mine():
        _cur["turn"][field] = _cur["turn"].get(field, 0) + n


def model_call(provider, model, input_tokens, output_tokens, latency_s=None, cached=0):
    if _mine():
        _cur["turn"]["model_calls"].append({"provider": provider, "model": model, "in": int(input_tokens or 0),
                                            "out": int(output_tokens or 0), "cached": int(cached or 0),
                                            "latency_s": round(latency_s, 3) if latency_s is not None else None})


def last_call_latency(seconds):
    """The model loops time each call; budget.py (which records its tokens) doesn't know how long it took."""
    if _mine() and _cur["turn"]["model_calls"] and _cur["turn"]["model_calls"][-1]["latency_s"] is None:
        _cur["turn"]["model_calls"][-1]["latency_s"] = round(seconds, 3)


def set_latency(field, seconds):
    if _cur["turn"] is not None and _cur["turn"].get(field) is None:
        _cur["turn"][field] = round(seconds, 3)


def end_turn():
    t = _cur["turn"]
    if t is None:
        return None
    t["turn_s"] = round(time.time() - t["started"], 3)
    t["tokens_in"] = sum(c["in"] for c in t["model_calls"])
    t["tokens_out"] = sum(c["out"] for c in t["model_calls"])
    _cur["turn"] = None
    return t


def summarize(rows):
    """Aggregate turn records (dicts) -> a small report, per level."""
    out = {}
    for r in rows:
        lv = out.setdefault(r["level"], {"turns": 0, "model_calls": 0, "tokens_in": 0, "tokens_out": 0, "actions": 0,
                                         "observations": 0, "tool_failures": 0, "verification_failures": 0,
                                         "replans": 0, "turn_s": 0.0})
        lv["turns"] += 1
        lv["model_calls"] += len(r.get("model_calls", []))
        for k in ("tokens_in", "tokens_out", "actions", "observations", "tool_failures", "verification_failures", "replans"):
            lv[k] += r.get(k, 0) or 0
        lv["turn_s"] += r.get("turn_s") or 0
    for lv in out.values():
        lv["avg_turn_s"] = round(lv.pop("turn_s") / max(lv["turns"], 1), 3)
    return out
