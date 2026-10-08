"""SQLite storage for missions and the lead database (MISSIONS_DB, under MISSIONS_DIR).

Tables
    missions        one row per mission: kind, params, state, budget and spend, workspace
    steps           the mission's plan: one row per step (unique key per mission), its state, attempts, result, evidence
    events          a mission's log (progress, retries, errors), shown on the dashboard
    leads           businesses: the verified profile, website status and analysis, opportunity score, contact status,
                    notes; deduplicated by a stable key (provider + id), then by phone, then by name + address
    research        every research result for a lead (kind, data, source URLs, time): the history behind the profile
    projects        generated website projects (folder, stack, edit history)
    outreach        drafts (email / call notes / proposal) and their status
    approvals       external actions waiting for the user's decision, and the decision
    lead_missions   which missions touched which leads
    cache           small cached lookups (geocoding) with their time

Writes never silently overwrite a verified field with a different value: the new value is kept as research history and the
conflict is noted instead.
"""

import csv
import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("room-agent")

SCHEMA = """
CREATE TABLE IF NOT EXISTS missions(
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, params TEXT NOT NULL, state TEXT NOT NULL,
    created REAL NOT NULL, updated REAL NOT NULL, budget_usd REAL NOT NULL, spent_usd REAL NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0, requests INTEGER NOT NULL DEFAULT 0,
    workspace TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS steps(
    id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL, key TEXT NOT NULL, kind TEXT NOT NULL,
    title TEXT NOT NULL, args TEXT NOT NULL DEFAULT '{}', depends TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3, idempotent INTEGER NOT NULL DEFAULT 1,
    started REAL, ended REAL, result TEXT NOT NULL DEFAULT '{}', evidence TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '', cost_usd REAL NOT NULL DEFAULT 0, seq INTEGER NOT NULL,
    UNIQUE(mission_id, key));
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL, at REAL NOT NULL, level TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS leads(
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '', lat REAL, lon REAL,
    phone TEXT NOT NULL DEFAULT '', phone_norm TEXT NOT NULL DEFAULT '', website TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '', website_status TEXT NOT NULL DEFAULT 'unchecked', analysis TEXT NOT NULL DEFAULT '{}',
    score INTEGER, level TEXT NOT NULL DEFAULT '', reasons TEXT NOT NULL DEFAULT '[]', confidence REAL,
    missing TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'candidate', contact_status TEXT NOT NULL DEFAULT 'not contacted',
    notes TEXT NOT NULL DEFAULT '', sources TEXT NOT NULL DEFAULT '[]', extra TEXT NOT NULL DEFAULT '{}',
    created REAL NOT NULL, updated REAL NOT NULL, researched REAL);
CREATE INDEX IF NOT EXISTS leads_phone ON leads(phone_norm);
CREATE TABLE IF NOT EXISTS research(
    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL, mission_id TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL,
    at REAL NOT NULL, data TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS projects(
    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL, mission_id TEXT NOT NULL, slug TEXT NOT NULL,
    path TEXT NOT NULL, stack TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
    history TEXT NOT NULL DEFAULT '[]', UNIQUE(mission_id, slug));
CREATE TABLE IF NOT EXISTS outreach(
    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL, mission_id TEXT NOT NULL, kind TEXT NOT NULL,
    recipient TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL, status TEXT NOT NULL,
    created REAL NOT NULL, updated REAL NOT NULL, external_id TEXT NOT NULL DEFAULT '', problems TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS approvals(
    id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL, lead_id INTEGER, action TEXT NOT NULL,
    summary TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, decided REAL,
    decided_via TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS lead_missions(lead_id INTEGER NOT NULL, mission_id TEXT NOT NULL, PRIMARY KEY(lead_id, mission_id));
CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, value TEXT NOT NULL, at REAL NOT NULL);
"""

LEAD_FIELDS = ("name", "category", "address", "city", "lat", "lon", "phone", "website", "email", "website_status",
               "analysis", "score", "level", "reasons", "confidence", "missing", "status", "contact_status", "notes",
               "sources", "extra", "researched")
JSON_FIELDS = {"analysis", "reasons", "missing", "sources", "extra", "params", "args", "depends", "result", "payload",
               "history", "problems"}
# A verified field that already has a value is not replaced by a different one: the difference is recorded instead.
PROTECTED = ("name", "address", "phone", "website", "email")


def norm_phone(phone):
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]  # (North American numbers: +1 is the same line)
    return digits if len(digits) >= 7 else ""


def norm_text(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---------------------------------------------------------------- helpers
    def _rows(self, sql, args=()):
        with self._lock:
            return [self._decode(dict(r)) for r in self.db.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        rows = self._rows(sql, args)
        return rows[0] if rows else None

    def _exec(self, sql, args=()):
        with self._lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    @staticmethod
    def _decode(row):
        for k in list(row):
            if k in JSON_FIELDS and isinstance(row[k], str):
                try:
                    row[k] = json.loads(row[k])
                except ValueError:
                    pass
        return row

    @staticmethod
    def _encode(k, v):
        return json.dumps(v, default=str) if k in JSON_FIELDS and not isinstance(v, str) else v

    def _update(self, table, key_col, key, fields, allowed=None):
        fields = {k: v for k, v in fields.items() if allowed is None or k in allowed}
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE {table} SET {sets} WHERE {key_col}=?", [self._encode(k, v) for k, v in fields.items()] + [key])

    # ---------------------------------------------------------------- missions
    def create_mission(self, mid, kind, title, params, budget_usd, workspace):
        now = time.time()
        self._exec("INSERT INTO missions(id, kind, title, params, state, created, updated, budget_usd, workspace) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", (mid, kind, title, json.dumps(params), "planned", now, now, float(budget_usd),
                                                 str(workspace)))
        return self.mission(mid)

    def mission(self, mid):
        return self._one("SELECT * FROM missions WHERE id=?", (mid,))

    def missions(self, limit=20, states=None):
        if states:
            q = ",".join("?" * len(states))
            return self._rows(f"SELECT * FROM missions WHERE state IN ({q}) ORDER BY updated DESC LIMIT ?", (*states, limit))
        return self._rows("SELECT * FROM missions ORDER BY updated DESC LIMIT ?", (limit,))

    def update_mission(self, mid, **fields):
        fields["updated"] = time.time()
        self._update("missions", "id", mid, fields)

    def add_spend(self, mid, usd=0.0, tokens_in=0, tokens_out=0, requests=0):
        self._exec("UPDATE missions SET spent_usd=spent_usd+?, tokens_in=tokens_in+?, tokens_out=tokens_out+?, "
                   "requests=requests+?, updated=? WHERE id=?", (float(usd), int(tokens_in), int(tokens_out), int(requests),
                                                                 time.time(), mid))

    # ---------------------------------------------------------------- steps
    def add_step(self, mid, key, kind, title, args=None, depends=None, idempotent=True, max_attempts=3):
        """Insert a step if this mission doesn't have one with that key yet (planning twice is harmless)."""
        with self._lock:
            seq = (self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM steps WHERE mission_id=?", (mid,)).fetchone()[0]) + 1
            cur = self.db.execute("INSERT OR IGNORE INTO steps(mission_id, key, kind, title, args, depends, state, "
                                  "idempotent, max_attempts, seq) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                  (mid, key, kind, title, json.dumps(args or {}), json.dumps(depends or []), "pending",
                                   1 if idempotent else 0, int(max_attempts), seq))
            self.db.commit()
            return cur.rowcount == 1

    def steps(self, mid):
        return self._rows("SELECT * FROM steps WHERE mission_id=? ORDER BY seq", (mid,))

    def step(self, mid, key):
        return self._one("SELECT * FROM steps WHERE mission_id=? AND key=?", (mid, key))

    def update_step(self, sid, **fields):
        self._update("steps", "id", sid, fields)

    # ---------------------------------------------------------------- events
    def event(self, mid, text, level="info"):
        self._exec("INSERT INTO events(mission_id, at, level, text) VALUES(?,?,?,?)", (mid, time.time(), level, str(text)[:500]))

    def events(self, mid, limit=50):
        return list(reversed(self._rows("SELECT * FROM events WHERE mission_id=? ORDER BY id DESC LIMIT ?", (mid, limit))))

    # ---------------------------------------------------------------- leads
    def find_lead(self, key="", phone="", name="", address=""):
        """The existing lead for a business: by stable key, then the same phone, then the same name + address."""
        if key:
            hit = self._one("SELECT * FROM leads WHERE key=?", (key,))
            if hit:
                return hit
        p = norm_phone(phone)
        if p:
            hit = self._one("SELECT * FROM leads WHERE phone_norm=?", (p,))
            if hit:
                return hit
        if name and address:
            n, a = norm_text(name), norm_text(address)
            for row in self._rows("SELECT * FROM leads WHERE lower(name)=lower(?)", (name,)):
                if norm_text(row["name"]) == n and norm_text(row["address"]) == a:
                    return row
        return None

    def upsert_lead(self, profile, mission_id=""):
        """Add a business or merge into the same one already stored. -> (lead id, created?)"""
        now = time.time()
        existing = self.find_lead(profile.get("key", ""), profile.get("phone", ""), profile.get("name", ""),
                                  profile.get("address", ""))
        if existing is None:
            fields = {k: profile.get(k) for k in LEAD_FIELDS if profile.get(k) not in (None, "")}
            fields.update(key=profile["key"], name=profile["name"], phone_norm=norm_phone(profile.get("phone", "")),
                          created=now, updated=now)
            cols = ", ".join(fields)
            with self._lock:
                cur = self.db.execute(f"INSERT INTO leads({cols}) VALUES({','.join('?' * len(fields))})",
                                      [self._encode(k, v) for k, v in fields.items()])
                lead_id = cur.lastrowid
                self.db.commit()
            created = True
        else:
            lead_id, created = existing["id"], False
            merged, conflicts = {}, []
            for k in LEAD_FIELDS:
                new = profile.get(k)
                if new in (None, "", [], {}):
                    continue
                old = existing.get(k)
                if k == "sources":
                    merged[k] = sorted(set(old or []) | set(new))
                elif old in (None, "", [], {}):
                    merged[k] = new
                elif k in PROTECTED and str(old).strip().lower() != str(new).strip().lower():
                    conflicts.append({"field": k, "kept": old, "other": new, "source": profile.get("sources", [])[:1]})
            if merged:
                if "phone" in merged:
                    merged["phone_norm"] = norm_phone(merged["phone"])
                merged["updated"] = now
                self._update("leads", "id", lead_id, merged)
            if conflicts:
                self.add_research(lead_id, mission_id, "conflict", {"conflicts": conflicts}, profile.get("sources", []))
        if mission_id:
            self._exec("INSERT OR IGNORE INTO lead_missions(lead_id, mission_id) VALUES(?,?)", (lead_id, mission_id))
        return lead_id, created

    def lead(self, lead_id):
        return self._one("SELECT * FROM leads WHERE id=?", (lead_id,))

    def update_lead(self, lead_id, **fields):
        if "phone" in fields:
            fields["phone_norm"] = norm_phone(fields["phone"])
        fields["updated"] = time.time()
        self._update("leads", "id", lead_id, fields, allowed=set(LEAD_FIELDS) | {"phone_norm", "updated"})

    def leads(self, mission_id=None, min_score=None, level=None, status=None, query=None, limit=200):
        sql, args = "SELECT l.* FROM leads l", []
        where = []
        if mission_id:
            sql += " JOIN lead_missions m ON m.lead_id=l.id"
            where.append("m.mission_id=?")
            args.append(mission_id)
        if min_score is not None:
            where.append("l.score>=?")
            args.append(int(min_score))
        if level:
            where.append("lower(l.level)=lower(?)")
            args.append(level)
        if status:
            where.append("l.status=?")
            args.append(status)
        if query:
            where.append("(lower(l.name) LIKE ? OR lower(l.category) LIKE ? OR lower(l.address) LIKE ?)")
            args += [f"%{query.lower()}%"] * 3
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY COALESCE(l.score, -1) DESC, l.name LIMIT ?"
        args.append(int(limit))
        return self._rows(sql, args)

    def add_research(self, lead_id, mission_id, kind, data, sources=()):
        self._exec("INSERT INTO research(lead_id, mission_id, kind, at, data, sources) VALUES(?,?,?,?,?,?)",
                   (lead_id, mission_id or "", kind, time.time(), json.dumps(data, default=str), json.dumps(list(sources))))

    def research(self, lead_id):
        return self._rows("SELECT * FROM research WHERE lead_id=? ORDER BY at", (lead_id,))

    def export(self, path, mission_id=None, fmt="csv"):
        rows = self.leads(mission_id=mission_id, limit=100000)
        cols = ["id", "name", "category", "address", "city", "phone", "email", "website", "website_status", "score", "level",
                "status", "contact_status", "confidence", "reasons", "missing", "sources", "researched"]
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            path.write_text(json.dumps([{c: r.get(c) for c in cols} for r in rows], indent=1, default=str), encoding="utf-8")
        else:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(cols)
                for r in rows:
                    w.writerow([json.dumps(r.get(c)) if isinstance(r.get(c), (list, dict)) else r.get(c) for c in cols])
        return path, len(rows)

    # ---------------------------------------------------------------- projects, outreach, approvals
    def add_project(self, lead_id, mission_id, slug, path, stack):
        now = time.time()
        self._exec("INSERT INTO projects(lead_id, mission_id, slug, path, stack, status, created, updated) "
                   "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(mission_id, slug) DO UPDATE SET path=excluded.path, "
                   "stack=excluded.stack, status=excluded.status, updated=excluded.updated",
                   (lead_id, mission_id, slug, str(path), stack, "generated", now, now))
        return self._one("SELECT * FROM projects WHERE mission_id=? AND slug=?", (mission_id, slug))

    def projects(self, mission_id=None, lead_id=None):
        if lead_id is not None:
            return self._rows("SELECT * FROM projects WHERE lead_id=? ORDER BY updated DESC", (lead_id,))
        if mission_id:
            return self._rows("SELECT * FROM projects WHERE mission_id=? ORDER BY created", (mission_id,))
        return self._rows("SELECT * FROM projects ORDER BY updated DESC LIMIT 100")

    def project_history(self, project_id, entry):
        p = self._one("SELECT * FROM projects WHERE id=?", (project_id,))
        if p:
            hist = (p["history"] or []) + [{"at": time.time(), **entry}]
            self._update("projects", "id", project_id, {"history": hist[-50:], "updated": time.time()})

    def add_outreach(self, lead_id, mission_id, kind, body, subject="", recipient="", problems=()):
        now = time.time()
        cur = self._exec("INSERT INTO outreach(lead_id, mission_id, kind, recipient, subject, body, status, created, updated, "
                         "problems) VALUES(?,?,?,?,?,?,?,?,?,?)", (lead_id, mission_id, kind, recipient, subject, body,
                                                                    "draft", now, now, json.dumps(list(problems))))
        return cur.lastrowid

    def outreach(self, mission_id=None, lead_id=None):
        if lead_id is not None:
            return self._rows("SELECT * FROM outreach WHERE lead_id=? ORDER BY created", (lead_id,))
        return self._rows("SELECT * FROM outreach WHERE mission_id=? ORDER BY created", (mission_id,))

    def update_outreach(self, oid, **fields):
        fields["updated"] = time.time()
        self._update("outreach", "id", oid, fields)

    def add_approval(self, mission_id, lead_id, action, summary, payload):
        """An external action waiting for the user. The same pending action isn't queued twice."""
        same = self._one("SELECT * FROM approvals WHERE mission_id=? AND action=? AND payload=? AND status='pending'",
                         (mission_id, action, json.dumps(payload, sort_keys=True)))
        if same:
            return same["id"]
        cur = self._exec("INSERT INTO approvals(mission_id, lead_id, action, summary, payload, status, created) "
                         "VALUES(?,?,?,?,?,?,?)", (mission_id, lead_id, action, summary, json.dumps(payload, sort_keys=True),
                                                   "pending", time.time()))
        return cur.lastrowid

    def approval(self, aid):
        return self._one("SELECT * FROM approvals WHERE id=?", (aid,))

    def approvals(self, mission_id=None, status=None):
        sql, args = "SELECT * FROM approvals", []
        conds = []
        if mission_id:
            conds.append("mission_id=?")
            args.append(mission_id)
        if status:
            conds.append("status=?")
            args.append(status)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        return self._rows(sql + " ORDER BY created", args)

    def decide_approval(self, aid, status, via, result=""):
        self._exec("UPDATE approvals SET status=?, decided=?, decided_via=?, result=? WHERE id=? AND status='pending'",
                   (status, time.time(), via, str(result)[:500], aid))

    # ---------------------------------------------------------------- cache
    def cache_get(self, key, max_age_s):
        row = self._one("SELECT * FROM cache WHERE key=?", (key,))
        if row and time.time() - row["at"] < max_age_s:
            try:
                return json.loads(row["value"])
            except ValueError:
                return None
        return None

    def cache_put(self, key, value):
        self._exec("INSERT OR REPLACE INTO cache(key, value, at) VALUES(?,?,?)", (key, json.dumps(value, default=str), time.time()))


_store = {"s": None}
_store_lock = threading.Lock()


def store():
    from room_agent import config

    with _store_lock:
        if _store["s"] is None or _store["s"].path != Path(config.MISSIONS_DB):
            _store["s"] = Store(config.MISSIONS_DB)
        return _store["s"]
