"""Emotional & social intelligence: Jarvis notices how the conversation is going and adapts what it says and how it
sounds, without announcing what it noticed.

    heard(pcm, text)        the voice loop hands over each utterance's audio (cheap prosody features)
    on_user_turn(text)      before the model is called: evidence -> SocialState -> ResponseStrategy + VoiceDelivery
                            (rt.turn.strategy / rt.turn.delivery); no extra model call, a few ms
    after_turn(reply,...)   what happened (failed? interrupted? ended with a question? used their name?)
    scrub(sentence)         stock assistant phrasing out, before anything is spoken
    over_cap(said)          a "tiny reply" turn has said enough

Separate from everything long-term: SocialState lives in memory only and is never written to memory.db, learning.db
or the conversation summaries. Stable preferences ("keep answers short", "no jokes in the morning") stay in the
UserModel (learning/), which this only reads.
"""

import logging
import re
import time

from room_agent import runtime as rt
from room_agent import trace
from room_agent.config import USER_NAME
from room_agent.social import habits, meaning, prosody, signals
from room_agent.social.delivery import VoiceDelivery, from_strategy  # noqa: F401 (re-exported)
from room_agent.social.state import SocialState
from room_agent.social.strategy import ResponseStrategy, derive, long_term  # noqa: F401 (re-exported)

log = logging.getLogger("room-agent")
CONFIDENT = 0.45   # below this, the voice keeps its usual style (only the pace hint is used)
state = SocialState()
_heard = {"feat": None, "at": 0.0}


def heard(pcm, text):
    """Audio of the utterance just transcribed (voice mode). Measured now (a few ms), used by the next on_user_turn."""
    try:
        feat = prosody.measure(pcm, text)
    except Exception as e:  # (never in the way of the conversation)
        log.debug("prosody: %s", e)
        feat = None
    _heard.update(feat=feat, at=time.time())


def _situation():
    driving = on_call = False
    try:
        from room_agent.phone import state as phone

        driving, on_call = phone.is_driving(), bool(phone.in_call)
    except Exception:
        pass
    ringing = bool(rt.ringing or (rt.last_ring and time.time() - rt.last_ring["at"] < 60))
    return driving, on_call, ringing


def on_user_turn(text):
    """Update SocialState from this utterance and decide how to answer. -> ResponseStrategy (also on rt.turn)."""
    now = time.time()
    try:
        failures = signals.conversation(now, state.history)
        evidence = signals.language(text, now, failures) + signals.behavior(text, now, state.history)
        from room_agent.speech.language import detect

        evidence += meaning.evidence(text, now, detect(text))  # (what it means, not just which words: meaning.py)
        feat = _heard["feat"] if now - _heard["at"] < 15 else None
        _heard["feat"] = None
        evidence += prosody.evidence(feat, now)
        driving, on_call, ringing = _situation()
        evidence += signals.situation(now, time.localtime(now).tm_hour, driving, on_call, ringing)
        state.add(evidence)
        state.turns += 1
        snap = state.snapshot(now)
        recent = state.history[-3:]
        streak = 0
        for h in reversed(recent):
            if not h.get("question"):
                break
            streak += 1
        habits_now = {"questions_in_a_row": streak, "name_recently": any(h.get("name_used") for h in recent[-2:])}
        strategy = derive(snap, text, long_term(text, snap["interaction_mode"]), habits_now, {"driving": driving})
        delivery = from_strategy(strategy, confident=snap["confidence"] >= CONFIDENT)
    except Exception as e:
        log.warning("social layer skipped this turn: %s", e)
        strategy, delivery, snap, evidence = ResponseStrategy(), VoiceDelivery(), None, []
    rt.turn.strategy, rt.turn.delivery = strategy, delivery
    state.note_turn(user=text, reply="", failed=False, interrupted=False, question=False, name_used=False)
    if snap and (evidence or not strategy.is_default()):
        log.info("social: mode %s, mood %s, energy %s (confidence %.2f%s) -> %s; voice %s x%.2f", snap["interaction_mode"],
                 snap["mood_signal"], snap["energy"], snap["confidence"],
                 f", from {'+'.join(snap['sources'])}" if snap["sources"] else "", ", ".join(strategy.why) or "default",
                 delivery.style or "usual", delivery.pace)
        trace.note("SOCIAL", f"{snap['interaction_mode']}/{snap['mood_signal']} ({snap['confidence']}) -> "
                             f"{', '.join(strategy.why) or 'default'}")
    return strategy


def after_turn(reply, plan=None, interrupted=False):
    """What the reply turned out to be: failures and interruptions are context for the next turn."""
    failed = bool(plan and any(s.error_code in ("failed", "unavailable", "dependency_failed") for s in plan.steps))
    pools = rt.phrases.pools
    failed = failed or reply in pools.get("unsure", []) or reply in pools.get("missed", [])
    reply = str(reply or "")
    state.update_last(reply=reply, failed=failed, interrupted=bool(interrupted), question=reply.rstrip().endswith("?"),
                      name_used=habits.uses_name(reply, USER_NAME))


# Offering to put something in a tool ("Want me to drop 'eat' on your calendar?") when they were only chatting
_TOOL_OFFER = re.compile(r"^\W*(?:(?:do you |would you )?want me to|should i|shall i|would you like me to|i can (?:drop|add|put|"
                         r"set|schedule|block|pencil|create|make|save))\b.*\b(?:calendar|timer|reminder|alarm|note|list|"
                         r"draft|e-?mail)\b", re.I)


def scrub(sentence):
    recent = state.history[-3:-1]  # (the replies before this one)
    s = habits.scrub(sentence, USER_NAME, name_recently=any(h.get("name_used") for h in recent))
    from room_agent.conversation import policy

    kind = getattr(getattr(rt.turn, "policy", None), "kind", "")
    if s and kind in (policy.CASUAL, policy.EMOTIONAL) and _TOOL_OFFER.search(s):
        return ""  # ("I'm going to the gym tomorrow" is chat: no calendar offer; they ask when they want one)
    return s


def over_cap(said):
    """`said`: the sentences of this reply already let through."""
    s = getattr(rt.turn, "strategy", None)
    return bool(s and s.max_sentences and len(said) >= s.max_sentences)


def context_lines(user_text):
    s = getattr(rt.turn, "strategy", None)
    line = s.render() if s else ""
    return [line] if line else []


def reset():
    state.reset()
    prosody.reset()
    _heard.update(feat=None, at=0.0)


def _register():
    from room_agent.actions import core

    core.register_context(context_lines, order=25)


_register()
