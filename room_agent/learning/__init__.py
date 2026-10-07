"""Learning & adaptation: Jarvis gets to know how each user likes things done, from what they say and what they do.

No model is trained or fine-tuned. Everything is structured data in a local file (learning.db) that works the same
with Claude, OpenAI or a local model:

    storage.py       SQLite: preferences, evidence (signals), interaction records. Per user, local only.
    model.py         UserModel: preference kinds, confidence from evidence, explicit > inferred > default.
    adaptation.py    records interactions, detects corrections / signals, applies preferences via executor hooks.
    privacy.py       secrets are never learned; stored text is redacted.
    capabilities.py  "always do X", "what have you learned?", "why did you do that?", "forget that", routines.

The current user is `rt.user_profile` (one profile until speaker identification can tell people apart).
"""

import threading

from room_agent import config
from room_agent import runtime as rt

_lock = threading.Lock()
_store = None
_learner = None


def store():
    global _store
    with _lock:
        if _store is None:
            from room_agent.learning.storage import LearningStore

            _store = LearningStore(config.LEARNING_DB)
        return _store


def user_model(user=None):
    from room_agent.learning.model import UserModel

    return UserModel(store(), (user or rt.user_profile or "default").strip().lower())


def learner():
    global _learner
    if _learner is None:
        from room_agent.learning.adaptation import Learner

        _learner = Learner(user_model, store(), telemetry=config.LEARNING_TELEMETRY)
    return _learner


def set_user(profile):
    """Switch whose preferences are used (e.g. when speaker identification recognizes someone else)."""
    rt.user_profile = (profile or "default").strip().lower()
    if _learner is not None:
        _learner.last, _learner.decisions = None, []


def context_lines(text):
    """The learned preferences that matter for this request (a few short lines; empty for most chat)."""
    try:
        from room_agent.actions.context import env

        return user_model().relevant(text, env.active_app)
    except Exception:
        return []
