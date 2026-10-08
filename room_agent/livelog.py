"""Live diagnostic log (livelog): one JSON line per event, for checking a real session afterwards. Off unless DIAGNOSTICS=1.

    logs/diagnostics-YYYYMMDD-HHMMSS.jsonl     (one file per run; the folder is git-ignored)

    speech   accepted / rejected, why (who it was: USER, AGENT_ECHO, NOISE, UNCERTAIN), recognition confidence, STT time
    state    conversation-state transitions (LISTENING -> PROCESSING -> SPEAKING ...) with the reason
    barge    an interruption: confirmed or rejected, and how long from your voice starting to the agent stopping
    tool     every action: name, argument names, success, verified, how long, the result (shortened)
    turn     where the time went: hearing, model's first words, tools, first sound after you stopped, whole turn

Never written: API keys, tokens, passwords (any .env credential value is replaced), phone numbers, email addresses, card-
or ID-like digit runs, and tool argument VALUES (only their names: what was asked can be private). Transcripts are kept
because that's what the mode is for; they're redacted the same way and stay on this PC.
"""

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from room_agent import config

log = logging.getLogger("room-agent")
_lock = threading.Lock()
_file = None
_secrets = None
_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}|sk_[A-Za-z0-9]{8,}|AC[0-9a-f]{32}|eyJ[A-Za-z0-9_\-]{20,}"), "[key]"),
    (re.compile(r"(?i)\b(key|token|secret|password|passcode|pin)\b(\s*(is|:|=)\s*)\S+"), r"\1\2[redacted]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[email]"),
    (re.compile(r"\+?\d[\d\s().-]{8,}\d"), "[number]"),  # phone, card, account or ID numbers
]


def enabled():
    return config.DIAGNOSTICS


def _credential_values():
    """Every credential-looking value in .env and the environment, so none of them can reach the file."""
    global _secrets
    if _secrets is None:
        names = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|SID|PHONE|NUMBER|CLIENT_ID", re.I)
        _secrets = {v for k, v in os.environ.items() if names.search(k) and len(v) >= 6}
    return _secrets


def redact(text, limit=300):
    text = str(text or "")
    for v in _credential_values():
        text = text.replace(v, "[redacted]")
    for pat, sub in _PATTERNS:
        text = pat.sub(sub, text)
    return text[:limit]


def _open():
    global _file
    if _file is None:
        folder = Path(config.DIAGNOSTICS_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        _file = open(path, "a", encoding="utf-8", buffering=1)
        log.info("diagnostics: writing to %s", path)
    return _file


def event(kind, **fields):
    """Record one event. Never raises: diagnostics must not break a conversation."""
    if not config.DIAGNOSTICS:
        return
    try:
        from room_agent import runtime as rt

        row = {"t": round(time.time(), 3), "clock": time.strftime("%H:%M:%S"), "kind": kind, "turn": rt.turn_no}
        for k, v in fields.items():
            row[k] = redact(v) if isinstance(v, str) else round(v, 3) if isinstance(v, float) else v
        with _lock:
            _open().write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        log.debug("diagnostics: event %s not written (%s)", kind, e)


def tool(result, seconds):
    """An action's outcome (actions/executor.py). Argument names only, never their values."""
    if not config.DIAGNOSTICS:
        return
    event("tool", name=result.capability, args=sorted((result.parameters or {}).keys()),
          success=bool(result.success), verified=bool(getattr(result, "verified", False)), seconds=float(seconds),
          result=str(result.message)[:160])
