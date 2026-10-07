"""
Long-term memory for the room agent: a real local database (SQLite, memory.db), not prompt text.

Two kinds of memory, kept separate:
  SESSION memory     the current conversation (the chat history kept by the conversation loop). Recent dialogue is also
                     saved here (table `dialogue`) so a restart doesn't cut a conversation off.
  PERSISTENT memory  table `memories`: what the agent knows about you across restarts. Every row has
                     content, category, created/updated timestamps, source (you said it / learned) and
                     confidence. Core facts have a `key` (home_location, name, units, ...) and are unique:
                     a correction ("I moved to Oakland") supersedes the old row (kept, inactive), and
                     "forget" deletes rows for real. Table `summaries` holds one or two lines per past
                     conversation.

Writes are committed and read back before the agent may say they happened. Nothing about memory is
taken from the LLM's word: the agent asks this module (context for each request, recall, remember, forget).
"""

import datetime
import json
import logging
import re
import sqlite3
import threading
from pathlib import Path

from .text import mentions, now as _now, norm as _norm, raw_words as _raw_words, terms as _terms

log = logging.getLogger("room-agent")

MAX_CONTENT = 300
RECENT_MESSAGES = 30  # word-for-word dialogue kept across restarts
FORGET_ALL = {"everything", "all", "all of it", "everything you know", "all my data", "everything about me"}
NOTHING_TO_LEARN = {"yeah", "yes", "yep", "no", "nope", "nah", "ok", "okay", "thanks", "thank you", "cool",
                    "nice", "sure", "alright", "right", "hmm", "mm", "lol", "haha", "bye", "good", "great"}
# Core facts: one value each (a new value supersedes the old one)
PROFILE_KEYS = {
    "name": "name", "home_location": "home city / location", "units": "preferred units (metric or imperial)",
    "timezone": "timezone", "birthday": "birthday", "job": "job", "school": "school", "partner": "partner",
    "pets": "pets",
}
HOME_KEYS = ("home_location", "location", "city", "home", "lives_in")
CATEGORIES = ("profile", "person", "preference", "plan", "routine", "fact")


# Words people use for core facts that don't appear in the stored text ("where do I live" -> home_location)
PROFILE_ALIASES = {
    "home_location": {"live", "where", "home", "city", "town", "address", "location", "from", "move", "moved", "weather"},
    "name": {"name", "call", "called"},
    "units": {"units", "celsius", "fahrenheit", "metric", "imperial", "temperature"},
    "birthday": {"birthday", "born", "age", "old"},
    "job": {"job", "work", "career", "profession"},
    "partner": {"partner", "girlfriend", "boyfriend", "wife", "husband", "spouse"},
    "pets": {"pet", "pets", "dog", "cat"},
}


class MemoryError_(Exception):
    pass


class Memory:
    """Thread-safe SQLite store."""

    def __init__(self, path, legacy_json=None, legacy_recent=None):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.generation = 0  # bumped by forget(): in-flight writer results from before are dropped
        self.error = None
        try:
            self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY,
                    category TEXT NOT NULL,
                    key TEXT,
                    content TEXT NOT NULL,
                    value TEXT,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    superseded_by INTEGER
                );
                CREATE INDEX IF NOT EXISTS memories_key ON memories(key, active);
                CREATE TABLE IF NOT EXISTS summaries (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, summary TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dialogue (id INTEGER PRIMARY KEY, role TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
            self._migrate(legacy_json, legacy_recent)
        except sqlite3.Error as e:
            self.error = str(e)
            self.db = None
            log.error("persistent memory unavailable: %s", e)

    @property
    def available(self):
        return self.db is not None

    def _q(self, sql, args=()):
        if not self.db:
            raise MemoryError_(self.error or "memory database unavailable")
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    def _migrate(self, legacy_json, legacy_recent):
        """One-time import of the older memory.json / conversation.json files."""
        for src, kind in ((legacy_json, "memory"), (legacy_recent, "dialogue")):
            if not src or not Path(src).exists():
                continue
            try:
                data = json.loads(Path(src).read_text(encoding="utf-8-sig"))
            except (ValueError, UnicodeDecodeError):
                continue
            if kind == "memory":
                if isinstance(data, list):
                    data = {"facts": data}
                if isinstance(data, dict):
                    for k, v in (data.get("profile") or {}).items():
                        if v:
                            self.set_key(k, v, source="imported")
                    for f in data.get("facts") or []:
                        text = f if isinstance(f, str) else (f or {}).get("fact")
                        if text:
                            self.add(text, category="fact", source="imported")
                    for s in data.get("summaries") or []:
                        if isinstance(s, dict) and s.get("summary"):
                            self._q("INSERT INTO summaries(created_at, summary) VALUES(?,?)",
                                    (s.get("date") or _now(), s["summary"]))
            elif isinstance(data, list):
                self.save_recent([m for m in data if isinstance(m, dict)])
            Path(src).replace(Path(src).with_suffix(".migrated.json"))
            log.info("moved %s into %s", Path(src).name, self.path.name)

    # ---------- reading ----------
    def count(self):
        return self._q("SELECT COUNT(*) FROM memories WHERE active=1")[0][0]

    def profile(self):
        return {r["key"]: r["value"] for r in
                self._q("SELECT key, value FROM memories WHERE active=1 AND key IS NOT NULL ORDER BY id")}

    def get(self, key):
        rows = self._q("SELECT value FROM memories WHERE active=1 AND key=? ORDER BY id DESC LIMIT 1", (key,))
        return rows[0]["value"] if rows else ""

    def home_location(self):
        profile = self.profile() if self.available else {}
        return next((profile[k] for k in HOME_KEYS if profile.get(k)), "")

    def facts(self):
        return [dict(r) for r in self._q("SELECT * FROM memories WHERE active=1 AND key IS NULL ORDER BY id")]

    def relevant(self, query, k=8):
        """Stored facts that share meaningful words with `query`, best first. Core (keyed) facts are
        handled separately and always known."""
        q = _terms(query)
        if not q:
            return []
        scored = []
        for f in self.facts():
            overlap = len(q & _terms(f["content"] + " " + (f["category"] or "")))
            if overlap:
                scored.append((overlap, f["updated_at"], f))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [f for _, _, f in scored[:k]]

    def recall(self, query="", limit=30):
        """Everything stored that matches `query` (all of it when empty): profile, facts, past conversations."""
        profile = self.profile()
        facts = self.facts()
        summaries = [dict(r) for r in self._q("SELECT * FROM summaries ORDER BY id DESC LIMIT 20")]
        if _norm(query) and _norm(query) not in ("everything", "all", "me", "about me"):
            q, words = _terms(query), _raw_words(query)
            profile = {k: v for k, v in profile.items()
                       if q & _terms(f"{k.replace('_', ' ')} {PROFILE_KEYS.get(k, '')} {v}") or words & PROFILE_ALIASES.get(k, set())}
            facts = [f for f in facts if q & _terms(f["content"])]
            summaries = [s for s in summaries if q & _terms(s["summary"])]
        return profile, facts[:limit], summaries[:5]

    def summaries(self, n=3):
        return [dict(r) for r in self._q("SELECT * FROM summaries ORDER BY id DESC LIMIT ?", (n,))][::-1]

    def snapshot(self):
        """For the background writer and --memory: a plain-dict view."""
        return {
            "profile": self.profile(),
            "facts": [{"fact": f["content"], "saved": f["updated_at"][:10], "id": f["id"], "category": f["category"],
                       "source": f["source"]} for f in self.facts()],
            "summaries": [{"date": s["created_at"], "summary": s["summary"]} for s in self.summaries(60)],
        }

    # ---------- writing (each returns a verified result) ----------
    def set_key(self, key, value, source="learned", confidence=1.0):
        """Set a core fact. A different existing value is superseded (kept for history, inactive).
        Returns (changed, previous_value). Raises MemoryError_ if it couldn't be stored."""
        key = re.sub(r"\W+", "_", str(key).strip().lower()).strip("_")
        value = str(value or "").strip()[:MAX_CONTENT]
        if not key:
            raise MemoryError_("empty key")
        with self._lock:
            old = self._q("SELECT id, value FROM memories WHERE active=1 AND key=?", (key,))
            previous = old[0]["value"] if old else ""
            if not value:  # delete
                self._q("DELETE FROM memories WHERE active=1 AND key=?", (key,))
                return bool(old), previous
            if previous == value:
                return False, previous
            now = _now()
            label = PROFILE_KEYS.get(key, key.replace("_", " "))
            cur = self.db.execute(
                "INSERT INTO memories(category, key, content, value, source, confidence, created_at, updated_at) "
                "VALUES('profile', ?, ?, ?, ?, ?, ?, ?)", (key, f"{label}: {value}", value, source, confidence, now, now))
            for r in old:
                self._q("UPDATE memories SET active=0, superseded_by=?, updated_at=? WHERE id=?", (cur.lastrowid, now, r["id"]))
            if self.get(key) != value:  # read back: only report what's really stored
                raise MemoryError_("write didn't stick")
            return True, previous

    def add(self, content, category="fact", source="learned", confidence=1.0):
        """Add a fact. Returns the new row id, or None if it was already stored. Raises MemoryError_."""
        content = str(content).strip()[:MAX_CONTENT]
        if len(content) < 3:
            raise MemoryError_("nothing to save")
        category = category if category in CATEGORIES else "fact"
        with self._lock:
            if any(_norm(f["content"]) == _norm(content) for f in self.facts()):
                return None
            now = _now()
            cur = self.db.execute(
                "INSERT INTO memories(category, content, source, confidence, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (category, content, source, confidence, now, now))
            if not self._q("SELECT 1 FROM memories WHERE id=? AND active=1", (cur.lastrowid,)):
                raise MemoryError_("write didn't stick")
            return cur.lastrowid

    def remove_ids(self, ids):
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        with self._lock:
            n = self.db.execute(f"DELETE FROM memories WHERE id IN ({','.join('?' * len(ids))})", ids).rowcount
            return n

    def forget(self, text):
        """Delete everything mentioning `text` as a whole word or phrase ("everything" wipes it all,
        including saved dialogue and summaries). Returns what was removed, as readable strings."""
        t = _norm(text)
        with self._lock:
            if t in FORGET_ALL:
                gone = [r["content"] for r in self._q("SELECT content FROM memories")]
                self._q("DELETE FROM memories")
                self._q("DELETE FROM summaries")
                self._q("DELETE FROM dialogue")
                self.generation += 1
                return gone
            if len(t) < 3:
                return []
            # "where I live" means the home city
            wanted = {t}
            if re.search(r"\b(where i live|my (home|address|city|location)|home location|where i m from|where i am)\b", t):
                wanted |= {"home location", "location", "city", "home"}
            rows = self._q("SELECT id, key, content, value FROM memories")
            doomed = [r for r in rows if any(mentions(f"{(r['key'] or '').replace('_', ' ')} {r['content']} {r['value'] or ''}", w)
                                             for w in wanted)]
            if doomed:
                self._q(f"DELETE FROM memories WHERE id IN ({','.join('?' * len(doomed))})", [r["id"] for r in doomed])
            sums = [r for r in self._q("SELECT id, summary FROM summaries") if any(mentions(r["summary"], w) for w in wanted)]
            if sums:
                self._q(f"DELETE FROM summaries WHERE id IN ({','.join('?' * len(sums))})", [r["id"] for r in sums])
            self.generation += 1  # always: in-flight work from before this forget is dropped, even if nothing matched
            # read back: nothing matching may remain
            left = [r for r in self._q("SELECT key, content, value FROM memories")
                    if any(mentions(f"{(r['key'] or '').replace('_', ' ')} {r['content']} {r['value'] or ''}", w) for w in wanted)]
            if left:
                raise MemoryError_("some of it is still stored")
            return [r["content"] for r in doomed] + [f"a past conversation summary" for _ in sums]

    def add_summary(self, summary):
        self._q("INSERT INTO summaries(created_at, summary) VALUES(?,?)", (_now(), summary.strip()[:400]))
        self._q("DELETE FROM summaries WHERE id NOT IN (SELECT id FROM summaries ORDER BY id DESC LIMIT 60)")

    # ---------- recent dialogue (survives restarts) ----------
    def save_recent(self, items):
        """Replace the saved dialogue with these [{"role", "text", "time"}] items (last RECENT_MESSAGES)."""
        items = [m for m in items if m.get("role") in ("user", "assistant") and isinstance(m.get("text"), str)]
        with self._lock:
            self._q("DELETE FROM dialogue")
            for m in items[-RECENT_MESSAGES:]:
                self._q("INSERT INTO dialogue(role, text, created_at) VALUES(?,?,?)", (m["role"], m["text"], m.get("time") or _now()))

    def load_recent(self, max_age_hours):
        """Saved dialogue from the last `max_age_hours`, starting on something you said."""
        if not self.available:
            return []
        cutoff = (datetime.datetime.now() - datetime.timedelta(hours=max_age_hours)).strftime("%Y-%m-%d %H:%M")
        fresh = [{"role": r["role"], "text": r["text"], "time": r["created_at"]} for r in
                 self._q("SELECT role, text, created_at FROM dialogue WHERE created_at >= ? ORDER BY id", (cutoff,))
                 if r["text"].strip()]
        while fresh and fresh[0]["role"] != "user":
            fresh.pop(0)
        return fresh
