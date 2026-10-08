"""The audit log: one line per action that changes something, or that was refused or held for a yes. Append-only,
monthly files (logs/audit-YYYYMM.jsonl), never rotated away automatically; it's how you check afterwards what Jarvis did,
why, and on whose words.

    {"t", "clock", "action", "risk", "outcome": OK/FAILED/NEEDS_CONFIRMATION/..., "verified", "via": reflex/model/dashboard,
     "words": what you said that turn (left out for private actions), "args": names only for private actions}

Secrets in the words are redacted the same way as the diagnostic log (livelog.redact).
"""

import json
import threading
import time
from pathlib import Path

from room_agent import config

_lock = threading.Lock()


def path(now=None):
    folder = Path(config.DIAGNOSTICS_DIR)
    return folder / f"audit-{time.strftime('%Y%m', time.localtime(now or time.time()))}.jsonl"


def record(cap, result, via="model"):
    """Called by the executor for every state-changing action and every refusal / confirmation request."""
    try:
        from room_agent import livelog
        from room_agent import runtime as rt

        outcome = str(result.message).split(":", 1)[0]
        if not cap.changes_state and outcome == "OK":
            return  # (reads are not audited: only what changes something, and what was refused)
        private = bool(getattr(cap, "private", False))
        row = {"t": round(time.time(), 3), "clock": time.strftime("%Y-%m-%d %H:%M:%S"), "action": cap.name,
               "risk": str(getattr(cap.risk, "value", cap.risk)), "outcome": outcome, "verified": bool(result.verified),
               "via": via, "turn": rt.turn_no}
        if private:
            row["args"] = sorted((result.parameters or {}).keys())
        else:
            row["args"] = {k: livelog.redact(str(v), 200) for k, v in (result.parameters or {}).items() if k != "confidence"}
            row["words"] = livelog.redact(rt.turn_text or "", 300)
            row["result"] = livelog.redact(str(result.message), 240)
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock, open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass  # (auditing must never break an action)


def event(kind, **fields):
    """A non-action event worth keeping (an emergency stop, a refused request from outside)."""
    try:
        row = {"t": round(time.time(), 3), "clock": time.strftime("%Y-%m-%d %H:%M:%S"), "event": kind, **fields}
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock, open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def recent(n=50):
    try:
        lines = path().read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]
    except (OSError, ValueError):
        return []
