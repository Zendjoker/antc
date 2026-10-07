"""Keep credentials out of every log line, whatever logger wrote it (ours, requests/urllib3, a library...).
Installed once (integrations/__init__.py) as a log-record factory, so it applies before any handler sees the text."""

import logging
import re

PATTERNS = [
    (re.compile(r"ya29\.[A-Za-z0-9_\-.]+"), "[access-token]"),                 # Google access tokens
    (re.compile(r"1//[A-Za-z0-9_\-]{20,}"), "[refresh-token]"),                # Google refresh tokens
    (re.compile(r"GOCSPX-[A-Za-z0-9_\-]+"), "[client-secret]"),                # Google client secrets
    (re.compile(r"\b4/[0-9A-Za-z_\-]{20,}"), "[auth-code]"),                    # Google authorization codes
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.=]+"), "Bearer [token]"),
    (re.compile(r"(?i)\b((?:access|refresh|id)_token|client_secret|code_verifier|code)(\"?'?\s*[:=]\s*\"?'?)([^\s\"'&,}]+)"),
     r"\1\2[redacted]"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]*"), "[jwt]"),
]


def scrub(text):
    text = str(text)
    for rx, repl in PATTERNS:
        text = rx.sub(repl, text)
    return text


_installed = False


def install():
    global _installed
    if _installed:
        return
    _installed = True
    old = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = old(*args, **kwargs)
        try:
            msg = record.getMessage()
            clean = scrub(msg)
            if clean != msg:
                record.msg, record.args = clean, ()
        except Exception:
            pass
        return record

    logging.setLogRecordFactory(factory)
