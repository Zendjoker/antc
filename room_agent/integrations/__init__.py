"""Connections to outside services (Settings -> Connections).

    base.py        provider interface, services/access levels, errors with human wording, provenance
    oauth.py       OAuth 2.0 Authorization Code + PKCE + state, system browser, loopback redirect, refresh, revoke
    vault.py       credentials in Windows Credential Manager (never files, logs, prompts, memory or telemetry)
    state.py       non-secret connection status shared with the settings page (connections.json)
    knowledge.py   PersonalKnowledge: live service data first, provenance on everything, no inbox copying
    untrusted.py   outside content is wrapped and treated as data, never instructions
    redact.py      scrubs any credential-looking text out of every log line
    google/        GoogleProvider (one connection) + GmailService + CalendarService
    capabilities.py  the Gmail and Calendar capabilities, registered in the capability registry

Adding a provider: implement IntegrationProvider (+ its services), add it to PROVIDERS, register its capabilities.
"""

from room_agent.integrations import redact

redact.install()  # before anything here can log

_providers = {}


def provider(pid):
    if pid not in _providers:
        if pid == "google":
            from room_agent.integrations.google.provider import GoogleProvider

            _providers[pid] = GoogleProvider()
        else:
            raise KeyError(pid)
    return _providers[pid]


PROVIDERS = ["google"]  # (future: microsoft, spotify, github, slack, home_assistant...)


def all_providers():
    return [provider(p) for p in PROVIDERS]
