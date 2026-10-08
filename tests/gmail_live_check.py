"""REAL Gmail check of the draft-reconciliation assumptions (NOT part of the automated suite; run by hand, once approved).

What it does, in YOUR connected Gmail account, and nothing else:
    1. creates ONE draft addressed to the account itself, with a unique Message-ID and X-Jarvis-Operation marker
       (never sent - there is no send call in this script)
    2. checks how Gmail stored the Message-ID (kept or rewritten)
    3. polls the rfc822msgid: search for up to --wait seconds (how long the search index lags) and runs the headers scan
    4. deletes that one draft (only the draft it created, by its id)
Prints timings and findings only - no message contents, no credentials.

    .venv\\Scripts\\python -m tests.gmail_live_check --yes [--wait 180]
"""

import sys
import time
import uuid


def main():
    if "--yes" not in sys.argv:
        print("This creates (and then deletes) one draft in your real Gmail. Re-run with --yes to do it.")
        return 2
    wait = int(sys.argv[sys.argv.index("--wait") + 1]) if "--wait" in sys.argv else 180
    from room_agent.integrations import provider
    from room_agent.integrations.google.gmail import GmailService

    g = GmailService(provider("google"))
    me = g._get("profile").get("emailAddress", "")
    if not me:
        print("couldn't read the account's own address; nothing created")
        return 1
    tag = uuid.uuid4().hex[:12]
    msgid, marker = f"<jarvis-livecheck-{tag}@jarvis.invalid>", f"livecheck-{tag}"
    t0 = time.time()
    d = g.create_draft(me, f"Jarvis reconciliation check {tag} (safe to delete)",
                       "Created by tests/gmail_live_check.py. Never sent; deleted automatically.",
                       message_id=msgid, marker=marker)
    did = d["id"]
    print(f"created draft {did} in {time.time() - t0:.1f}s (to the account itself; not sent)")
    try:
        full = g.session.call("GET", f"https://gmail.googleapis.com/gmail/v1/users/me/drafts/{did}",
                              params={"format": "metadata"})
        hs = {x["name"].lower(): x["value"] for x in (full["message"].get("payload") or {}).get("headers", [])}
        print(f"Message-ID kept as sent: {hs.get('message-id', '').strip() == msgid}  "
              f"(stored: {'same' if hs.get('message-id', '').strip() == msgid else 'REWRITTEN'})")
        print(f"X-Jarvis-Operation header kept: {hs.get('x-jarvis-operation', '') == marker}")
        found_at = None
        while time.time() - t0 < wait:
            if g.find_draft_by_message_id(msgid) == did:
                found_at = time.time() - t0
                break
            time.sleep(5)
        print(f"rfc822msgid: search finds the draft: {found_at is not None}"
              + (f" after {found_at:.0f}s" if found_at is not None else f" (not within {wait}s)"))
        r = g.find_draft(message_id="", marker=marker)
        print(f"headers scan (marker only) finds it: {r['found'] == did}  (scanned {r['scanned']}, complete {r['complete']})")
        print("RECONCILE_MIN_AGE_S is 600s; the observed search delay should be well under it.")
    finally:
        g.delete_draft(did)
        print(f"deleted draft {did}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
