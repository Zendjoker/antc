"""Connection state that isn't secret (connections.json): which accounts are connected, what each was granted, which
services are switched on, when access last worked, the last problem. Shared by the agent and the settings page.
Tokens are NEVER in here: they're in the vault (vault.py)."""

import json
import threading
import time

from room_agent.config import CONNECTIONS_FILE

_lock = threading.RLock()


def load():
    with _lock:
        try:
            data = json.loads(CONNECTIONS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError, OSError):
            return {}


def save(data):
    with _lock:
        tmp = CONNECTIONS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(CONNECTIONS_FILE)


def provider(pid):
    return load().get(pid, {"accounts": {}, "active": None})


def update_provider(pid, fn):
    with _lock:
        data = load()
        p = data.setdefault(pid, {"accounts": {}, "active": None})
        fn(p)
        save(data)
        return p


def set_account(pid, account, **fields):
    def apply(p):
        a = p["accounts"].setdefault(account, {"connected_at": time.time()})
        a.update(fields)
        if not p.get("active"):
            p["active"] = account
    return update_provider(pid, apply)


def remove_account(pid, account):
    def apply(p):
        p["accounts"].pop(account, None)
        if p.get("active") == account:
            p["active"] = next(iter(p["accounts"]), None)
    return update_provider(pid, apply)


def note(pid, account, ok, problem=""):
    """Last successful access / last problem (never error bodies or tokens: just a short code)."""
    if not account:
        return
    fields = {"last_success": time.time(), "last_error": ""} if ok else {"last_error": problem, "last_error_at": time.time()}
    def apply(p):
        if account in p["accounts"]:
            p["accounts"][account].update(fields)
    update_provider(pid, apply)
