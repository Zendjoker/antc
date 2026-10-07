"""A simulated Google for the integration tests: the OAuth server (with PKCE / state / redirect / scope / revocation
checks), a browser that really calls Jarvis's loopback redirect, and Gmail + Calendar APIs with in-memory data.
Knobs make it misbehave: expire tokens, revoke grants, rate-limit, go down, fail a send, silently drop a change."""

import base64
import datetime
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
from email import message_from_bytes
from email.utils import parseaddr

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3"
SCOPE_READ = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
SCOPE_CAL_READ = "https://www.googleapis.com/auth/calendar.readonly"
SCOPE_CAL_EVENTS = "https://www.googleapis.com/auth/calendar.events"


class Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def b64(s):
    return base64.urlsafe_b64encode(s.encode() if isinstance(s, str) else s).decode().rstrip("=")


class Mailbox:
    def __init__(self, owner):
        self.owner = owner
        self.messages = {}   # id -> message resource
        self.drafts = {}     # id -> {"id", "message": {...}}
        self.seq = 100

    def add(self, sender, subject, body, when, labels=("INBOX",), thread=None, to=None, attachments=(), msgid=None,
            unread=False):
        self.seq += 1
        mid = f"m{self.seq}"
        headers = [{"name": "From", "value": sender}, {"name": "To", "value": to or self.owner},
                   {"name": "Subject", "value": subject}, {"name": "Message-ID", "value": msgid or f"<{mid}@mail.test>"},
                   {"name": "Date", "value": when.isoformat()}]
        parts = [{"mimeType": "text/plain", "body": {"data": b64(body), "size": len(body)}}]
        for i, (name, mime, content) in enumerate(attachments):
            parts.append({"mimeType": mime, "filename": name,
                          "body": {"attachmentId": f"a{self.seq}{i}", "size": len(content)}, "_content": content})
        labels = list(labels) + (["UNREAD"] if unread else [])
        self.messages[mid] = {"id": mid, "threadId": thread or f"t{self.seq}", "labelIds": labels,
                              "snippet": body[:100], "internalDate": str(int(when.timestamp() * 1000)),
                              "payload": {"mimeType": "multipart/mixed", "headers": headers, "parts": parts}}
        return mid


class FakeGoogle:
    def __init__(self, client_id="test-client.apps.googleusercontent.com", client_secret="GOCSPX-test-secret-123"):
        self.client_id, self.client_secret = client_id, client_secret
        self.codes = {}          # code -> {challenge, redirect, scopes, account}
        self.refresh = {}        # refresh token -> {account, scopes, revoked}
        self.access = {}         # access token -> {account, scopes, exp}
        self.issued = []         # every token/code ever handed out (for the leak checks)
        self.mailboxes, self.calendars = {}, {}
        self.calls = []          # (method, url) of API calls
        self.revocations = 0
        self.fail = []           # [(url regex, status)] consumed in order
        self.down = False        # network failure
        self.drop_changes = False  # accept calendar PATCHes / draft PUTs but don't save them (verification test)
        self.last_auth = {}      # params of the last authorization request
        self.browser_action = "approve"
        self.browser_account = "adam@gmail.test"
        self.browser_drop_scopes = []
        self.access_ttl = 3600

    # ------------------------------------------------ browser (the user, on Google's page)
    def open_browser(self, url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        self.last_auth = q
        assert q["response_type"] == "code" and q["code_challenge_method"] == "S256" and q["client_id"] == self.client_id
        assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["access_type"] == "offline"
        redirect, action = q["redirect_uri"], self.browser_action
        if action == "close":
            return  # never comes back
        if action == "deny":
            back = {"error": "access_denied", "state": q["state"]}
        else:
            code = "4/" + secrets.token_urlsafe(30)
            scopes = [s for s in q["scope"].split() if s not in self.browser_drop_scopes]
            if q.get("include_granted_scopes") == "true":
                prev = next((r["scopes"] for r in self.refresh.values() if r["account"] == self.browser_account
                             and not r["revoked"]), [])
                scopes = sorted(set(scopes) | set(prev))
            self.codes[code] = {"challenge": q["code_challenge"], "redirect": redirect, "scopes": scopes,
                                "account": self.browser_account}
            self.issued.append(code)
            back = {"code": code, "state": q["state"] if action != "csrf" else "forged-state"}

        def hit():
            time.sleep(0.1)
            urllib.request.urlopen(redirect + "?" + urllib.parse.urlencode(back), timeout=5).read()
        threading.Thread(target=hit, daemon=True).start()

    # ------------------------------------------------ requests-like interface
    def post(self, url, data=None, json=None, timeout=None, headers=None):
        return self.request("POST", url, data=data, json=json, headers=headers)

    def get(self, url, params=None, timeout=None, headers=None):
        return self.request("GET", url, params=params, headers=headers)

    def request(self, method, url, params=None, json=None, data=None, timeout=None, headers=None):
        import requests

        if self.down:
            raise requests.ConnectionError("simulated network failure")
        for i, (rx, status) in enumerate(self.fail):
            if re.search(rx, url):
                self.fail.pop(i)
                return Resp(status, {"error": {"code": status, "status": "UNAVAILABLE" if status >= 500 else "x",
                                               "errors": [{"reason": "rateLimitExceeded" if status == 429 else "backendError"}]}})
        if url == "https://oauth2.googleapis.com/token":
            return self._token(data)
        if url == "https://oauth2.googleapis.com/revoke":
            self.revocations += 1
            tok = data["token"]
            if tok in self.refresh:
                self.refresh[tok]["revoked"] = True
                return Resp(200, {})
            return Resp(400, {"error": "invalid_token"})
        auth = (headers or {}).get("Authorization", "")
        tok = self.access.get(auth.replace("Bearer ", ""))
        if not tok or tok["exp"] < time.time() or self.refresh[tok["refresh"]]["revoked"]:
            return Resp(401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}})
        self.calls.append((method, url))
        if url.startswith(GMAIL):
            return self._gmail(method, url[len(GMAIL):], params or {}, json, tok)
        if url.startswith(CAL):
            return self._cal(method, url[len(CAL):], params or {}, json, tok)
        return Resp(404, {"error": {"code": 404}})

    # ------------------------------------------------ OAuth server
    def _token(self, data):
        if data.get("client_id") != self.client_id or data.get("client_secret") != self.client_secret:
            return Resp(401, {"error": "invalid_client"})
        if data["grant_type"] == "authorization_code":
            c = self.codes.pop(data["code"], None)
            if not c:
                return Resp(400, {"error": "invalid_grant"})
            if base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest()).rstrip(b"=").decode() != c["challenge"]:
                return Resp(400, {"error": "invalid_grant", "error_description": "PKCE mismatch"})
            if data["redirect_uri"] != c["redirect"]:
                return Resp(400, {"error": "redirect_uri_mismatch"})
            rt_ = "1//" + secrets.token_urlsafe(40)
            self.refresh[rt_] = {"account": c["account"], "scopes": c["scopes"], "revoked": False}
            self.issued.append(rt_)
            body = self._access_for(rt_)
            body["refresh_token"] = rt_
            payload = {"email": c["account"], "email_verified": True, "sub": "1234"}
            body["id_token"] = f"{b64(json.dumps({'alg': 'RS256'}))}.{b64(json.dumps(payload))}.sig"
            self.issued.append(body["id_token"])
            return Resp(200, body)
        if data["grant_type"] == "refresh_token":
            r = self.refresh.get(data["refresh_token"])
            if not r or r["revoked"]:
                return Resp(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
            return Resp(200, self._access_for(data["refresh_token"]))
        return Resp(400, {"error": "unsupported_grant_type"})

    def _access_for(self, rt_):
        r = self.refresh[rt_]
        tok = "ya29." + secrets.token_urlsafe(40)
        self.access[tok] = {"account": r["account"], "scopes": r["scopes"], "exp": time.time() + self.access_ttl,
                            "refresh": rt_}
        self.issued.append(tok)
        return {"access_token": tok, "expires_in": self.access_ttl, "scope": " ".join(r["scopes"]), "token_type": "Bearer"}

    def expire_access_tokens(self):
        for t in self.access.values():
            t["exp"] = 0

    def revoke_all(self, account):
        for r in self.refresh.values():
            if r["account"] == account:
                r["revoked"] = True

    # ------------------------------------------------ Gmail
    def _need(self, tok, *scopes):
        if not any(s in tok["scopes"] for s in scopes):
            return Resp(403, {"error": {"code": 403, "errors": [{"reason": "insufficientPermissions"}]}})
        return None

    def _gmail(self, method, path, params, body, tok):
        box = self.mailboxes.setdefault(tok["account"], Mailbox(tok["account"]))
        if path.startswith("/drafts"):
            if (bad := self._need(tok, SCOPE_COMPOSE)):
                return bad
            return self._drafts(method, path, params, body, box)
        if (bad := self._need(tok, SCOPE_READ)):
            return bad
        if path == "/messages":
            ids = [m for m in sorted(box.messages, key=lambda k: -int(box.messages[k]["internalDate"]))
                   if self._match(box.messages[m], params.get("q", ""))]
            return Resp(200, {"messages": [{"id": i, "threadId": box.messages[i]["threadId"]}
                                           for i in ids[:int(params.get("maxResults", 10))]]})
        m = re.fullmatch(r"/messages/(\w+)/attachments/(\w+)", path)
        if m:
            msg = box.messages.get(m[1])
            part = next((p for p in (msg or {}).get("payload", {}).get("parts", []) if p.get("body", {}).get("attachmentId") == m[2]), None)
            return Resp(200, {"data": b64(part["_content"])}) if part else Resp(404, {"error": {"code": 404}})
        m = re.fullmatch(r"/messages/(\w+)", path)
        if m:
            msg = box.messages.get(m[1])
            if not msg:
                return Resp(404, {"error": {"code": 404}})
            if params.get("format") == "minimal":
                return Resp(200, {k: msg[k] for k in ("id", "threadId", "labelIds")})
            return Resp(200, msg)
        m = re.fullmatch(r"/threads/(\w+)", path)
        if m:
            msgs = sorted([x for x in box.messages.values() if x["threadId"] == m[1]], key=lambda x: int(x["internalDate"]))
            return Resp(200, {"id": m[1], "messages": msgs}) if msgs else Resp(404, {"error": {"code": 404}})
        return Resp(404, {"error": {"code": 404}})

    @staticmethod
    def _match(msg, q):
        h = {x["name"].lower(): x["value"] for x in msg["payload"]["headers"]}
        text = (h.get("subject", "") + " " + msg["snippet"]).lower()
        for tok in re.findall(r'(\w+:\([^)]*\)|\w+:\S+|"[^"]+"|\S+)', q):
            if tok.startswith("-"):
                continue  # (exclusions like -category:promotions: the simulated mailbox has no categories)
            if tok.startswith("from:"):
                if tok[5:].strip("()").lower() not in h.get("from", "").lower():
                    return False
            elif tok.startswith("subject:"):
                if tok[8:].strip("()").lower() not in h.get("subject", "").lower():
                    return False
            elif tok.startswith("after:"):
                if int(msg["internalDate"]) / 1000 < int(tok[6:]):
                    return False
            elif tok.startswith("before:"):
                if int(msg["internalDate"]) / 1000 >= int(tok[7:]):
                    return False
            elif tok == "is:unread":
                if "UNREAD" not in msg["labelIds"]:
                    return False
            elif tok == "in:inbox":
                if "INBOX" not in msg["labelIds"]:
                    return False
            elif tok == "has:attachment":
                if not any(p.get("filename") for p in msg["payload"]["parts"]):
                    return False
            elif tok == "is:important":
                if "IMPORTANT" not in msg["labelIds"]:
                    return False
            elif tok.lower().strip('"') not in text:
                return False
        return True

    def _drafts(self, method, path, params, body, box):
        if method == "POST" and path == "/drafts":
            box.seq += 1
            did = f"d{box.seq}"
            box.drafts[did] = {"id": did, "message": self._raw_to_msg(body["message"], f"dm{box.seq}")}
            return Resp(200, {"id": did, "message": {"id": box.drafts[did]["message"]["id"]}})
        if method == "POST" and path == "/drafts/send":
            d = box.drafts.pop(body["id"], None)
            if not d:
                return Resp(404, {"error": {"code": 404}})
            msg = d["message"]
            msg["labelIds"] = ["SENT"]
            box.messages[msg["id"]] = msg
            return Resp(200, {"id": msg["id"], "threadId": msg["threadId"], "labelIds": ["SENT"]})
        m = re.fullmatch(r"/drafts/(\w+)", path)
        if not m or m[1] not in box.drafts:
            return Resp(404, {"error": {"code": 404}})
        if method == "GET":
            return Resp(200, box.drafts[m[1]])
        if method == "PUT":
            if not self.drop_changes:
                box.drafts[m[1]]["message"] = self._raw_to_msg(body["message"], box.drafts[m[1]]["message"]["id"])
            return Resp(200, {"id": m[1]})
        if method == "DELETE":
            box.drafts.pop(m[1], None)
            return Resp(204)
        return Resp(400, {})

    @staticmethod
    def _raw_to_msg(message, mid):
        raw = base64.urlsafe_b64decode(message["raw"] + "=" * (-len(message["raw"]) % 4))
        em = message_from_bytes(raw)
        body = em.get_payload(decode=True).decode()
        headers = [{"name": k, "value": v} for k, v in em.items()]
        return {"id": mid, "threadId": message.get("threadId") or f"t-{mid}", "labelIds": ["DRAFT"],
                "snippet": body[:100], "internalDate": str(int(time.time() * 1000)),
                "payload": {"mimeType": "text/plain", "headers": headers, "body": {"data": b64(body)}}}

    # ------------------------------------------------ Calendar
    def _cal(self, method, path, params, body, tok):
        cal = self.calendars.setdefault(tok["account"], {"tz": "America/Los_Angeles", "events": {}, "seq": 0})
        writing = method in ("POST", "PATCH", "DELETE")
        if (bad := self._need(tok, SCOPE_CAL_EVENTS) if writing else self._need(tok, SCOPE_CAL_READ, SCOPE_CAL_EVENTS)):
            return bad
        if path == "/calendars/primary":
            return Resp(200, {"id": "primary", "timeZone": cal["tz"]})
        if path == "/users/me/calendarList":
            return Resp(200, {"items": [{"id": tok["account"], "summary": tok["account"], "primary": True,
                                         "timeZone": cal["tz"], "accessRole": "owner"}]})
        m = re.fullmatch(r"/calendars/primary/events(?:/(\w+))?", path)
        if not m:
            return Resp(404, {"error": {"code": 404}})
        eid = m[1]
        if eid is None and method == "GET":
            lo, hi = _dt(params["timeMin"]), _dt(params["timeMax"])
            q = (params.get("q") or "").lower()
            items = [e for e in cal["events"].values() if e["status"] != "cancelled"
                     and _dt(e["start"].get("dateTime") or e["start"]["date"] + "T00:00:00+00:00") < hi
                     and _dt(e["end"].get("dateTime") or e["end"]["date"] + "T00:00:00+00:00") > lo
                     and (not q or q in (e["summary"] + " " + e.get("description", "") + " " + e.get("location", "")).lower())]
            items.sort(key=lambda e: _dt(e["start"].get("dateTime") or e["start"]["date"] + "T00:00:00+00:00"))
            return Resp(200, {"items": items[:int(params.get("maxResults", 250))]})
        if eid is None and method == "POST":
            assert params.get("sendUpdates") == "none"
            cal["seq"] += 1
            new = {"id": f"ev{cal['seq']}", "status": "confirmed", "summary": body["summary"], "start": body["start"],
                   "end": body["end"], "location": body.get("location", ""), "description": body.get("description", "")}
            cal["events"][new["id"]] = new
            return Resp(200, new)
        ev = cal["events"].get(eid)
        if not ev or (ev["status"] == "cancelled" and method != "GET"):
            return Resp(404, {"error": {"code": 404}})
        if method == "GET":
            return Resp(200, ev)
        if method == "PATCH":
            if not self.drop_changes:
                ev.update(body)
            return Resp(200, ev)
        if method == "DELETE":
            ev["status"] = "cancelled"
            return Resp(204)
        return Resp(400, {})

    def add_event(self, account, summary, start, end, description="", location="", tz="America/Los_Angeles"):
        cal = self.calendars.setdefault(account, {"tz": tz, "events": {}, "seq": 0})
        cal["seq"] += 1
        eid = f"ev{cal['seq']}"
        cal["events"][eid] = {"id": eid, "status": "confirmed", "summary": summary, "start": {"dateTime": start.isoformat()},
                              "end": {"dateTime": end.isoformat()}, "description": description, "location": location}
        return eid


def _dt(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
