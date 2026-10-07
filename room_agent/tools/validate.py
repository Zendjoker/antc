"""Checks that run before any tool: required parameters, types, ranges and risky actions all come from the tool
schema and the tables below, never from the model's say-so. The model understands the request; this decides
whether it may run."""

import time

from room_agent import runtime as rt

SCHEMAS = {}  # name -> parameter schema, filled in as capabilities register (actions/core.register)
HIGH_IMPACT = {"go_quiet": 0.8, "forget": 0.8, "cancel_timer": 0.7, "close_app": 0.7}  # minimum intent confidence before these run
PENDING_TURNS, PENDING_SECONDS = 3, 180  # how long an unfinished request is remembered


def _coerce(value, spec):
    kind = spec.get("type")
    try:
        if kind == "integer":
            if isinstance(value, bool) or float(value) != int(float(value)):
                raise ValueError
            return int(float(value))
        if kind == "number":
            return float(value)
        if kind == "boolean":
            if isinstance(value, str):
                word = value.strip().lower()
                if word in ("true", "yes", "1"):
                    return True
                if word in ("false", "no", "0"):
                    return False
                raise ValueError
            return bool(value)
        if kind == "string":
            return str(value).strip()
    except (TypeError, ValueError):
        raise ValueError({"integer": "a whole number", "number": "a number", "boolean": "true or false"}.get(kind, "valid"))
    return value


def _loaded():
    """The schemas are registered by the capability modules: make sure they've been loaded."""
    from room_agent.actions import core

    core.ensure_loaded()


def validate(name, args):
    """-> (clean_args, None) if the call may run, or (None, result_text) when it must not. The text goes back to the
    model as the tool result, so it says exactly what to do next."""
    _loaded()
    schema = SCHEMAS.get(name)
    if schema is None:
        return args, None
    props, required = schema.get("properties", {}), schema.get("required", [])
    args = dict(args or {})
    clean, missing = {}, []
    for key, spec in props.items():
        value = args.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if key in required:
                missing.append(key)
            continue
        try:
            value = _coerce(value, spec)
        except ValueError as e:
            return None, f"FAILED: '{key}' must be {e}. Nothing was done."
        if "enum" in spec and value not in spec["enum"]:
            return None, f"FAILED: '{key}' must be one of: {', '.join(map(str, spec['enum']))}. Nothing was done."
        low, high = spec.get("minimum"), spec.get("maximum")
        if (low is not None and value < low) or (high is not None and value > high):
            return None, f"FAILED: '{key}' must be between {low} and {high}. Nothing was done."
        clean[key] = value
    if missing:
        rt.pending = {"tool": name, "args": {k: v for k, v in clean.items() if k != "confidence"},
                      "missing": missing, "turn": rt.turn_no, "at": time.time()}
        what = "; ".join(f"{k} ({props[k].get('description', k)})" for k in missing)
        return None, (f"NEEDS: {what}. Nothing was done yet. Ask the user for only that, in one short question; "
                      "what they already told you is remembered.")
    need = HIGH_IMPACT.get(name)
    if need is not None and clean.get("confidence", 0.5) < need:
        rt.pending = {"tool": name, "args": {k: v for k, v in clean.items() if k != "confidence"}, "confirm": True,
                      "turn": rt.turn_no, "at": time.time()}
        return None, ("NEEDS_CONFIRMATION: nothing was done. This changes something important and the request wasn't "
                      "clearly explicit. Ask one short yes/no question to confirm; if they agree, call it again with "
                      "confidence 1.")
    clean.pop("confidence", None)
    return clean, None


def missing_required(name, args):
    """Required parameters the model left out of a call (checked before anything is said or run)."""
    from room_agent.tools.timers import as_timer

    _loaded()
    required = (SCHEMAS.get(name) or {}).get("required", [])
    args = args if isinstance(args, dict) else {}
    if as_timer(name, args, rt.turn_text):
        return []  # an alarm "in 10 seconds": it runs as a timer, nothing is missing
    return [k for k in required if args.get(k) is None or (isinstance(args[k], str) and not args[k].strip())]


def current_pending():
    """The unfinished request that the user's next message may be answering, or None."""
    p = rt.pending
    if p and (rt.turn_no - p["turn"] > PENDING_TURNS or time.time() - p["at"] > PENDING_SECONDS):
        rt.pending = None
    return rt.pending


def contracts(tools):
    """One line per tool saying what it needs and its limits, straight from the schemas (the model sees these as facts)."""
    out = []
    for t in tools:
        schema = t["input_schema"]
        required = schema.get("required", [])
        parts = []
        for key, spec in schema.get("properties", {}).items():
            if key == "confidence":
                continue
            limits = ""
            if "minimum" in spec or "maximum" in spec:
                limits = f" {spec.get('minimum', '')}..{spec.get('maximum', '')}"
            elif "enum" in spec:
                limits = " " + "/".join(map(str, spec["enum"]))
            parts.append(f"{key}{limits} {'REQUIRED' if key in required else 'optional'}")
        if parts:
            out.append(f"{t['name']}({', '.join(parts)})")
    return "; ".join(out)
