"""ExperienceStore: what happened when Jarvis worked on a goal (experience.db, local only).

Separate from everything else on purpose:
    memory.db            facts about the user ("works on AimChart")
    learning.db          how they like things done (preferences, routines) + per-turn interaction records
    SocialState          how the conversation is going right now (memory only)
    env.beliefs          what's true in the world right now
    experience.db        THIS: goal -> what was observed, what was done, how it turned out (+ turn metrics)

An experience is evidence, never truth: retrieved for a similar new goal it's offered as "last time X turned out to
be the cause; check it first", and the current state is still observed before anything is assumed. Never stored:
secrets (redacted), raw prompts, model reasoning, personal data from private capabilities (email, calendar).
"""

import json
import sqlite3
import threading
import time

from room_agent.learning.privacy import redact
from room_agent.memory.text import terms

RETAIN = 500
SCHEMA = """
CREATE TABLE IF NOT EXISTS experiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, goal TEXT NOT NULL, level TEXT NOT NULL,
    context TEXT NOT NULL, observations TEXT NOT NULL, actions TEXT NOT NULL, outcome TEXT NOT NULL,
    success INTEGER NOT NULL, corrections INTEGER NOT NULL DEFAULT 0, replans INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER, terms TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turn_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, data TEXT NOT NULL);
"""


def _step(s, private):
    """One executed step, as kept: capability, argument names/values (redacted), outcome. Private -> no content."""
    if private:
        return {"capability": s.capability, "params": sorted(k for k in (s.parameters or {}) if k != "confidence"),
                "ok": s.success, "result": "[personal data]"}
    params = {k: redact(str(v))[:80] for k, v in (s.parameters or {}).items() if k != "confidence"}
    out = {"capability": s.capability, "params": params, "ok": s.success, "result": redact(s.message)[:160]}
    if getattr(s, "expected", None) is not None:
        out["expected"], out["observed"] = s.expected, s.observed
    return out


class ExperienceStore:
    def __init__(self, path):
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def record(self, goal, steps, is_private, duration_s, corrections=0, replans=0):
        """A finished (or abandoned) goal. `steps`: the ActionResults of its plan(s); `is_private(cap)` -> bool."""
        from room_agent.actions import core

        obs, acts = [], []
        for s in steps:
            cap = core.get(s.capability)
            item = _step(s, is_private(s.capability))
            (acts if cap is None or cap.changes_state else obs).append(item)
        text = f"{goal.objective or goal.user_request} {' '.join(goal.desired_state)}"
        row = (time.time(), redact(goal.objective or goal.user_request)[:200], goal.level,
               json.dumps({"request": redact(goal.user_request)[:200], "constraints": [c.text for c in goal.constraints],
                           "desired_state": goal.desired_state[:5], "reason": goal.reason[:160]}),
               json.dumps(obs[:12]), json.dumps(acts[:12]), goal.status, int(goal.status == "SATISFIED"), int(corrections),
               int(replans), int(duration_s * 1000), " ".join(sorted(terms(text))))
        with self._lock:
            cur = self.db.execute("INSERT INTO experiences (at, goal, level, context, observations, actions, outcome, success, "
                                  "corrections, replans, duration_ms, terms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
            self.db.execute("DELETE FROM experiences WHERE id NOT IN (SELECT id FROM experiences ORDER BY id DESC LIMIT ?)",
                            (RETAIN,))
            self.db.commit()
            return cur.lastrowid

    def similar(self, text, k=3):
        """Past experiences that share meaningful words with `text`, best first (a hint for planning, not a script)."""
        want = terms(text)
        if not want:
            return []
        with self._lock:
            rows = [dict(r) for r in self.db.execute("SELECT * FROM experiences ORDER BY id DESC LIMIT 200")]
        scored = []
        for r in rows:
            overlap = len(want & set(r["terms"].split()))
            if overlap >= 2 or (overlap and len(want) <= 2):
                scored.append((overlap, r["id"], r))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        out = []
        for _, _, r in scored[:k]:
            r["observations"], r["actions"], r["context"] = (json.loads(r["observations"]), json.loads(r["actions"]),
                                                             json.loads(r["context"]))
            out.append(r)
        return out

    def hint(self, r):
        """One line for the model: what was found and done last time, and how it ended."""
        days = max(0, int((time.time() - r["at"]) // 86400))
        found = "; ".join(f"{o['capability']}: {o['result'][:90]}" for o in r["observations"][:2])
        did = ", ".join(a["capability"] + ("" if a["ok"] else " (failed)") for a in r["actions"][:4])
        return (f"{'today' if not days else f'{days} days ago'}, goal {r['goal']!r} -> {r['outcome'].lower()}"
                + (f"; found {found}" if found else "") + (f"; did {did}" if did else ""))

    def log_turn(self, data):
        with self._lock:
            self.db.execute("INSERT INTO turn_metrics (at, data) VALUES (?, ?)", (time.time(), json.dumps(data)))
            self.db.execute("DELETE FROM turn_metrics WHERE id NOT IN (SELECT id FROM turn_metrics ORDER BY id DESC LIMIT 2000)")
            self.db.commit()

    def turns(self, limit=500):
        with self._lock:
            return [json.loads(r["data"]) for r in self.db.execute("SELECT data FROM turn_metrics ORDER BY id DESC LIMIT ?",
                                                                    (limit,))]
