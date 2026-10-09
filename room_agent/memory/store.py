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

from .text import concepts as _concepts, mentions, now as _now, norm as _norm, raw_words as _raw_words, terms as _terms

log = logging.getLogger("room-agent")

MAX_CONTENT = 300
RECENT_MESSAGES = 30  # word-for-word dialogue kept across restarts
FORGET_ALL = {"everything", "all", "all of it", "everything you know", "all my data", "everything about me"}
NOTHING_TO_LEARN = {"yeah", "yes", "yep", "no", "nope", "nah", "ok", "okay", "thanks", "thank you", "cool",
                    "nice", "sure", "alright", "right", "hmm", "mm", "lol", "haha", "bye", "good", "great"}
# Core facts: one value each (a new value supersedes the old one)
PROFILE_KEYS = {
    "name": "name", "address_as": "what to call them; never their real name",
    "home_location": "home city / location", "units": "preferred units (metric or imperial)",
    "timezone": "timezone", "birthday": "birthday", "job": "job", "school": "school", "partner": "partner",
    "pets": "pets",
}
# Who they are (the real name: bookings, emails, calls) is changed only by their own "my name is..." (override=True);
# how they like to be addressed ("call me boss") is address_as, a separate key.
IDENTITY_KEYS = {"name"}
FORMS_OF_ADDRESS = {"boss", "the boss", "big boss", "boss man", "sir", "chief", "captain", "cap", "king", "queen", "champ",
                    "champion", "legend", "master", "madam", "ma am", "maam", "my lord", "lord", "your majesty",
                    "your highness", "commander", "general", "big man", "buddy", "bro", "dude", "mate", "pal"}
CALL_ME = re.compile(r"\b(call me|you can call me|address me as|refer to me as|call me by|i'?d like to be called|"
                     r"i want to be called|i prefer to be called)\b", re.I)
NAME_IS = re.compile(r"\b(my name is|my name'?s|i'?m called|i am called|my real name|changed my name|"
                     r"spell(ed|s)? (it|my name))\b", re.I)
HOME_KEYS = ("home_location", "location", "city", "home", "lives_in")
OPEN_THREAD = re.compile(r"\bopen[- ]threads?\b|\bunresolved\b|\bnot done\b|\bstill (?:to do|needs?)\b|\bnext step\b", re.I)
CATEGORIES = ("profile", "person", "preference", "plan", "routine", "fact")


def clip(text, limit):
    """At most `limit` characters, cut after the last whole sentence that fits (else the last whole word), never
    mid-word."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit + 1]  # (one more character: is a sentence's full stop followed by a space?)
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s)", cut)]
    if ends and ends[-1] >= limit // 3:
        return cut[:ends[-1]]
    return cut[:max(cut[:limit - 2].rfind(" "), 1)].rstrip(" ,;:-") + "..."


def is_form_of_address(value):
    return _norm(value) in FORMS_OF_ADDRESS


def resolve_identity(rows):
    """Who they are vs. what they asked to be called, read from the 'name' / 'address_as' rows (dicts with id, key,
    value, active, superseded_by). Read-time only, nothing is written: a form of address ('boss') that superseded a
    real name ('Adam') under 'name' reads as name Adam, address_as boss.
    -> {"name", "address_as", "name_suspect", "real_row", "title_row"} (rows only when that repair applies)."""
    by_id = {r["id"]: r for r in rows}
    current = next((r for r in sorted(rows, key=lambda r: -r["id"]) if r["key"] == "name" and r["active"]), None)
    address = next((r for r in sorted(rows, key=lambda r: -r["id"]) if r["key"] == "address_as" and r["active"]), None)
    out = {"name": current["value"] if current else "", "address_as": address["value"] if address else "",
           "name_suspect": False, "real_row": None, "title_row": None}
    if not current or not is_form_of_address(current["value"]):
        return out
    seen, row = set(), current  # (walk back what it superseded: Adam -> boss, or Adam -> boss -> sir)
    while row is not None and row["id"] not in seen:
        seen.add(row["id"])
        row = next((r for r in by_id.values() if r["key"] == "name" and not r["active"]
                    and r.get("superseded_by") == row["id"]), None)
        if row is not None and not is_form_of_address(row["value"]):
            out.update(name=row["value"], address_as=out["address_as"] or current["value"], name_suspect=True,
                       real_row=row, title_row=current)
            break
    return out


# Words people use for core facts that don't appear in the stored text ("where do I live" -> home_location)
PROFILE_ALIASES = {
    "home_location": {"live", "where", "home", "city", "town", "address", "location", "from", "move", "moved", "weather"},
    "name": {"name"},
    "address_as": {"call", "called", "nickname", "address"},
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
        """Core facts, with the name read through identity() (a stored 'boss' that replaced 'Adam' reads correctly)."""
        out = {r["key"]: r["value"] for r in
               self._q("SELECT key, value FROM memories WHERE active=1 AND key IS NOT NULL ORDER BY id")}
        if "name" in out or "address_as" in out:
            who = self.identity()
            out.update({k: who[k] for k in ("name", "address_as") if who[k]})
        return out

    def identity(self):
        """-> {"name": their real name, "address_as": what they asked to be called, "name_suspect": the stored name
        is a form of address that replaced the real one}. Reads only: the table is never changed here."""
        rows = [dict(r) for r in self._q("SELECT id, key, value, source, active, superseded_by FROM memories "
                                         "WHERE key IN ('name', 'address_as')")]
        who = resolve_identity(rows)
        return {k: who[k] for k in ("name", "address_as", "name_suspect")}

    def get(self, key):
        rows = self._q("SELECT value FROM memories WHERE active=1 AND key=? ORDER BY id DESC LIMIT 1", (key,))
        return rows[0]["value"] if rows else ""

    def home_location(self):
        profile = self.profile() if self.available else {}
        return next((profile[k] for k in HOME_KEYS if profile.get(k)), "")

    def facts(self):
        return [dict(r) for r in self._q("SELECT * FROM memories WHERE active=1 AND key IS NULL ORDER BY id")]

    # how much a source is trusted when ranking (what they told it directly ranks above what was inferred)
    SOURCE_WEIGHT = {"you said it": 0.5, "user_statement": 0.3, "confirmation": 0.3, "tool": 0.3, "imported": 0.1,
                     "learned": 0.0, "inference": -0.2}

    CONCEPT_ONLY_MAX = 3  # facts that share only a topic (no word) with the request: at most this many

    def relevant(self, query, k=8, context=""):
        """Stored facts relevant to `query`, best first: shared meaningful words, then a shared topic ("get more
        clients" and "a side business building websites" are both about work: text.CONCEPTS), then the words of the
        recent conversation (`context`: a short follow-up keeps its topic), then how trustworthy the source is and how
        recent. Core (keyed) facts are handled separately and always known."""
        q, ctx = _terms(query), _terms(context) - _terms(query)
        if not q and not ctx:
            return []
        topics = _concepts(q) or _concepts(ctx)
        today = datetime.datetime.now()
        scored = []
        for f in self.facts():
            ft = _terms(f["content"] + " " + (f["category"] or ""))
            overlap, from_ctx, shared = len(q & ft), len(ctx & ft), topics & _concepts(ft)
            if not (overlap or from_ctx or shared):
                continue
            try:
                age_days = (today - datetime.datetime.strptime(f["updated_at"][:10], "%Y-%m-%d")).days
            except ValueError:
                age_days = 999
            score = (overlap + 0.5 * min(from_ctx, 2) + (0.6 if shared else 0.0)
                     + self.SOURCE_WEIGHT.get(f["source"], 0.0) + (0.2 if age_days <= 7 else 0.0))
            scored.append((score, f["updated_at"], not (overlap or from_ctx), f))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        out, topic_only = [], 0
        for _, _, only_topic, f in scored:
            if only_topic:
                topic_only += 1
                if topic_only > self.CONCEPT_ONLY_MAX:
                    continue
            out.append(f)
            if len(out) >= k:
                break
        return out

    def relevant_summaries(self, query, context="", n=3, scan=12, looking_back=False):
        """The past-conversation summaries worth knowing for this request (not just the newest): the ones that share
        words or a topic with it (or with the recent conversation), and - when they refer back ("continue the project",
        "what did we decide") - the ones with an open thread. The newest is always included. Oldest first."""
        sums = [s for s in self.summaries(scan) if "SKIP" not in s["summary"]]
        if not sums:
            return []
        q, ctx = _terms(query), _terms(context)
        topics = _concepts(q) or _concepts(ctx)
        scored = []
        for i, s in enumerate(sums):
            st = _terms(s["summary"])
            score = len(q & st) + 0.5 * min(len(ctx & st), 2) + (0.6 if topics & _concepts(st) else 0.0)
            if looking_back and OPEN_THREAD.search(s["summary"]):
                score += 1.5
            scored.append((score, i, s))
        picked = {i for score, i, _ in sorted(scored, key=lambda x: (x[0], x[1]), reverse=True)[:n - 1] if score >= 1}
        picked.add(len(sums) - 1)  # (the newest: what was just going on)
        return [s for i, s in enumerate(sums) if i in picked]

    # ---------- what must never come back, near-duplicates, contradictions, expiry ----------
    def reject(self, content, why="they said it isn't true"):
        """Keep a record that this is NOT true about them (a misheard request, a corrected fact), so it can't be learned
        again, e.g. through a later summary. Stored inactive: it never shows up as a memory."""
        content = str(content).strip()[:MAX_CONTENT]
        if content and not self.is_rejected(content):
            now = _now()
            with self._lock:
                self.db.execute("INSERT INTO memories(category, content, source, confidence, created_at, updated_at, active) "
                                "VALUES('fact', ?, 'rejected', 0, ?, ?, 0)", (content, now, now))

    def is_rejected(self, content):
        t = _terms(content)
        for r in self._q("SELECT content FROM memories WHERE source='rejected'"):
            r_t = _terms(r["content"])
            if _norm(r["content"]) == _norm(content) or (t and r_t and len(t & r_t) / len(t | r_t) >= 0.6):
                return True
        return False

    def near_duplicate(self, content):
        """An active fact that says the same thing in other words (word overlap >= 0.8), or None."""
        t = _terms(content)
        if not t:
            return None
        for f in self.facts():
            ft = _terms(f["content"])
            if ft and len(t & ft) / len(t | ft) >= 0.8:
                return f
        return None

    def supersede(self, old_ids, new_id):
        """Older facts replaced by a newer one (a changed preference): kept for history, no longer active."""
        ids = [int(i) for i in old_ids if int(i) != int(new_id)]
        if ids:
            with self._lock:
                self.db.execute(f"UPDATE memories SET active=0, superseded_by=?, updated_at=? WHERE id IN "
                                f"({','.join('?' * len(ids))})", [new_id, _now(), *ids])
        return ids

    _DATE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December|"
                       r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+(\d{1,2})(?:\s*[-\u2013]\s*(\d{1,2}))?,?\s+(\d{4})\b")

    def expire_plans(self, today=None):
        """Plans with a date that has passed ('Dentist on Oct 3, 2026') stop being active. -> how many expired."""
        today = (today or datetime.date.today())
        gone = []
        for f in self.facts():
            if f["category"] != "plan":
                continue
            m = self._DATE.search(f["content"])
            if not m:
                continue
            try:
                when = datetime.datetime.strptime(f"{m.group(1)[:3]} {m.group(3) or m.group(2)} {m.group(4)}", "%b %d %Y").date()
            except ValueError:
                continue
            if when < today:
                gone.append(f["id"])
        if gone:
            with self._lock:
                self.db.execute(f"UPDATE memories SET active=0, updated_at=? WHERE id IN ({','.join('?' * len(gone))})",
                                [_now(), *gone])
        return len(gone)

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
    def set_key(self, key, value, source="learned", confidence=1.0, override=False):
        """Set a core fact. A different existing value is superseded (kept for history, inactive), except: what they
        told it directly (source 'explicit') isn't replaced by anything learned in the background, and their real name
        (IDENTITY_KEYS) changes only with override=True (their own "my name is ..."). Returns (changed, previous_value);
        a kept value returns (False, previous). Raises MemoryError_ if it couldn't be stored."""
        key = re.sub(r"\W+", "_", str(key).strip().lower()).strip("_")
        value = str(value or "").strip()[:MAX_CONTENT]
        if not key:
            raise MemoryError_("empty key")
        with self._lock:
            old = self._q("SELECT id, value, source FROM memories WHERE active=1 AND key=?", (key,))
            previous = old[0]["value"] if old else ""
            if old and previous != value and not override:
                why = ("told directly" if any(r["source"] == "explicit" for r in old) and source != "explicit"
                       else "their real name" if key in IDENTITY_KEYS else "")
                if why:
                    log.info("memory: kept %s=%r (%s), not replaced by %r (%s)", key, previous, why, value, source)
                    return False, previous
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
        self._q("INSERT INTO summaries(created_at, summary) VALUES(?,?)", (_now(), clip(summary, 400)))
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
