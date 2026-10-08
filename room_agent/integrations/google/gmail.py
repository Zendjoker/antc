"""Gmail through the Google connection. Read/search needs gmail.readonly; drafts and sending need gmail.compose.

Returns small structured dicts (never whole mailboxes): lists are summaries, a message body is cut to a sane length,
attachments are metadata (plus a short excerpt for small text files). Every write is read back before it's reported.
"""

import base64
import datetime
import html
import re
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

from room_agent.integrations.base import IntegrationError, NotFound

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
BODY_LIMIT, THREAD_BODY_LIMIT, MAX_LIST = 2000, 700, 10


def _b64d(data):
    return base64.urlsafe_b64decode((data or "") + "=" * (-len(data or "") % 4))


def _text_of(payload):
    """The readable text of a message: text/plain if there is one, else text/html with the tags taken out."""
    plain, rich = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and not part.get("filename"):
            text = _b64d(data).decode("utf-8", "replace")
            (plain if mime == "text/plain" else rich if mime == "text/html" else []).append(text)
        for p in part.get("parts") or []:
            walk(p)

    walk(payload or {})
    if plain:
        return "\n".join(plain).strip()
    text = "\n".join(rich)
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", " ", text)).strip()


def _attachments(payload):
    out = []

    def walk(part):
        body = part.get("body") or {}
        if part.get("filename") and body.get("attachmentId"):
            out.append({"id": body["attachmentId"], "filename": part["filename"], "mime": part.get("mimeType", ""),
                        "size": body.get("size", 0)})
        for p in part.get("parts") or []:
            walk(p)

    walk(payload or {})
    return out


def _when(ms, tz=None):
    d = datetime.datetime.fromtimestamp(int(ms) / 1000, tz)
    return d.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def _day_bounds(word, tz=None):
    """'today' / 'yesterday' / YYYY-MM-DD -> (start epoch, end epoch) of that local day."""
    now = datetime.datetime.now(tz)
    word = str(word).strip().lower()
    if word in ("today", "yesterday"):
        day = now.date() - datetime.timedelta(days=1 if word == "yesterday" else 0)
    else:
        day = datetime.date.fromisoformat(word[:10])
    start = datetime.datetime.combine(day, datetime.time(), now.tzinfo)
    return int(start.timestamp()), int((start + datetime.timedelta(days=1)).timestamp())


def build_query(query="", sender="", to="", subject="", after="", before="", on="", unread=None, has_attachment=None,
                important=None, inbox=True):
    parts = [query.strip()] if query and query.strip() else []
    if sender:
        parts.append(f"from:({sender})")
    if to:
        parts.append(f"to:({to})")
    if subject:
        parts.append(f"subject:({subject})")
    if on:
        a, b = _day_bounds(on)
        parts += [f"after:{a}", f"before:{b}"]
    if after:
        parts.append(f"after:{_day_bounds(after)[0]}" if re.fullmatch(r"today|yesterday|\d{4}-\d{2}-\d{2}.*", str(after))
                     else f"newer_than:{after}")
    if before:
        parts.append(f"before:{_day_bounds(before)[0]}")
    if unread:
        parts.append("is:unread")
    if has_attachment:
        parts.append("has:attachment")
    if important:
        parts.append("is:important")
    if inbox and not query:
        parts.append("in:inbox")
    return " ".join(parts)


class GmailService:
    def __init__(self, provider, account=None):
        self.session = provider.session(account)
        self.account = self.session.account

    def _get(self, path, **params):
        return self.session.call("GET", f"{BASE}/{path}", params=params or None)

    # ----- reading
    def _summary(self, mid):
        m = self._get(f"messages/{mid}", format="metadata", metadataHeaders=["From", "To", "Subject", "Date"])
        h = {x["name"].lower(): x["value"] for x in (m.get("payload") or {}).get("headers", [])}
        name, addr = parseaddr(h.get("from", ""))
        labels = m.get("labelIds", [])
        return {"id": m["id"], "thread_id": m.get("threadId"), "from_name": name or addr, "from_email": addr.lower(),
                "to": h.get("to", ""), "subject": h.get("subject", "(no subject)"), "date": _when(m.get("internalDate", 0)),
                "snippet": html.unescape(m.get("snippet", ""))[:160], "unread": "UNREAD" in labels,
                "important": "IMPORTANT" in labels}

    def search(self, q, limit=5):
        limit = max(1, min(int(limit or 5), MAX_LIST))
        res = self._get("messages", q=q, maxResults=limit)
        return [self._summary(m["id"]) for m in res.get("messages", [])[:limit]]

    def get_message(self, mid, body_limit=BODY_LIMIT):
        m = self._get(f"messages/{mid}", format="full")
        return self._full(m, body_limit)

    def _full(self, m, body_limit):
        payload = m.get("payload") or {}
        h = {x["name"].lower(): x["value"] for x in payload.get("headers", [])}
        name, addr = parseaddr(h.get("from", ""))
        labels = m.get("labelIds", [])
        return {"id": m["id"], "thread_id": m.get("threadId"), "from_name": name or addr, "from_email": addr.lower(),
                "to": h.get("to", ""), "cc": h.get("cc", ""), "reply_to": h.get("reply-to", ""),
                "subject": h.get("subject", "(no subject)"), "date": _when(m.get("internalDate", 0)),
                "message_id": h.get("message-id", ""), "references": h.get("references", ""),
                "unread": "UNREAD" in labels, "body": _text_of(payload)[:body_limit],
                "attachments": _attachments(payload),
                "participants": sorted({a.lower() for _, a in getaddresses([h.get(k, "") for k in ("from", "to", "cc", "reply-to")]) if a})}

    def get_thread(self, tid):
        t = self._get(f"threads/{tid}", format="full")
        msgs = [self._full(m, THREAD_BODY_LIMIT) for m in t.get("messages", [])][-MAX_LIST:]
        return {"id": t.get("id", tid), "subject": msgs[0]["subject"] if msgs else "", "messages": msgs}

    def attachment_text(self, mid, att):
        """A short excerpt of a small text attachment (other kinds: metadata only)."""
        if not (att["mime"].startswith("text/") or att["filename"].lower().endswith((".txt", ".csv", ".md"))) or att["size"] > 50000:
            return None
        data = self._get(f"messages/{mid}/attachments/{att['id']}")
        return _b64d(data.get("data", "")).decode("utf-8", "replace")[:1500]

    # ----- drafts and sending (gmail.compose)
    def _mime(self, to, subject, body, in_reply_to="", references="", cc="", message_id="", marker=""):
        msg = EmailMessage()
        if message_id:
            msg["Message-ID"] = message_id  # (lets a caller find this exact draft again: find_draft)
        if marker:
            msg["X-Jarvis-Operation"] = marker  # (a second, custom identifier, in case Gmail rewrites the Message-ID)
        msg["To"] = to
        if cc:
            msg["Cc"] = cc
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = (references + " " + in_reply_to).strip()
        msg.set_content(body)
        return base64.urlsafe_b64encode(msg.as_bytes()).decode()

    def get_draft(self, did):
        d = self.session.call("GET", f"{BASE}/drafts/{did}", params={"format": "full"})
        m = self._full(d["message"], BODY_LIMIT)
        return {"id": d["id"], "message_id": d["message"]["id"], "thread_id": m["thread_id"], "to": m["to"], "cc": m["cc"],
                "subject": m["subject"], "body": m["body"]}

    def find_draft_by_message_id(self, message_id):
        """The id of the draft carrying this Message-ID, or None (a search: subject to Gmail's indexing delay)."""
        res = self._get("drafts", q=f"rfc822msgid:{message_id}", maxResults=5)
        drafts = res.get("drafts") or []
        return drafts[0]["id"] if drafts else None

    def find_draft(self, message_id="", marker="", scan_limit=300):
        """Look for a draft Jarvis created, two ways (for reconciling a creation whose answer was lost):
            1. a search by Message-ID (fast, but search results can lag behind new drafts)
            2. a scan of the drafts themselves (drafts.list + each message's headers), matching the X-Jarvis-Operation
               marker or the Message-ID - not dependent on the search index, and robust to a rewritten Message-ID
        -> {"found": draft id or None, "complete": True if every draft was scanned (so "not found" means absent),
            "scanned": n}"""
        if message_id:
            try:
                hit = self.find_draft_by_message_id(message_id)
            except Exception:  # noqa: BLE001 (the scan below decides)
                hit = None
            if hit:
                return {"found": hit, "complete": True, "scanned": 0, "how": "search"}
        token, scanned = None, 0
        want_mid = (message_id or "").strip("<>").lower()
        while scanned < scan_limit:
            params = {"maxResults": 100}
            if token:
                params["pageToken"] = token
            page = self._get("drafts", **params)
            for d in page.get("drafts") or []:
                scanned += 1
                mid = (d.get("message") or {}).get("id")
                if not mid:
                    continue
                m = self._get(f"messages/{mid}", format="metadata", metadataHeaders=["Message-ID", "X-Jarvis-Operation"])
                h = {x["name"].lower(): x["value"] for x in (m.get("payload") or {}).get("headers", [])}
                if (marker and h.get("x-jarvis-operation", "") == marker) or \
                        (want_mid and h.get("message-id", "").strip("<>").lower() == want_mid):
                    return {"found": d["id"], "complete": True, "scanned": scanned, "how": "scan"}
            token = page.get("nextPageToken")
            if not token:
                return {"found": None, "complete": True, "scanned": scanned, "how": "scan"}
        return {"found": None, "complete": False, "scanned": scanned, "how": "scan (stopped at the limit)"}

    def create_draft(self, to, subject, body, reply_to=None, message_id="", marker=""):
        """reply_to: the message being answered (same thread, proper reply headers). message_id: an explicit
        Message-ID header (so the draft can be found again if the answer to this request is lost)."""
        thread, irt, refs = None, "", ""
        if reply_to:
            to = to or reply_to["reply_to"] or f"{reply_to['from_name']} <{reply_to['from_email']}>"
            subject = subject or (reply_to["subject"] if reply_to["subject"].lower().startswith("re:") else "Re: " + reply_to["subject"])
            thread, irt, refs = reply_to["thread_id"], reply_to["message_id"], reply_to["references"]
        body_json = {"message": {"raw": self._mime(to, subject or "(no subject)", body, irt, refs, message_id=message_id,
                                                   marker=marker)}}
        if thread:
            body_json["message"]["threadId"] = thread
        d = self.session.call("POST", f"{BASE}/drafts", json_body=body_json, scope_hint="gmail.compose")
        draft = self.get_draft(d["id"])  # read it back
        if _norm(draft["body"]) != _norm(body) or (to and parseaddr(to)[1].lower() not in draft["to"].lower()):
            raise IntegrationError("Gmail", "draft didn't save as written")
        draft["reply_headers"] = {"in_reply_to": irt, "references": refs}
        return draft

    def update_draft(self, did, body=None, subject=None, to=None, reply_headers=None):
        cur = self.session.call("GET", f"{BASE}/drafts/{did}", params={"format": "full"})
        m = self._full(cur["message"], 100000)
        h = {x["name"].lower(): x["value"] for x in cur["message"]["payload"].get("headers", [])}
        new = {"to": to or m["to"], "subject": subject or m["subject"], "body": body if body is not None else m["body"]}
        raw = self._mime(new["to"], new["subject"], new["body"], h.get("in-reply-to", ""),
                         (h.get("references", "").replace(h.get("in-reply-to", ""), "")).strip(), m["cc"])
        msg = {"raw": raw}
        if m["thread_id"]:
            msg["threadId"] = m["thread_id"]
        self.session.call("PUT", f"{BASE}/drafts/{did}", json_body={"id": did, "message": msg}, scope_hint="gmail.compose")
        draft = self.get_draft(did)
        if _norm(draft["body"]) != _norm(new["body"]) or draft["subject"] != new["subject"]:
            raise IntegrationError("Gmail", "draft change didn't save")
        return draft

    def send_draft(self, did):
        sent = self.session.call("POST", f"{BASE}/drafts/send", json_body={"id": did}, scope_hint="gmail.compose")
        mid = sent.get("id")
        check = self._get(f"messages/{mid}", format="minimal") if mid else {}
        if "SENT" not in check.get("labelIds", []):
            raise IntegrationError("Gmail", "not confirmed as sent")
        return {"id": mid, "thread_id": check.get("threadId")}

    def delete_draft(self, did):
        self.session.call("DELETE", f"{BASE}/drafts/{did}", scope_hint="gmail.compose")
        try:
            self.session.call("GET", f"{BASE}/drafts/{did}")
        except NotFound:
            return True
        raise IntegrationError("Gmail", "draft still there")


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()
