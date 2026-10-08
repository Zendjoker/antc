"""Google connection health, offline (simulated Google, in-memory vault): an expired / revoked sign-in is announced once
and only once, the background check finds it early, a healthy connection stays quiet, and nothing is checked when
Google isn't connected. Your real account and vault are never touched.

Run:  .venv\\Scripts\\python -m tests.test_google_health
"""

import time

from tests.harness import setup_env

setup_env(JARVIS_VAULT="memory", GOOGLE_CLIENT_ID="test-client.apps.googleusercontent.com",
          GOOGLE_CLIENT_SECRET="GOCSPX-test-secret-123")

from room_agent import triggers  # noqa: E402
from room_agent.conversation import greet  # noqa: E402
from room_agent.conversation.states import State  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.integrations import provider, state  # noqa: E402
from tests.fake_google import FakeGoogle  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
FG = FakeGoogle()
G = provider("google")
G.http = FG
said = []
greet.say_soon = lambda text: said.append(text)
rt.state.go(State.WAKE_WORD_ONLY, "test")

print("Nothing connected:")
t.check("not connected -> the background check does nothing (no call)", triggers.google_health(now=1e10, provider=G) is None
        and not FG.calls)

print("Connected and healthy:")
G.connect(open_browser=FG.open_browser, timeout=5)
acct = G.active()
issued = len(FG.access)
t.check("healthy -> the check refreshes the token (free sign-in call: a new access token) and says nothing",
        triggers.google_health(now=2e10, provider=G) is True and len(FG.access) > issued and not said)
t.check("...and it doesn't run again within 6 hours", triggers.google_health(now=2e10 + 3600, provider=G) is None)

print("Expired / revoked:")
FG.revoke_all(acct)
r = triggers.google_health(now=3e10, provider=G)
t.check("revoked at Google -> the check finds it (before they ask for email)", r is False and G.connection_status() == "expired")
t.check("...and says it once, pointing to Connections", len(said) == 1 and "reconnect" in said[0] and "Connections" in said[0],
        said)
G.refresh_credentials()
try:
    G.session().call("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages")
except Exception:
    pass
t.check("...later failures don't repeat it", len(said) == 1, said)
t.check("the dashboard shows it too (status 'expired', the reason kept)", state.provider("google")["accounts"][acct]["status"] == "expired"
        and state.provider("google")["accounts"][acct].get("last_error"))
G.connect(open_browser=FG.open_browser, timeout=5)
t.check("reconnecting clears it", G.connection_status() == "connected")
FG.revoke_all(acct)
said.clear()
triggers.google_health(now=4e10, provider=G)
t.check("...and a later expiry is announced again (once)", len(said) == 1)
t.done("GOOGLE HEALTH")
