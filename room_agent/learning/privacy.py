"""What the learning layer must never keep: passwords, tokens, keys, payment and ID numbers.

Preferences that look like secrets are refused outright; free text that's stored (requests in interaction records) has
anything secret-looking replaced with [redacted] first."""

import re

_SECRET_WORDS = re.compile(r"\b(password|passcode|passwd|pin( code)?|api[ _-]?key|secret|token|private key|seed phrase|"
                           r"recovery (code|phrase)|security (code|question)|cvv|cvc|login code|2fa|one[- ]time code|"
                           r"credit card|card number|bank account|routing number|iban|social security|ssn)\b", re.I)
_SECRET_VALUES = [
    re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_-]{12,}"),          # API keys (OpenAI/Stripe style)
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b(ghp|gho|github_pat|xox[abp]|AKIA)[A-Za-z0-9_-]{10,}"),  # GitHub / Slack / AWS
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),       # JWTs
    re.compile(r"\b[A-Za-z0-9+/_-]{32,}\b"),                          # long random-looking tokens
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                             # SSN
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),                  # IBAN
]
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn(digits):
    total, alt = 0, False
    for d in reversed(digits):
        n = int(d)
        if alt:
            n = n * 2 - 9 if n > 4 else n * 2
        total, alt = total + n, not alt
    return total % 10 == 0


def looks_secret(text):
    """True if this mentions or contains a secret (never learned)."""
    text = str(text or "")
    if _SECRET_WORDS.search(text) or any(rx.search(text) for rx in _SECRET_VALUES):
        return True
    return any(_luhn(re.sub(r"\D", "", m[0])) for m in _CARD.finditer(text))


def redact(text):
    """The text with secret-looking parts replaced (for records that keep what was said)."""
    text = str(text or "")
    for rx in _SECRET_VALUES:
        text = rx.sub("[redacted]", text)
    text = _CARD.sub(lambda m: "[redacted]" if _luhn(re.sub(r"\D", "", m[0])) else m[0], text)
    return re.sub(r"(?i)\b(password|passcode|pin|api[ _-]?key|token|secret|cvv|cvc)\b(\s*(is|:|=)?\s*)\S+",
                  r"\1\2[redacted]", text)
