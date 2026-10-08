"""Google: ONE connection (OAuth) that Gmail, Calendar and later Drive/Contacts all use.

Least privilege: only identity + the access levels switched on are requested; turning on more (e.g. Gmail drafts &
sending) asks Google for just that, on top of what was already granted (incremental authorization).
Several Google accounts can be connected; one is active. Each has its own refresh token in the vault.
"""

import base64
import json
import logging
import threading
import time

import requests

from room_agent import config
from room_agent.integrations import oauth, state
from room_agent.integrations.base import (AccessLevel, AuthExpired, IntegrationError, IntegrationProvider, MissingScope,
                                          NotConfigured, NotConnected, NotFound, RateLimited, ServiceSpec, Unavailable)
from room_agent.integrations.vault import vault

log = logging.getLogger("room-agent")

IDENTITY = ["openid", "https://www.googleapis.com/auth/userinfo.email"]
GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
CAL_READ = "https://www.googleapis.com/auth/calendar.readonly"
CAL_EVENTS = "https://www.googleapis.com/auth/calendar.events"
SCOPE_LABELS = {GMAIL_READ: "read and search Gmail", GMAIL_COMPOSE: "write Gmail drafts and send email",
                CAL_READ: "see your calendars and events", CAL_EVENTS: "add, change and delete calendar events",
                "openid": "know it's you", "https://www.googleapis.com/auth/userinfo.email": "see your email address",
                "email": "see your email address"}

SERVICES = [
    ServiceSpec("gmail", "Gmail", [
        AccessLevel("read", "Read and search your email", [GMAIL_READ]),
        AccessLevel("compose", "Write drafts and send email (Jarvis always asks before sending)", [GMAIL_COMPOSE],
                    consequential=True)]),
    ServiceSpec("calendar", "Google Calendar", [
        AccessLevel("read", "See your calendars and events", [CAL_READ]),
        AccessLevel("write", "Add, change and delete events (asks before deleting)", [CAL_EVENTS], consequential=True)]),
]


def _client():
    """The app registration (Google Cloud "Desktop app" OAuth client): .env or the downloaded JSON file."""
    cid, secret = config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET
    if not cid and config.GOOGLE_CLIENT_FILE and config.GOOGLE_CLIENT_FILE.exists():
        try:
            data = json.loads(config.GOOGLE_CLIENT_FILE.read_text(encoding="utf-8"))
            data = data.get("installed") or data.get("web") or {}
            cid, secret = data.get("client_id", ""), data.get("client_secret", "")
        except (ValueError, OSError):
            pass
    return cid, secret


class GoogleProvider(IntegrationProvider):
    id = "google"
    display_name = "Google"
    icon = "google"
    services = SERVICES
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    REVOKE_URL = "https://oauth2.googleapis.com/revoke"

    def __init__(self, http=requests):
        self.http = http           # (tests pass a simulated Google here)
        self._tokens = {}          # account -> (access token, expires at): memory only, never written anywhere
        self._lock = threading.Lock()

    # ----- setup
    def configured(self):
        return bool(_client()[0])

    def app(self):
        cid, secret = _client()
        if not cid:
            raise NotConfigured("Google")
        return oauth.OAuthApp("Google", self.AUTH_URL, self.TOKEN_URL, self.REVOKE_URL, cid, secret,
                              extra={"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"})

    # ----- accounts
    def accounts(self):
        return list(state.provider(self.id)["accounts"])

    def active(self):
        return state.provider(self.id).get("active")

    def set_active(self, account):
        if account not in self.accounts():
            raise NotConnected("Google")
        state.update_provider(self.id, lambda p: p.update(active=account))
        from room_agent.integrations import knowledge

        knowledge.forget_account(None)  # switching accounts: nothing cached from another one may show through

    def _account(self, account=None):
        account = account or self.active()
        if not account or account not in self.accounts():
            raise NotConnected("Google")
        return account

    # ----- connect / disconnect
    def scopes_for(self, levels):
        scopes = list(IDENTITY)
        for svc in SERVICES:
            for lid in (levels or {}).get(svc.id, []):
                scopes += [s for s in svc.level(lid).scopes if s not in scopes]
        return scopes

    def connect(self, levels=None, account_hint="", open_browser=None, timeout=300):
        levels = levels or {"gmail": ["read"], "calendar": ["read"]}
        app = self.app()
        scopes = self.scopes_for(levels)
        if account_hint and account_hint in self.accounts():  # (incremental: keep what this account already has)
            scopes += [s for s in self.granted_scopes(account_hint) if s not in scopes]
        tokens = oauth.run_browser_flow(app, scopes, open_browser=open_browser, timeout=timeout, login_hint=account_hint,
                                        http=self.http)
        account = self._identity(tokens)
        granted = sorted(set((tokens.get("scope") or "").split()))
        old = vault().get(self.id, account) or {}
        refresh_token = tokens.get("refresh_token") or old.get("refresh_token")
        if not refresh_token:
            raise IntegrationError("Google", "no refresh token")
        vault().put(self.id, account, {"refresh_token": refresh_token, "scopes": granted})
        with self._lock:
            self._tokens[account] = (tokens["access_token"], time.time() + float(tokens.get("expires_in", 3600)))
        missing = [s for s in scopes if s not in granted and s not in IDENTITY]
        state.set_account(self.id, account, scopes=granted, status="connected", last_success=time.time(), last_error="",
                          disabled=[])
        log.info("Google connected (%d services)", len(self.available_services(account)))
        return {"account": account, "scopes": granted, "missing": missing, "services": self.available_services(account)}

    def reconnect(self, account, open_browser=None, timeout=300):
        levels = self.available_services(account, include_disabled=True) or {"gmail": ["read"], "calendar": ["read"]}
        return self.connect(levels, account_hint=account, open_browser=open_browser, timeout=timeout)

    def enable(self, account, service, level, on=True, open_browser=None, timeout=300):
        """Switch an access level on (asking Google for its scope if it isn't granted yet) or off (Jarvis stops using it;
        the grant itself stays until disconnect)."""
        key = f"{service}.{level}"
        if not on:
            state.update_provider(self.id, lambda p: p["accounts"][account].setdefault("disabled", []).append(key)
                                  if key not in p["accounts"][account].get("disabled", []) else None)
            return {"account": account, "services": self.available_services(account)}
        state.update_provider(self.id, lambda p: p["accounts"][account].update(
            disabled=[d for d in p["accounts"][account].get("disabled", []) if d != key]))
        need = next(s for s in SERVICES if s.id == service).level(level).scopes
        if all(s in self.granted_scopes(account) for s in need):
            return {"account": account, "services": self.available_services(account)}
        levels = self.available_services(account, include_disabled=True)
        levels.setdefault(service, []).append(level)
        return self.connect(levels, account_hint=account, open_browser=open_browser, timeout=timeout)

    def disconnect(self, account):
        """Revoke at Google, delete the local credential, forget the account and anything cached from it."""
        saved = vault().get(self.id, account) or {}
        revoked = True
        if saved.get("refresh_token"):
            try:
                revoked = oauth.revoke(self.app(), saved["refresh_token"], http=self.http)
            except NotConfigured:
                revoked = False
        vault().delete(self.id, account)
        with self._lock:
            self._tokens.pop(account, None)
        state.remove_account(self.id, account)
        from room_agent.integrations import knowledge

        knowledge.forget_account(account)
        return revoked

    # ----- status
    def connection_status(self, account=None):
        if not self.configured():
            return "not_configured"
        try:
            account = self._account(account)
        except NotConnected:
            return "disconnected"
        info = state.provider(self.id)["accounts"].get(account, {})
        if info.get("status") == "expired" or not (vault().get(self.id, account) or {}).get("refresh_token"):
            return "expired"
        return "connected"

    def account_identity(self, account=None):
        try:
            return self._account(account)
        except NotConnected:
            return None

    def granted_scopes(self, account=None):
        try:
            account = self._account(account)
        except NotConnected:
            return []
        return state.provider(self.id)["accounts"].get(account, {}).get("scopes", [])

    def available_services(self, account=None, include_disabled=False):
        try:
            account = self._account(account)
        except NotConnected:
            return {}
        granted = set(self.granted_scopes(account))
        disabled = set(state.provider(self.id)["accounts"].get(account, {}).get("disabled", []))
        out = {}
        for svc in SERVICES:
            levels = [l.id for l in svc.levels if set(l.scopes) <= granted
                      and (include_disabled or f"{svc.id}.{l.id}" not in disabled)]
            if levels:
                out[svc.id] = levels
        return out

    def can(self, service, level, account=None):
        return self.connection_status(account) == "connected" and level in self.available_services(account).get(service, [])

    # ----- tokens (never leave this object, except as the Authorization header of a request to Google)
    def _access_token(self, account, force=False):
        with self._lock:
            tok = self._tokens.get(account)
        if tok and not force and tok[1] - time.time() > 60:
            return tok[0]
        saved = vault().get(self.id, account) or {}
        if not saved.get("refresh_token"):
            self._expired(account, "no_credential")
        try:
            fresh = oauth.refresh(self.app(), saved["refresh_token"], http=self.http)
        except AuthExpired:
            self._expired(account, "refresh_rejected")
        with self._lock:
            self._tokens[account] = (fresh["access_token"], time.time() + float(fresh.get("expires_in", 3600)))
        if fresh.get("refresh_token") and fresh["refresh_token"] != saved["refresh_token"]:
            vault().put(self.id, account, {**saved, "refresh_token": fresh["refresh_token"]})
        if fresh.get("scope"):
            granted = sorted(set(fresh["scope"].split()))
            if granted != sorted(saved.get("scopes", [])):  # (they removed a permission in their Google account)
                vault().put(self.id, account, {**(vault().get(self.id, account) or saved), "scopes": granted})
                state.set_account(self.id, account, scopes=granted)
        return fresh["access_token"]

    def refresh_credentials(self, account=None):
        try:
            self._access_token(self._account(account), force=True)
            return True
        except IntegrationError:
            return False

    def _expired(self, account, why):
        with self._lock:
            self._tokens.pop(account, None)
        was = (state.provider(self.id)["accounts"].get(account) or {}).get("status")
        state.set_account(self.id, account, status="expired", last_error=why, last_error_at=time.time())
        if was != "expired":  # (said once, when it changes: not on every failed request afterwards)
            self._tell_expired(account, why)
        raise AuthExpired("Google", why)

    def _tell_expired(self, account, why):
        connected = (state.provider(self.id)["accounts"].get(account) or {}).get("connected_at") or 0
        days = (time.time() - connected) / 86400 if connected else 0
        log.warning("Google: %s's connection stopped working (%s)%s", account, why,
                    " after ~7 days: a Google Cloud app in 'Testing' mode gets 7-day sign-ins (publish it, or reconnect "
                    "weekly)" if 6.5 <= days <= 8 else "")
        try:
            from room_agent import triggers

            triggers.say("Heads up: your Google connection stopped working, so I can't check your email or calendar until "
                         "you reconnect it. It's on the dashboard under Connections.", "reminder",
                         f"google-expired:{account}:{time.time():.3f}")  # (once per expiry: the status change)
        except Exception as e:
            log.debug("Google: couldn't announce the expiry (%s)", e)

    def _identity(self, tokens):
        """The signed-in account's email, from the ID token Google just returned to us directly (over TLS)."""
        try:
            payload = tokens["id_token"].split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if claims.get("email"):
                return claims["email"].lower()
        except (KeyError, IndexError, ValueError):
            pass
        r = self.http.get("https://openidconnect.googleapis.com/v1/userinfo", timeout=15,
                          headers={"Authorization": "Bearer " + tokens["access_token"]})
        email = (r.json() or {}).get("email") if r.status_code == 200 else None
        if not email:
            raise IntegrationError("Google", "no account identity")
        return email.lower()

    def session(self, account=None):
        return GoogleSession(self, self._account(account))


class GoogleSession:
    """Authorized calls to Google APIs for one account. Turns every failure into a human IntegrationError."""

    def __init__(self, provider, account):
        self.provider, self.account = provider, account

    def call(self, method, url, params=None, json_body=None, scope_hint=""):
        for attempt in (1, 2):
            token = self.provider._access_token(self.account, force=attempt == 2)
            try:
                r = self.provider.http.request(method, url, params=params, json=json_body, timeout=20,
                                               headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
            except requests.RequestException as e:
                state.note("google", self.account, False, "network")
                raise Unavailable("Google", e.__class__.__name__)
            if r.status_code == 401 and attempt == 1:
                continue  # token expired early or was revoked: refresh once, then give up
            if r.status_code == 401:
                self.provider._expired(self.account, "unauthorized")
            if r.status_code in (200, 201, 204):
                state.note("google", self.account, True)
                return r.json() if r.content else {}
            state.note("google", self.account, False, f"http_{r.status_code}")
            reason = _reason(r)
            if r.status_code in (404, 410):
                raise NotFound("Google", reason)
            if r.status_code == 429 or reason in ("rateLimitExceeded", "userRateLimitExceeded"):
                raise RateLimited("Google", reason)
            if r.status_code == 403 and reason in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "forbidden"):
                raise MissingScope("Google", scope_hint or reason)
            if r.status_code == 403 and reason in ("accessNotConfigured", "SERVICE_DISABLED"):
                raise NotConfigured("Google", "api disabled")
            raise Unavailable("Google", f"http_{r.status_code}")


def _reason(r):
    try:
        err = r.json().get("error", {})
        if isinstance(err, dict):
            details = err.get("errors") or err.get("details") or []
            for d in details:
                if d.get("reason"):
                    return d["reason"]
            return err.get("status", "")
        return str(err)
    except ValueError:
        return ""
