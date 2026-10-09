"""UserModel: what Jarvis has learned about how ONE user likes things done. Separate from memory (facts about them),
the conversation, and the world state (what's going on right now).

Every preference has a kind (below), a value, a source and a confidence:
  explicit  they said so ("always put Spotify on monitor 2"): confidence 1, always wins over anything inferred
  inferred  learned from evidence (signals): used automatically only when confidence >= APPLY_CONFIDENCE and it's
            backed by at least APPLY_EVIDENCE signals, so one odd action never becomes a habit

When a decision needs a preference:  the current request  >  explicit rule  >  confident inferred  >  default.
"""

import math
import re
import time

APPLY_CONFIDENCE, APPLY_EVIDENCE = 0.75, 3
PRIOR = 2.0            # how much evidence it takes to become confident (6 corrections of weight 1.5 -> 0.82)
HALF_LIFE_DAYS = 90    # old evidence counts less
TENTATIVE = 0.4        # below this, an inferred preference isn't even mentioned


# kind -> (key prefix needs a subject?, value check, how to say it, words that make it relevant, behavior: always relevant)
def _int_range(lo, hi):
    def check(v):
        n = int(round(float(str(v).strip().rstrip("%"))))
        if not lo <= n <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
        return n
    return check


def _text(v):
    v = str(v).strip()
    if not v or len(v) > 300:
        raise ValueError("needs a short value")
    return v


def _choice(*options):
    def check(v):
        v = str(v).strip().lower()
        if v not in options:
            raise ValueError("must be one of " + ", ".join(options))
        return v
    return check


def _steps(v):
    """A routine: a list of {"action", "args"} (or a plain description to fill in later)."""
    if isinstance(v, list):
        out = [{"action": str(s.get("action")), "args": dict(s.get("args") or {})} for s in v if isinstance(s, dict) and s.get("action")]
        if out:
            return out
    return _text(v)


KINDS = {
    "volume_step": dict(subject=False, check=_int_range(1, 50), say=lambda s, v: f"'louder/quieter' means {v}%",
                        words=r"volume|louder|quieter|loud|quiet|turn (it|that) (up|down)|sound|softer"),
    "app_monitor": dict(subject=True, check=_text, say=lambda s, v: f"{s} goes on monitor {v}", words=None),
    "app_alias": dict(subject=True, check=_text, say=lambda s, v: f"'{s}' means {v}", words=None),
    "monitor_alias": dict(subject=True, check=_text, say=lambda s, v: f"'{s}' is monitor {v}", words=r"monitor|screen|display"),
    "routine": dict(subject=True, check=_steps,
                    say=lambda s, v: f"when they say '{s}': " + (", ".join(f"{x['action']} {' '.join(map(str, x['args'].values()))}".strip()
                                                                         for x in v) if isinstance(v, list) else v),
                    words=None),
    "response_style": dict(subject=False, check=_choice("minimal", "short", "normal", "detailed"),
                           say=lambda s, v: f"answers: {v}", words=None, behavior=True),  # (minimal: "Alright." after actions)
    "humor": dict(subject=True, check=_text, say=lambda s, v: f"humor{'' if s in ('', 'always') else ' when ' + s}: {v}",
                  words=None, behavior=True),
    "interruptions": dict(subject=False, check=_text, say=lambda s, v: f"interruptions: {v}", words=None, behavior=True),
    "wake_up": dict(subject=False, check=_text, say=lambda s, v: f"wake-ups: {v}", words=r"wake|alarm|morning|get up"),
    "confirmation": dict(subject=True, check=_choice("ask", "dont_ask"),
                         say=lambda s, v: f"{'ask before' if v == 'ask' else 'no need to ask before'} {s.replace('_', ' ')}",
                         words=None),
    "device": dict(subject=True, check=_text, say=lambda s, v: f"{s}: {v}", words=r"device|room|speaker|light|lamp|tv"),
    "other": dict(subject=True, check=_text, say=lambda s, v: f"{s}: {v}", words=None),
}


def key_for(kind, subject=""):
    subject = re.sub(r"\s+", " ", str(subject or "").strip().lower())
    return f"{kind}:{subject}" if KINDS[kind]["subject"] else kind


def split_key(key):
    kind, _, subject = key.partition(":")
    return kind, subject


def _decay(at, now):
    return 0.5 ** ((now - at) / (HALF_LIFE_DAYS * 86400))


def score(signals, now=None):
    """Signals -> (best value, confidence, evidence count). Positive weight supports its value; negative weight
    (an undo, "no, not that") counts against it. Everything, for or against, dilutes the winner."""
    now = now or time.time()
    pos, neg, counts = {}, {}, {}
    for s in signals:
        w = s["weight"] * _decay(s["at"], now)
        v = repr(s["value"])
        if w > 0:
            pos[v] = pos.get(v, 0.0) + w
            counts[v] = counts.get(v, 0) + 1
        else:
            neg[v] = neg.get(v, 0.0) - w
    net = {v: pos.get(v, 0.0) - neg.get(v, 0.0) for v in set(pos) | set(neg)}
    if not net:
        return None, 0.0, 0
    best = max(net, key=net.get)
    if net[best] <= 0:
        return None, 0.0, 0
    # Diluted by evidence for OTHER values (contradictions) and against this one; evidence against a value that
    # lost ("no, not Edge") doesn't make the winner ("Chrome") less certain.
    total = sum(pos.values()) + neg.get(best, 0.0)
    value = next(s["value"] for s in signals if repr(s["value"]) == best)
    return value, round(net[best] / (total + PRIOR), 3), counts.get(best, 0)


class UserModel:
    def __init__(self, store, user):
        self.store, self.user = store, user

    # ----- teaching (explicit)
    def teach(self, kind, value, subject="", because=""):
        """An explicit rule. -> the stored value (raises ValueError if it doesn't fit the kind)."""
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind}")
        value = KINDS[kind]["check"](value)
        key = key_for(kind, subject)
        self.store.put_preference(self.user, key, value, kind, "explicit", 1.0, 1, because=because, auto=True)
        return key, value

    # ----- learning (implicit)
    def observe(self, kind, subject, value, signal, weight, interaction_id=None, note=""):
        """Evidence for (weight > 0) or against (weight < 0) a value. Recomputes the inferred preference; an explicit
        rule is never changed by evidence (only by the user)."""
        try:
            value = KINDS[kind]["check"](value)
        except (ValueError, TypeError):
            return None
        key = key_for(kind, subject)
        self.store.add_signal(self.user, key, value, signal, weight, interaction_id, note)
        current = self.store.preference(self.user, key)
        if current and current["source"] == "explicit":
            return current
        best, conf, evidence = score(self.store.signals(self.user, key))
        if best is None:
            if current:
                self.store.put_preference(self.user, key, current["value"], kind, "inferred", 0.0, 0)
            return None
        self.store.put_preference(self.user, key, best, kind, "inferred", conf, evidence,
                                  because=f"learned from {evidence} time{'s' if evidence != 1 else ''} you did or corrected this")
        return self.store.preference(self.user, key)

    # ----- deciding
    def resolve(self, kind, subject="", current=None):
        """The value to use now -> {"value", "source", "confidence", "because", "key"} or None (use the default).
        `current`: what this very request says, which always wins."""
        key = key_for(kind, subject)
        if current is not None:
            return {"value": current, "source": "current request", "confidence": 1.0, "because": "you just said so", "key": key}
        p = self.store.preference(self.user, key)
        if not p:
            return None
        if p["source"] == "explicit" or (p["confidence"] >= APPLY_CONFIDENCE and p["evidence"] >= APPLY_EVIDENCE):
            return {**p, "key": key}
        return None

    def automatic(self, kind, subject=""):
        """Like resolve, but only if it may be applied without being asked."""
        d = self.resolve(kind, subject)
        return d if d and d.get("auto", True) else None

    # ----- inspecting / removing
    def all(self, include_tentative=False):
        out = []
        for p in self.store.preferences(self.user):
            if p["source"] == "inferred" and p["confidence"] < (TENTATIVE if include_tentative else APPLY_CONFIDENCE):
                continue
            out.append(p)
        return out

    def say(self, p):
        kind, subject = split_key(p["key"])
        text = KINDS[kind]["say"](subject, p["value"])
        how = "you told me" if p["source"] == "explicit" else (
            f"learned, {round(p['confidence'] * 100)}% sure from {p['evidence']} times" if p["confidence"] >= APPLY_CONFIDENCE
            else f"still guessing, {round(p['confidence'] * 100)}% sure")
        return f"{text} ({how}{'' if p['auto'] else '; not applied automatically'})"

    def find(self, words):
        """Preferences matching what they called it ("my Spotify monitor preference", "the volume thing")."""
        words = {w for w in re.findall(r"[a-z0-9]+", str(words).lower())
                 if w not in {"my", "the", "preference", "preferences", "that", "rule", "setting", "thing", "about", "for", "a", "of"}}
        hits = []
        for p in self.store.preferences(self.user):
            kind, subject = split_key(p["key"])
            text = f"{kind.replace('_', ' ')} {subject} {p['value']} " + KINDS[kind]["say"](subject, p["value"]).lower()
            text += " " + {"volume_step": "volume louder quieter step", "app_monitor": "monitor screen",
                           "response_style": "answers style short long verbose", "humor": "jokes roast humor"}.get(kind, "")
            score_ = sum(1 for w in words if w in text.lower())
            if score_:
                hits.append((score_, p))
        hits.sort(key=lambda x: -x[0])
        best = hits[0][0] if hits else 0
        return [p for s, p in hits if s == best]

    def forget(self, key):
        self.store.delete_preference(self.user, key)

    def set_auto(self, key, auto):
        self.store.set_auto(self.user, key, auto)

    # ----- what the model sees
    def relevant(self, text, active_app=None, limit=6):
        """Short lines about the preferences that matter for this request (never the whole profile): behavior
        preferences always; others only when the request mentions what they're about."""
        text = f" {str(text or '').lower()} "
        out = []
        for p in self.all():
            kind, subject = split_key(p["key"])
            spec = KINDS[kind]
            hit = spec.get("behavior")
            if not hit and subject:
                hit = re.search(rf"\b{re.escape(subject)}\b", text) is not None or (
                    active_app and subject == active_app.lower() and re.search(r"\b(it|that)\b", text))
            if not hit and spec.get("words"):
                hit = re.search(spec["words"], text) is not None
            if not hit and kind == "app_alias":
                hit = re.search(rf"\b{re.escape(str(p['value']).lower())}\b", text) is not None
            if hit:
                out.append(self.say(p))
        return out[:limit]
