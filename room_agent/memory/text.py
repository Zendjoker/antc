"""Text helpers for memory: normalising, whole-word matching and search terms."""

import datetime
import re

STOPWORDS = set("""a an the i me my mine you your we our is are was were be been am do does did to of in on at for
with about and or but so that this it its what whats who where when why how can could would will should
just like really tell know remember any anything something some thing things there here have has had not
dont don't im i'm youre you're""".split())


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def now_stamp():
    return now()


def norm(text):
    return " ".join(re.findall(r"\w+", str(text).lower()))


def mentions(text, term):
    """Whole-word match: forgetting "car" mustn't delete "career" or "healthcare"."""
    return re.search(r"\b" + re.escape(norm(term)) + r"\b", norm(text)) is not None


def stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def terms(text):
    words = (re.sub(r"'s$|'", "", w) for w in re.findall(r"[a-z0-9']+", str(text).lower()))
    return {stem(w) for w in words if w not in STOPWORDS and len(w) > 1}


# What a memory is ABOUT, beyond its exact words: "get more clients" is about the side business, "I miss my sister" about
# family. A small fixed vocabulary, stemmed like terms(): bounded and predictable (no model call, no embeddings).
CONCEPTS = {
    "work": "business busines client customer money income revenue sale sell market lead project side startup company "
            "website site web design freelance agency pitch profit earn work job career plan idea demo restaurant",
    "family": "sister brother mom mother dad father family parent cousin aunt uncle son daughter wife husband partner "
              "kid child grandma grandpa",
    "health": "doctor dentist appointment sick pain health hospital medicine therapy gym workout sleep tired",
    "money": "money rent bill pay budget cost expense afford debt salary price spend saving",
    "mood": "stress stressed anxious anxiety worried sad lonely upset happy mood overwhelmed struggling feel",
    "home": "house apartment home room door light lamp kitchen bedroom",
}
_CONCEPT_TERMS = {name: {stem(w) for w in words.split()} for name, words in CONCEPTS.items()}


def concepts(term_set):
    """The concepts a set of terms() touches."""
    return {name for name, ts in _CONCEPT_TERMS.items() if ts & term_set}


def raw_words(text):
    return {re.sub(r"'s$|'", "", w) for w in re.findall(r"[a-z0-9']+", str(text).lower())}
