"""SocialState: how the conversation is going NOW. In memory only, never saved, never sent to long-term memory or
learning. Built from evidence (signals.py, prosody.py) that decays: recent strong evidence counts most, old weak
evidence fades (someone frustrated 20 minutes ago isn't treated as frustrated now), and a clear current signal
overrides what was inferred before ("haha nice, that fixed it" ends the frustration).
"""

import math
import time

HALF_LIFE = {"frustration": 300, "annoyed": 300, "low": 900, "positive": 240, "excited": 180, "energy_up": 240,
             "energy_down": 900, "joking": 120, "serious": 600, "urgent": 180, "brief": 600, "talk": 300, "dismiss": 60,
             "stop": 45}
KEEP_S = 1800      # evidence older than this is dropped
TURN_HALF = 1.0    # ...and it also halves with every turn that doesn't renew it: a mood is about the moment it was
                   # expressed ("I'm just exhausted"), not the next topics (a plan, the gym, dinner)
TURN_ONLY = ("dismiss", "stop")  # "whatever" / "enough" are about the reply to THAT utterance, never the next ones
HISTORY = 8        # recent exchanges kept for conversational context


def _level(v, mid=0.3, high=0.6):
    return "high" if v >= high else "medium" if v >= mid else "low"


class SocialState:
    def __init__(self):
        self.evidence = []      # signals.Evidence, oldest first
        self.cleared = {}       # dim -> update number: evidence from earlier updates no longer counts (overridden)
        self.seq = 0
        self.history = []       # [{"at", "user", "reply", "failed", "interrupted", "question", "name_used"}]
        self.turns = 0

    # ----- evidence
    def add(self, items):
        self.seq += 1
        for dim in TURN_ONLY:
            self.cleared[dim] = self.seq
        for e in items:
            e.seq = self.seq
            for dim in e.clears:
                self.cleared[dim] = self.seq  # (evidence from earlier updates is overridden; this update's still counts)
        for e in items:
            # The same observation re-derived from history ("cut Jarvis off" while that turn is still recent, "repeated
            # the request") is not new, independent evidence: keep the original (it keeps decaying) instead of adding a
            # fresh copy every turn, which used to pin confidence at 1.00.
            same = next((o for o in self.evidence if o.dim == e.dim and o.source == e.source and o.why == e.why
                         and o.seq >= self.cleared.get(e.dim, 0) and e.at - o.at < HALF_LIFE.get(e.dim, 300)), None)
            if same is None:
                self.evidence.append(e)
            else:
                same.seq = e.seq  # (seen again this turn: it doesn't fade by turns, only by time)
                if e.weight > same.weight:
                    same.weight = e.weight  # (stronger now: e.g. it failed again) but still dated from the first time
        cutoff = time.time() - KEEP_S
        self.evidence = [e for e in self.evidence if e.at >= cutoff]

    def value(self, dim, now=None):
        """0..1: the strongest decayed evidence from each source, combined across sources (noisy-or): several cues of
        the same kind from one source don't add up as if they were independent."""
        now = now or time.time()
        best = {}
        for e in self.evidence:
            if e.dim != dim or e.seq < self.cleared.get(dim, 0):
                continue
            w = e.weight * math.pow(0.5, max(0.0, now - e.at) / HALF_LIFE.get(dim, 300) + (self.seq - e.seq) / TURN_HALF)
            best[e.source] = max(best.get(e.source, 0.0), min(0.95, w))
        miss = 1.0
        for w in best.values():
            miss *= 1 - w
        return 1 - miss

    def sources(self, dim, now=None, within=600):
        now = now or time.time()
        return {e.source for e in self.evidence if e.dim == dim and now - e.at < within and e.seq >= self.cleared.get(dim, 0)}

    # ----- the fields asked for (derived, not stored)
    def snapshot(self, now=None):
        now = now or time.time()
        v = {d: round(self.value(d, now), 2) for d in HALF_LIFE}
        known = self.turns > 0
        frustr = max(v["frustration"], v["annoyed"])
        candidates = {"frustrated": frustr, "excited": v["excited"], "positive": v["positive"], "low": v["low"]}
        mood, strength = max(candidates.items(), key=lambda kv: kv[1])
        mood = mood if strength >= 0.3 else ("neutral" if known else "unknown")
        e = v["energy_up"] + 0.6 * v["excited"] - v["energy_down"] - 0.5 * v["low"]
        energy = "unknown" if not known else "high" if e >= 0.3 else "low" if e <= -0.3 else "normal"
        if v["urgent"] >= 0.45:
            mode = "urgent"
        elif v["serious"] >= 0.45 or (v["low"] >= 0.45 and v["talk"] >= 0.3):
            mode = "emotional"
        elif frustr >= 0.4:
            mode = "focused"
        elif v["joking"] >= 0.4:
            mode = "joking"
        elif v["talk"] >= 0.35 or v["positive"] >= 0.35 or v["excited"] >= 0.35 or v["low"] >= 0.35:
            mode = "casual"
        else:
            mode = "task"
        top = max(v.values()) if v else 0.0
        srcs = set().union(*(self.sources(d, now) for d, x in v.items() if x >= 0.3)) if top >= 0.3 else set()
        confidence = round(min(0.9, top * (0.6 + 0.15 * len(srcs))), 2) if known else 0.0  # (inferred: never certain)
        return {"mood_signal": mood, "energy": energy, "frustration": _level(frustr), "seriousness": _level(v["serious"]),
                "urgency": _level(v["urgent"]), "interaction_mode": mode, "confidence": confidence, "values": v,
                "sources": sorted(srcs)}

    # ----- recent exchanges (conversational context, kept only for this session)
    def note_turn(self, **fields):
        self.history = (self.history + [{"at": time.time(), **fields}])[-HISTORY:]

    def update_last(self, **fields):
        if self.history:
            self.history[-1].update(fields)

    def reset(self):
        self.__init__()
