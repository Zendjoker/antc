"""Memory clean-up: duplicates, moments stored as facts, junk summaries. Dry run first, nothing deleted.

    python main.py --memory-cleanup            shows what would change
    python main.py --memory-cleanup --apply    does it (memory.db is backed up first)

    duplicates   active facts in the same category that say the same thing (the local meaning model, cosine >= SIMILAR;
                 word overlap if the model isn't there). The most trusted one stays (their own words / explicit, then
                 the newest); the others become inactive with superseded_by pointing at it: hidden, not deleted.
    moments      "On the bed right now": inactive (the present isn't a lasting fact).
    summaries    "(Summary for memory): SKIP" junk rows are removed from the summaries table.
Undo: python main.py --memory-cleanup --revert (re-activates what this tool deactivated; the backup is also kept).
"""

import datetime
import re
import shutil

SIMILAR = 0.82
TRUST = {"explicit": 3, "user_statement": 3, "confirmation": 3, "learned": 1}
MARK = "cleanup"  # (recorded in superseded_by notes so --revert knows what it did)


def _similarity(texts):
    """Pairwise similarity matrix (meaning model if available, else word overlap)."""
    import numpy as np

    try:
        from room_agent.social import meaning

        meaning.ENABLED = True
        meaning.load(wait=True)
        if meaning.ready():
            v = meaning.embed(texts)
            return v @ v.T, "meaning"
    except Exception:
        pass
    words = [set(re.findall(r"\w{3,}", t.lower())) for t in texts]
    n = len(texts)
    m = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            m[i, j] = len(words[i] & words[j]) / max(len(words[i] | words[j]), 1)
    return m, "words"


def plan(db):
    """-> {"duplicates": [(keep_row, [dup_rows])], "moments": [rows], "skip_summaries": [(id, text)], "how": str}"""
    from room_agent.memory.writer import is_skip, is_transient

    cols = [r[1] for r in db.execute("PRAGMA table_info(memories)")]
    rows = [dict(zip(cols, r)) for r in db.execute("SELECT * FROM memories WHERE active=1 AND source != 'rejected'")]
    out = {"duplicates": [], "moments": [], "skip_summaries": [], "how": ""}
    out["moments"] = [r for r in rows if r["category"] not in ("profile",) and is_transient(r["content"], r["category"])]
    moment_ids = {r["id"] for r in out["moments"]}
    by_cat = {}
    for r in rows:
        if r["id"] not in moment_ids and r["category"] != "profile":
            by_cat.setdefault(r["category"], []).append(r)
    for cat, items in by_cat.items():
        if len(items) < 2:
            continue
        sim, out["how"] = _similarity([i["content"] for i in items])
        used = set()
        for i, a in enumerate(items):
            if a["id"] in used:
                continue
            group = [a] + [b for j, b in enumerate(items) if j != i and b["id"] not in used and sim[i, j] >= SIMILAR]
            if len(group) < 2:
                continue
            keep = max(group, key=lambda r: (TRUST.get(r["source"], 1), r["updated_at"] or "", r["id"]))
            dups = [r for r in group if r is not keep]
            used.update(r["id"] for r in group)
            out["duplicates"].append((keep, dups))
    scols = [r[1] for r in db.execute("PRAGMA table_info(summaries)")]
    for r in db.execute("SELECT * FROM summaries"):
        d = dict(zip(scols, r))
        if is_skip(d.get("summary", "")):
            out["skip_summaries"].append((d["id"], d["summary"]))
    return out


def describe(p):
    lines = [f"Similarity by: {p['how'] or 'n/a'}"]
    for keep, dups in p["duplicates"]:
        lines.append(f"  keep #{keep['id']} ({keep['source']}): {keep['content'][:90]}")
        lines += [f"     hide #{d['id']} ({d['source']}): {d['content'][:90]}" for d in dups]
    for r in p["moments"]:
        lines.append(f"  moment, hide #{r['id']}: {r['content'][:90]}")
    for sid, text in p["skip_summaries"]:
        lines.append(f"  junk summary, remove #{sid}: {text[:60]!r}")
    if len(lines) == 1:
        lines.append("  nothing to clean up")
    return "\n".join(lines)


def apply(db_path, p):
    import sqlite3

    backup = f"{db_path}.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(db_path, backup)
    db = sqlite3.connect(db_path)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with db:
        for keep, dups in p["duplicates"]:
            for d in dups:
                db.execute("UPDATE memories SET active=0, superseded_by=?, updated_at=? WHERE id=?", (keep["id"], now, d["id"]))
        for r in p["moments"]:
            db.execute("UPDATE memories SET active=0, superseded_by=-1, updated_at=? WHERE id=?", (now, r["id"]))
        for sid, _ in p["skip_summaries"]:
            db.execute("DELETE FROM summaries WHERE id=?", (sid,))
    db.close()
    ids = [d["id"] for _, ds in p["duplicates"] for d in ds] + [r["id"] for r in p["moments"]]
    _remember(db_path, ids)
    return backup


def _remember(db_path, ids):
    from room_agent.audio import voices

    voices.save_setting("memory_cleanup_hidden", sorted(set((voices.saved("memory_cleanup_hidden", []) or []) + ids)))


def revert(db_path):
    import sqlite3

    from room_agent.audio import voices

    ids = voices.saved("memory_cleanup_hidden", []) or []
    if not ids:
        return 0
    db = sqlite3.connect(db_path)
    with db:
        db.execute(f"UPDATE memories SET active=1, superseded_by=NULL WHERE id IN ({','.join('?' * len(ids))})", ids)
    db.close()
    voices.save_setting("memory_cleanup_hidden", [])
    return len(ids)
