"""The generic pieces every integration shares: the provider interface, services with access levels, errors with
human wording, and provenance for anything retrieved.

    Provider (authentication: one connection, possibly several accounts)
      -> Services (Gmail, Calendar... each with access levels mapped to provider scopes)
        -> Capabilities (registered in the capability registry; the model sees only these)

A provider never hands its credentials to a service: services ask the provider for an authorized session.
"""

import time
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------- errors (always say something human)
class IntegrationError(Exception):
    code = "error"
    human = "Something went wrong talking to {provider}."
    recoverable = True

    def __init__(self, provider="the service", detail=""):
        super().__init__(detail or self.code)
        self.provider, self.detail = provider, detail

    def say(self):
        return self.human.format(provider=self.provider)


class NotConfigured(IntegrationError):
    code, human, recoverable = "not_configured", "{provider} isn't set up yet: it needs an app registration first (see Connections).", False


class NotConnected(IntegrationError):
    code, human, recoverable = "not_connected", "{provider} isn't connected yet. Connect it in Settings, Connections.", False


class AuthCanceled(IntegrationError):
    code, human = "canceled", "The {provider} sign-in was canceled, so nothing was connected."


class AuthExpired(IntegrationError):
    code, human, recoverable = "auth_expired", "I lost access to your {provider} account. Reconnect it in Connections.", False


class MissingScope(IntegrationError):
    code, human, recoverable = "missing_scope", "Jarvis doesn't have permission for that in {provider} yet. Turn it on in Connections.", False


class RateLimited(IntegrationError):
    code, human = "rate_limited", "{provider} is asking me to slow down. Try again in a minute."


class Unavailable(IntegrationError):
    code, human = "unavailable", "I couldn't reach {provider} just now. Try again in a bit."


class NotFound(IntegrationError):
    code, human = "not_found", "I couldn't find that in {provider}."


# ---------------------------------------------------------------- services and access levels
@dataclass
class AccessLevel:
    id: str                 # read / compose / write
    label: str              # "Read and search your email"
    scopes: list            # provider scopes this level needs
    consequential: bool = False  # sends, deletes, shares: Jarvis always confirms these, whatever the scope allows


@dataclass
class ServiceSpec:
    id: str                 # gmail / calendar
    name: str               # "Gmail"
    levels: list            # [AccessLevel], least privilege first
    default_levels: list = field(default_factory=lambda: ["read"])

    def level(self, level_id):
        return next(l for l in self.levels if l.id == level_id)


# ---------------------------------------------------------------- where a piece of information came from
@dataclass
class Provenance:
    provider: str           # google
    service: str            # gmail
    resource: str           # message / event / thread / draft ...
    resource_id: str
    account: str            # which connected account
    retrieved_at: float = field(default_factory=time.time)
    freshness: str = "live"  # live | cached
    meta: dict = field(default_factory=dict)

    def as_dict(self):
        return {"provider": self.provider, "service": self.service, "resource": self.resource, "id": self.resource_id,
                "account": self.account, "retrieved_at": self.retrieved_at, "freshness": self.freshness}


# ---------------------------------------------------------------- the provider interface
class IntegrationProvider:
    """One external account system (Google, Microsoft, Spotify...). Authentication lives here; services use it."""

    id = "provider"
    display_name = "Provider"
    icon = ""
    services: list = []     # [ServiceSpec]

    def configured(self) -> bool:
        """Is the app registration (client id...) in place?"""
        raise NotImplementedError

    def connect(self, levels: Optional[dict] = None, account_hint: str = "", open_browser=None) -> dict:
        """Sign in (in the system browser) and grant `levels` ({service: [level ids]}). -> the account record."""
        raise NotImplementedError

    def reconnect(self, account: str, open_browser=None) -> dict:
        raise NotImplementedError

    def disconnect(self, account: str) -> None:
        """Revoke at the provider, delete local credentials, forget the account."""
        raise NotImplementedError

    def connection_status(self, account: Optional[str] = None) -> str:
        """connected / expired / disconnected / not_configured"""
        raise NotImplementedError

    def account_identity(self, account: Optional[str] = None) -> Optional[str]:
        raise NotImplementedError

    def granted_scopes(self, account: Optional[str] = None) -> list:
        raise NotImplementedError

    def available_services(self, account: Optional[str] = None) -> dict:
        """{service id: [granted level ids]}"""
        raise NotImplementedError

    def refresh_credentials(self, account: Optional[str] = None) -> bool:
        raise NotImplementedError

    def session(self, account: Optional[str] = None):
        """An authorized session for services to call the provider's APIs with. Never exposes the tokens."""
        raise NotImplementedError
