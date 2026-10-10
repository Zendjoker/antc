"""OAuth 2.0 for a desktop app: Authorization Code flow + PKCE (S256), in the system browser, with a one-shot loopback
redirect (http://127.0.0.1:<random port>/), a random `state` checked on return (CSRF), and refresh tokens for offline
access. Provider-neutral: any provider gives its endpoints in an OAuthApp.

Jarvis never sees or asks for a password: the user signs in on the provider's own page in their browser.
Tokens are returned to the caller (the provider) only, which puts the long-lived one in the vault.
"""

import base64
import hashlib
import http.server as httpserver
import logging
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field

import requests

from room_agent.integrations.base import AuthCanceled, AuthExpired, IntegrationError, Unavailable

log = logging.getLogger("room-agent")


@dataclass
class OAuthApp:
    provider: str            # display name, for messages
    auth_url: str
    token_url: str
    revoke_url: str
    client_id: str
    client_secret: str = ""  # desktop ("installed") clients have one that isn't really secret; sent as the provider requires
    extra: dict = field(default_factory=dict)  # provider-specific auth parameters (access_type=offline...)


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def pkce_pair():
    verifier = _b64url(secrets.token_bytes(48))  # 64 chars, within RFC 7636's 43..128
    return verifier, _b64url(hashlib.sha256(verifier.encode()).digest())


PAGE = ("<!doctype html><meta charset=utf-8><title>Jarvis</title><body style='font-family:system-ui;background:#111;"
        "color:#eee;display:grid;place-items:center;height:100vh;margin:0'><div style='text-align:center'><h2>{title}</h2>"
        "<p>{text}</p></div></body>")


def run_browser_flow(app: OAuthApp, scopes, open_browser=None, timeout=300, login_hint="", http=requests):
    """Sign in in the system browser. -> the token response (access_token, refresh_token, scope, id_token...).
    Raises AuthCanceled (they said no / closed it / timed out) or IntegrationError."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(32)
    result = {}
    done = threading.Event()

    class Callback(httpserver.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if not q:  # (favicon and the like)
                self.send_response(404)
                self.end_headers()
                return
            forged = not secrets.compare_digest(q.get("state", [""])[0].encode(), state.encode())
            if forged:  # not our request: its code is ignored, and the real sign-in keeps waiting (no denial of service)
                result["ignored"] = result.get("ignored", 0) + 1
                title, text = "Sign-in ignored", "This sign-in didn't come from Jarvis, so it was ignored."
            elif "error" in q:
                result["error"] = q["error"][0]
                title, text = "Sign-in canceled", "Nothing was connected. You can close this tab."
            else:
                result["code"] = q.get("code", [""])[0]
                title, text = "Connected", "You can close this tab and go back to Jarvis."
            body = PAGE.format(title=title, text=text).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if not forged:
                done.set()

        def log_message(self, *args):  # the request line carries the code: never log it
            pass

    server = httpserver.HTTPServer(("127.0.0.1", 0), Callback)  # loopback only, a free port
    server.timeout = 0.5
    redirect = f"http://127.0.0.1:{server.server_address[1]}/"
    params = {"response_type": "code", "client_id": app.client_id, "redirect_uri": redirect, "scope": " ".join(scopes),
              "state": state, "code_challenge": challenge, "code_challenge_method": "S256", **app.extra}
    if login_hint:
        params["login_hint"] = login_hint
    url = app.auth_url + "?" + urllib.parse.urlencode(params)
    log.info("opening the browser to sign in to %s", app.provider)
    try:
        (open_browser or webbrowser.open)(url)
        deadline = time.time() + timeout
        while not done.is_set() and time.time() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if result.get("ignored"):
        log.warning("sign-in: ignored %d callback(s) whose state didn't match (not from this sign-in)", result["ignored"])
    if not done.is_set():
        raise AuthCanceled(app.provider, "timed out")
    if result.get("error"):
        raise AuthCanceled(app.provider, result["error"])
    data = {"code": result["code"], "client_id": app.client_id, "redirect_uri": redirect,
            "grant_type": "authorization_code", "code_verifier": verifier}
    if app.client_secret:
        data["client_secret"] = app.client_secret
    return _token_call(app, http, data)


def refresh(app: OAuthApp, refresh_token, http=requests):
    """A fresh access token. AuthExpired if the grant was revoked / expired (the user must reconnect)."""
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": app.client_id}
    if app.client_secret:
        data["client_secret"] = app.client_secret
    return _token_call(app, http, data)


def revoke(app: OAuthApp, token, http=requests):
    """Tell the provider to drop the grant. True if it confirmed (or the grant was already gone)."""
    try:
        r = http.post(app.revoke_url, data={"token": token}, timeout=15,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
        return r.status_code in (200, 400)  # 400: already invalid
    except requests.RequestException:
        return False


def _token_call(app, http, data):
    try:
        r = http.post(app.token_url, data=data, timeout=20, headers={"Accept": "application/json"})
    except requests.RequestException as e:
        raise Unavailable(app.provider, e.__class__.__name__)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 200 and body.get("access_token"):
        return body
    err = body.get("error", f"http_{r.status_code}")
    if err in ("invalid_grant", "unauthorized_client", "invalid_token"):
        raise AuthExpired(app.provider, err)
    if r.status_code >= 500:
        raise Unavailable(app.provider, err)
    raise IntegrationError(app.provider, err)  # (never the body: it may echo what was sent)
