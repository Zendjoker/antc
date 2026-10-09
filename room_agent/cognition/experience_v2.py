"""Operational experience from run_task plans, kept in experience.db next to the goal experiences (cognition/experience.py).

    procedures         a plan that was fully VERIFIED (every step COMPLETED with its check): the capability families of
                       the request, the tool sequence, the words of the goal (redacted), how often it worked / failed.
                       A procedure that later fails more often than it worked is disabled (conflict handling); entries
                       expire after EXPERIENCE_TTL_DAYS. Offered to the model as a hint ("last time this worked: ..."),
                       never as an instruction, never run by itself.
    failure_patterns   which recovery alternative worked (or didn't) for a tool + failure kind (actions/recovery.py
                       tries the one that worked first).
Stored: tool names, failure kinds, recovery labels, the user's own goal words (redacted). Never stored: page / file /
email content, tool results, arguments with personal data, anything from outside sources - so nothing a web page says
can become a "procedure" or a preference. Inspect and correct: list_task_knowledge / forget_task_knowledge.
"""

import json
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS procedures (
    id INTEGER PRIMARY KEY AUTOINCREMENT, signature TEXT NOT NULL UNIQUE, families TEXT NOT NULL, tools TEXT NOT NULL,
    goal TEXT NOT NULL, terms TEXT NOT NULL, successes INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
    last_ok REAL, last_fail REAL, created REAL NOT NULL, source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS failure_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, tool TEXT NOT NULL, kind TEXT NOT NULL, alternative TEXT NOT NULL,
    successes INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL,
    UNIQUE(tool, kind, alternative));
"""
_ready = {"db": None}


def _db():
    from room_agent import cognition

    st = cognition.store()
    if _ready["db"] is not st.db:
        with st._lock:
            st.db.executescript(SCHEMA)
            st.db.commit()
        _ready["db"] = st.db
    return st


def _ttl():
    from room_agent import config

    return getattr(config, "EXPERIENCE_TTL_DAYS", 90) * 86400


def record_task(t):
    """A finished plan: a fully verified one strengthens its procedure; a failed one weakens a matching procedure."""
    from room_agent.cognition import understand
    from room_agent.learning.privacy import redact
    from room_agent.memory.text import terms

    tools = [s["tool"] for s in t["steps"] if s["state"] != "SKIPPED"]
    if not tools:
        return
    words = t.get("intent") or t.get("goal") or ""
    fams = sorted(understand.from_words(words).families)
    sig = json.dumps([fams, tools])
    st = _db()
    verified = t["state"] == "COMPLETED" and all(s["state"] in ("COMPLETED", "SKIPPED") for s in t["steps"]) and \
        all(s.get("evidence") for s in t["steps"] if s["state"] == "COMPLETED")
    now = time.time()
    with st._lock:
        row = st.db.execute("SELECT id FROM procedures WHERE signature=?", (sig,)).fetchone()
        if verified:
            if row:
                st.db.execute("UPDATE procedures SET successes=successes+1, last_ok=? WHERE id=?", (now, row[0]))
            else:
                st.db.execute("INSERT INTO procedures (signature, families, tools, goal, terms, successes, last_ok, created, "
                              "source) VALUES (?,?,?,?,?,1,?,?,?)",
                              (sig, json.dumps(fams), json.dumps(tools), redact(words)[:160], " ".join(sorted(terms(words))),
                               now, now, f"verified task {t['id']}"))
        elif row:
            st.db.execute("UPDATE procedures SET failures=failures+1, last_fail=? WHERE id=?", (now, row[0]))
        st.db.commit()


def relevant(text, k=2):
    """Verified procedures for a similar request (shared words, same families), not expired, not contradicted."""
    from room_agent.cognition import understand
    from room_agent.memory.text import terms

    want, fams = terms(text or ""), set(understand.from_words(text or "").families)
    if not want:
        return []
    st = _db()
    with st._lock:
        rows = [dict(r) for r in st.db.execute("SELECT * FROM procedures WHERE successes > failures AND last_ok > ? "
                                               "ORDER BY last_ok DESC LIMIT 200", (time.time() - _ttl(),))]
    scored = []
    for r in rows:
        overlap = len(want & set(r["terms"].split()))
        if overlap >= 2 and (not fams or fams & set(json.loads(r["families"]))):
            scored.append((overlap, r["successes"], r))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [r for _, _, r in scored[:k]]


def hint(r):
    return (f"a similar request worked {r['successes']}x (verified) with: {' -> '.join(json.loads(r['tools']))}"
            + (f"; failed {r['failures']}x" if r["failures"] else "") + " (a hint, not a script)")


def record_pattern(tool, kind, alternative, ok):
    st = _db()
    with st._lock:
        st.db.execute("INSERT INTO failure_patterns (tool, kind, alternative, successes, failures, updated) "
                      "VALUES (?,?,?,?,?,?) ON CONFLICT(tool, kind, alternative) DO UPDATE SET "
                      "successes=successes+excluded.successes, failures=failures+excluded.failures, updated=excluded.updated",
                      (tool, kind, alternative, int(bool(ok)), int(not ok), time.time()))
        st.db.commit()


def pattern_score(tool, kind, alternative):
    st = _db()
    with st._lock:
        row = st.db.execute("SELECT successes, failures FROM failure_patterns WHERE tool=? AND kind=? AND alternative=? "
                            "AND updated > ?", (tool, kind, alternative, time.time() - _ttl())).fetchone()
    return (row[0] - row[1]) if row else 0


def listing():
    st = _db()
    with st._lock:
        procs = [dict(r) for r in st.db.execute("SELECT * FROM procedures ORDER BY last_ok DESC LIMIT 50")]
        pats = [dict(r) for r in st.db.execute("SELECT * FROM failure_patterns ORDER BY updated DESC LIMIT 50")]
    return procs, pats


def forget(which="all"):
    """which: a procedure id, "patterns", or "all". -> how many entries were removed."""
    st = _db()
    with st._lock:
        if which == "all":
            n = st.db.execute("DELETE FROM procedures").rowcount + st.db.execute("DELETE FROM failure_patterns").rowcount
        elif which == "patterns":
            n = st.db.execute("DELETE FROM failure_patterns").rowcount
        else:
            n = st.db.execute("DELETE FROM procedures WHERE id=?", (int(which),)).rowcount
        st.db.commit()
    return n
