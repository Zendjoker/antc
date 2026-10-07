"""PersonalKnowledge: facts owned by an external service (your calendar, your inbox) are looked up THERE, when needed.

Authority:  live service data  >  a recent cached copy (only if the service can't be reached right now)  >  what was
said in conversation / memory. Service content is NOT copied into long-term memory; a small in-memory cache exists only
so a network blip doesn't make Jarvis forget what it just read, and every item carries its provenance (which service,
account, resource, when, live or cached) so Jarvis can say "from your calendar" vs "you mentioned".

Future background features ("tell me when Andrew replies") plug in through Watch / ChangeSource below. They're opt-in
and nothing polls now.
"""

import threading
import time

from room_agent.integrations.base import Provenance, Unavailable

CACHE_MAX_AGE_S = 3600     # a cached copy older than this is never used
_cache = {}                # (account, service, key) -> (data, Provenance)
_lock = threading.Lock()


def fetch(provider, service, resource, key, account, live, cacheable=True):
    """Get something from its source of truth. `live()` calls the service. If the service is unreachable, a recent
    copy is used and marked as cached. -> (data, Provenance)"""
    ck = (account, service, resource, key)
    try:
        data = live()
    except Unavailable:
        with _lock:
            hit = _cache.get(ck)
        if hit and time.time() - hit[1].retrieved_at < CACHE_MAX_AGE_S:
            data, prov = hit
            return data, Provenance(prov.provider, prov.service, prov.resource, prov.resource_id, prov.account,
                                    prov.retrieved_at, "cached", prov.meta)
        raise
    prov = Provenance(provider, service, resource, str(key), account)
    if cacheable:
        with _lock:
            _cache[ck] = (data, prov)
            if len(_cache) > 200:  # (small: this is a safety net, not a mirror of the inbox)
                for old in sorted(_cache, key=lambda k: _cache[k][1].retrieved_at)[:50]:
                    _cache.pop(old, None)
    return data, prov


def source_note(prov):
    """How Jarvis should think of where this came from (short; no technical detail)."""
    where = {"gmail": "their Gmail", "calendar": "their Google Calendar"}.get(prov.service, prov.service)
    if prov.freshness == "cached":
        mins = int((time.time() - prov.retrieved_at) // 60)
        return f"source: {where}, a copy from {mins} min ago (couldn't reach Google just now; say so)"
    return f"source: {where}, live"


def forget_account(account):
    """Drop everything cached from this account (or from all, with None): disconnect / account switch."""
    with _lock:
        for k in [k for k in _cache if account is None or k[0] == account]:
            _cache.pop(k, None)


# ---------------------------------------------------------------- future: background changes (interfaces only)
class ChangeSource:
    """A service that can report what changed since a cursor (Gmail historyId, Calendar syncToken) or push changes
    (webhooks). Implement for a service, then Watches can be built on it. Not implemented/enabled yet."""

    def changes_since(self, cursor):
        raise NotImplementedError("background sync isn't enabled")


class Watch:
    """An opt-in standing request ("tell me when Andrew replies"). Stored with the user's consent; evaluated by a
    future sync worker from ChangeSource events, never by polling the whole inbox."""

    def __init__(self, user, service, condition, notify):
        self.user, self.service, self.condition, self.notify = user, service, condition, notify


def watches_enabled():
    from room_agent import config

    return config.BACKGROUND_SYNC
