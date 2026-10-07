"""Local storage for the learning layer: its own SQLite file (learning.db), separate from the conversation history,
memory.db (facts about the user), and the .env secrets. Nothing here is uploaded anywhere.

Every row belongs to a user profile, so two people using the same Jarvis never share preferences.

  preferences   one row per (user, key): the current value, where it came from (explicit / inferred), confidence,
                evidence count, whether it may be applied automatically, and the user's own words for "why".
  signals       evidence for or against a value (a correction, a repeated choice, an undo...), with a weight.
                Confidence is recomputed from these; they're the reason a preference exists.
  interactions  one structured record per meaningful turn (request, context, intent, plan, tool calls, results,
                verification, correction, outcome, latency). Bounded (RETAIN_* below). Shaped so it could one day
                become an evaluation / training set, but it never leaves this PC on its own.
"""

import json
import sqlite3
import threading
import time

RETAIN_ROWS, RETAIN_DAYS = 2000, 180  # interactions kept per user

SCHEMA = """
CREATE TABLE IF NOT EXISTS preferences (
    user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, kind TEXT NOT NULL,
    source TEXT NOT NULL,            -- explicit | inferred
    confidence REAL NOT NULL, evidence INTEGER NOT NULL DEFAULT 0,
    auto INTEGER NOT NULL DEFAULT 1, -- may be applied without being asked (0 after "don't do that automatically")
    because TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL, last_used_at REAL,
    PRIMARY KEY (user_id, key));
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
    signal TEXT NOT NULL, weight REAL NOT NULL, interaction_id INTEGER, note TEXT NOT NULL DEFAULT '', at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS signals_user_key ON signals (user_id, key);
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, at REAL NOT NULL, request TEXT NOT NULL,
    context TEXT NOT NULL, intent TEXT NOT NULL, entities TEXT NOT NULL, plan TEXT NOT NULL, outcome TEXT NOT NULL,
    correction_of INTEGER, correction TEXT, signals TEXT NOT NULL DEFAULT '[]', latency_ms INTEGER);
CREATE INDEX IF NOT EXISTS interactions_user ON interactions (user_id, at);
"""


class LearningStore:
    def __init__(self, path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def _q(self, sql, args=(), one=False):
        with self._lock:
            cur = self._db.execute(sql, args)
            rows = cur.fetchall()
            self._db.commit()
            return (rows[0] if rows else None) if one else rows

    # ----- preferences
    def preference(self, user, key):
        row = self._q("SELECT * FROM preferences WHERE user_id=? AND key=?", (user, key), one=True)
        return _pref(row) if row else None

    def preferences(self, user):
        return [_pref(r) for r in self._q("SELECT * FROM preferences WHERE user_id=? ORDER BY updated_at DESC", (user,))]

    def put_preference(self, user, key, value, kind, source, confidence, evidence, because="", auto=None):
        now = time.time()
        old = self.preference(user, key)
        auto = (old["auto"] if old else True) if auto is None else auto
        self._q("INSERT OR REPLACE INTO preferences VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (user, key, json.dumps(value), kind, source, confidence, evidence, int(bool(auto)),
                 because or (old["because"] if old else ""), old["created_at"] if old else now, now,
                 old["last_used_at"] if old else None))

    def set_auto(self, user, key, auto):
        self._q("UPDATE preferences SET auto=?, updated_at=? WHERE user_id=? AND key=?", (int(auto), time.time(), user, key))

    def touch(self, user, key):
        self._q("UPDATE preferences SET last_used_at=? WHERE user_id=? AND key=?", (time.time(), user, key))

    def delete_preference(self, user, key):
        """Forget it AND its evidence, so it isn't immediately learned back."""
        self._q("DELETE FROM preferences WHERE user_id=? AND key=?", (user, key))
        self._q("DELETE FROM signals WHERE user_id=? AND key=?", (user, key))

    # ----- signals
    def add_signal(self, user, key, value, signal, weight, interaction_id=None, note=""):
        self._q("INSERT INTO signals (user_id, key, value, signal, weight, interaction_id, note, at) VALUES (?,?,?,?,?,?,?,?)",
                (user, key, json.dumps(value), signal, weight, interaction_id, note, time.time()))

    def signals(self, user, key):
        return [{**dict(r), "value": json.loads(r["value"])}
                for r in self._q("SELECT * FROM signals WHERE user_id=? AND key=? ORDER BY at", (user, key))]

    # ----- interactions
    def add_interaction(self, user, record):
        cur_id = None
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO interactions (user_id, at, request, context, intent, entities, plan, outcome, correction_of, "
                "correction, signals, latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (user, record["at"], record["request"], json.dumps(record["context"]), json.dumps(record["intent"]),
                 json.dumps(record["entities"]), json.dumps(record["plan"]), record["outcome"],
                 record.get("correction_of"), json.dumps(record.get("correction")) if record.get("correction") else None,
                 json.dumps(record.get("signals", [])), record.get("latency_ms")))
            cur_id = cur.lastrowid
            self._db.execute("DELETE FROM interactions WHERE user_id=? AND (at < ? OR id NOT IN (SELECT id FROM interactions "
                             "WHERE user_id=? ORDER BY at DESC LIMIT ?))",
                             (user, time.time() - RETAIN_DAYS * 86400, user, RETAIN_ROWS))
            self._db.commit()
        return cur_id

    def update_interaction(self, interaction_id, **fields):
        for k, v in fields.items():
            self._q(f"UPDATE interactions SET {k}=? WHERE id=?", (json.dumps(v) if not isinstance(v, (str, int)) else v,
                                                                   interaction_id))

    def interactions(self, user, limit=50):
        out = []
        for r in self._q("SELECT * FROM interactions WHERE user_id=? ORDER BY at DESC LIMIT ?", (user, limit)):
            d = dict(r)
            for k in ("context", "intent", "entities", "plan", "signals"):
                d[k] = json.loads(d[k])
            d["correction"] = json.loads(d["correction"]) if d["correction"] else None
            out.append(d)
        return out

    def forget_user(self, user):
        for table in ("preferences", "signals", "interactions"):
            self._q(f"DELETE FROM {table} WHERE user_id=?", (user,))


def _pref(row):
    d = dict(row)
    d["value"] = json.loads(d["value"])
    d["auto"] = bool(d["auto"])
    return d
