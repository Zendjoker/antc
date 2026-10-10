"""Strip credentials from text before it is shown or written to a launcher log."""

import re

_SK = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b")
_TWILIO = re.compile(r"\bAC[0-9a-fA-F]{32}\b")
_ASSIGNED = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|token|secret|"
    r"password|xi-api-key|authorization)\s*[=:]\s*)(\S+)"
)
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/=]{6,}")


def redact(text: str) -> str:
    if not text:
        return text
    text = _SK.sub("[redacted]", text)
    text = _TWILIO.sub("[redacted]", text)
    text = _BEARER.sub(r"\1[redacted]", text)
    text = _ASSIGNED.sub(r"\1[redacted]", text)
    return text
