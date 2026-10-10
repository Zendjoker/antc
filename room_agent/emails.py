"""Spoken email addresses -> written ones: "adam dot azzouz at gmail dot com" -> adam.azzouz@gmail.com.

Deterministic, no model. It never changes the transcript: it only finds address candidates in it, each with a
confidence, and the caller decides (accept / ask "is that right?" / ignore). Outside a moment where an address is
expected, only candidates that strongly resemble one count, so "I looked at google dot com" stays ordinary talk.
"""

import logging
import re

log = logging.getLogger("room-agent")

ACCEPT, CONFIRM = 0.85, 0.5  # >= ACCEPT: use it; >= CONFIRM: ask "I heard X, is that right?"; below: not an address

SPOKEN = {"at": "@", "dot": ".", "period": ".", "point": ".", "underscore": "_", "dash": "-", "hyphen": "-",
          "minus": "-"}
SEPARATORS = {".", "_", "-"}
PROVIDERS = {"gmail.com", "googlemail.com", "yahoo.com", "yahoo.fr", "outlook.com", "outlook.fr", "hotmail.com",
             "hotmail.fr", "live.com", "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me",
             "protonmail.com", "gmx.com", "gmx.net", "zoho.com", "yandex.com", "mail.com", "orange.fr", "free.fr",
             "laposte.net"}
BARE_PROVIDERS = {"gmail": "gmail.com", "hotmail": "hotmail.com", "yahoo": "yahoo.com", "outlook": "outlook.com",
                  "icloud": "icloud.com", "protonmail": "protonmail.com"}  # "adam at gmail": a guess, so always confirmed
TLDS = {"com", "net", "org", "io", "co", "edu", "gov", "me", "app", "dev", "info", "biz", "ai", "tech", "online",
        "test"}  # (+ any two-letter country code)
# Ordinary words that come before "at" in normal talk ("I looked at", "I'm at home"): never a confident local part
COMMON = set("""i me you we he she it they him her them us my your our this that these those there here what where
when who how is was are be been am being look looked looking stare staring good bad great better best home work
school least last first all once night noon one the a an and or but so just still also even only arrive arrived
meet meeting see seen laugh laughing point pointed aim aimed shoot shot""".split())
CUES = {"to", "email", "e-mail", "address", "is", "it's", "its", "mail", "send", "contact"}
WRITTEN = re.compile(r"(?<![\w.+-])([\w.+-]+@[\w-]+(?:\.[\w-]+)+)(?![\w-])")


def said_addresses(texts):
    """The exact addresses in what someone said: written ones whole, spoken ones only when confidently recognized. Never
    a fragment ('e@acme.co' isn't in 'joe@acme.com')."""
    out = set()
    for text in texts:
        out.update(a.lower().rstrip(".") for a in WRITTEN.findall(str(text or "")))
        out.update(c["email"].lower() for c in find(str(text or ""), expecting=True) if c["confidence"] >= ACCEPT)
    return out


def name_matches_address(name, addr):
    """Does a first name they said name this address? Only as a whole part of its local part: 'Sam' -> sam.jones@...,
    sam_k@..., never samsung-promo@... (a near miss means asking for the address, never guessing it)."""
    word = str(name or "").strip().split()[0].lower() if str(name or "").strip() else ""
    local = str(addr or "").split("@")[0].lower()
    return bool(word) and word in [p for p in re.split(r"[._+\-\d]+", local) if p]


def _tokens(text):
    """Lowercased words and symbols, with spoken symbols ("at", "dot"...) turned into the symbol, and whether each
    symbol was spoken (True) or written (False)."""
    out = []
    for tok in re.findall(r"[a-z0-9]+(?:'[a-z]+)?|[@._\-+]", str(text).lower()):
        if tok in SPOKEN:
            out.append((SPOKEN[tok], True))
        elif tok in ("@", ".", "_", "-"):
            out.append((tok, False))
        elif re.fullmatch(r"[a-z0-9]+", tok):
            out.append((tok, None))
        else:
            out.append((tok, "word"))  # (a contraction like "it's": a word, but never part of an address)
    return out


def _is_word(item):
    return item[1] is None


def find(text, expecting=False):
    """Email address candidates in `text`, best first: [{"email", "confidence", "spoken"}]. `expecting`: the
    conversation is waiting for an address right now (Jarvis asked for one, or a field that takes one is being filled)."""
    toks = _tokens(text)
    found = {}
    for i, (tok, how) in enumerate(toks):
        if tok != "@":
            continue
        # local part: word (sep word)* right before the "at"
        j = i - 1
        if j < 0 or not _is_word(toks[j]):
            continue
        local, spoken_bits, k = [toks[j][0]], [how], j - 1
        while k - 1 >= 0 and toks[k][0] in SEPARATORS and _is_word(toks[k - 1]):
            local[:0] = [toks[k - 1][0], toks[k][0]]
            spoken_bits.append(toks[k][1])
            k -= 2
        spelled = False
        if expecting and len(local) == 1 and len(local[0]) == 1:  # spelled out: "j o h n at ..."
            while k >= 0 and _is_word(toks[k]) and len(toks[k][0]) == 1:
                local.insert(0, toks[k][0])
                k -= 1
            spelled = len(local) > 1
        before = toks[k][0] if k >= 0 else ""
        # domain: word ((dot|dash) word)* right after
        m = i + 1
        if m >= len(toks) or not _is_word(toks[m]):
            continue
        dom, m = [toks[m][0]], m + 1
        while m + 1 < len(toks) and toks[m][0] in (".", "-") and _is_word(toks[m + 1]):
            dom += [toks[m][0], toks[m + 1][0]]
            spoken_bits.append(toks[m][1])
            m += 2
        local_s, domain = "".join(local), "".join(dom)
        guessed = False
        if "." not in domain:
            if not (expecting and domain in BARE_PROVIDERS):
                continue
            domain, guessed = BARE_PROVIDERS[domain], True
        tld = domain.rsplit(".", 1)[1]
        if not (tld in TLDS or re.fullmatch(r"[a-z]{2}", tld)):
            continue
        if not re.fullmatch(r"[a-z0-9]([a-z0-9._+-]*[a-z0-9])?", local_s) or ".." in local_s or ".." in domain:
            continue
        spoken = how is True or any(b is True for b in spoken_bits)
        if not spoken:
            conf = 0.95  # written out already ("adam@gmail.com")
        else:
            conf = 0.75 + (0.15 if domain in PROVIDERS else 0) + (0.1 if expecting else 0) + (0.05 if before in CUES else 0)
            if local_s in COMMON:
                conf -= 0.4
            if guessed:
                conf -= 0.25
            if spelled:
                conf -= 0.2  # (letters are easy to mishear: always read back)
        conf = round(max(0.0, min(conf, 0.97)), 2)
        email = f"{local_s}@{domain}"
        if conf > found.get(email, {"confidence": -1})["confidence"]:
            found[email] = {"email": email, "confidence": conf, "spoken": spoken}
    return sorted(found.values(), key=lambda c: -c["confidence"])


def strong(text):
    """Candidates that count even when no address was expected (the transcript strongly resembles one)."""
    return [c for c in find(text) if c["confidence"] >= ACCEPT]


def spoken_form(email):
    """How to say it back: 'adam dot azzouz at gmail dot com'."""
    return (email.replace("@", " at ").replace(".", " dot ").replace("_", " underscore ").replace("-", " dash ")
            .replace("  ", " ").strip())


def log_resolution(raw, candidate, resolution):
    """One line per decision about an address (the transcript is logged as heard, before any normalization)."""
    log.info("email: RAW TRANSCRIPT %r | NORMALIZED EMAIL CANDIDATE %s | CONFIDENCE %s | RESOLUTION %s", raw,
             candidate["email"] if candidate else "-", f"{candidate['confidence']:.2f}" if candidate else "-", resolution)
