"""Who may use Jarvis's local servers (the dashboard on 8765, the control link on CONTROL_PORT).

127.0.0.1 is shared by every program and every user account on this PC, and a request that names 127.0.0.1 as its Host
and sends X-Jarvis: 1 is easy for any local program to make. So both servers also require a secret:

    control.token   a random secret made on first use (CONTROL_TOKEN_FILE), readable only by this user account where
                    the system allows it (POSIX 0600; on Windows the Jarvis folder's own permissions apply)
    control link    every request carries it in the X-Jarvis-Token header (only UI/server.py talks to it)
    dashboard       the browser gets it once, from the link UI/server.py prints and opens, and keeps it as an HttpOnly,
                    SameSite=Strict cookie: every /api request needs that cookie (plus, for changes, the existing
                    X-Jarvis header + local Origin checks against other websites)
"""

import hmac
import os
import secrets
from pathlib import Path

HEADER = "X-Jarvis-Token"
COOKIE = "jarvis_session"
COOKIE_MAX_AGE = 30 * 24 * 3600


def path():
    from room_agent import config

    return Path(os.getenv("CONTROL_TOKEN_FILE", "").strip() or config.HERE / "control.token")


def token():
    """The secret (made the first time, then kept)."""
    p = path()
    exists = False
    try:
        t = p.read_text(encoding="utf-8").strip()
        if len(t) >= 32:
            return t
        exists = True  # (empty / damaged: replaced)
    except FileNotFoundError:
        pass
    t = secrets.token_urlsafe(32)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(t)
    try:
        if exists:
            os.replace(tmp, p)
        else:
            os.link(tmp, p)  # (atomic, and never replaces a token another process just made: both then use that one)
    except FileExistsError:
        pass
    except OSError:
        os.replace(tmp, p)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return p.read_text(encoding="utf-8").strip()


def valid(sent):
    sent = str(sent or "")
    try:
        return bool(sent) and hmac.compare_digest(sent.encode("utf-8"), token().encode("utf-8"))
    except (UnicodeError, OSError):
        return False
