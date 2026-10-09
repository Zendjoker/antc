""""I didn't say that": when they deny what Jarvis thought it heard, the misheard request is discarded everywhere.

    memory      the misheard exchange never reaches long-term memory or the conversation summary, and any fact already
                learned from it is removed (memory/writer.py: invalidate)
    dialogue    the saved transcript marks it "[misheard]", so after a restart the model still sees it never happened
    state       a request being filled in (actions/pending.py) and the current goal (cognition/) are cancelled
    this reply  a context line tells the model: acknowledge briefly, don't continue it, don't ask about it again

Decided in code from their words (no model call), and logged as a STATE change.
"""

import logging
import re

from room_agent import runtime as rt

log = logging.getLogger("room-agent")
MISHEARD = "[misheard] "

DENIAL = re.compile(
    r"\b(i\s+(didn'?t|did not|never|ain'?t)\s+(say|said|ask|asked|mention|mentioned|want|wanted|tell|told)"
    r"|that'?s\s+not\s+what\s+i\s+(said|asked|meant|say)|not\s+what\s+i\s+(said|asked)|you\s+misheard"
    r"|you\s+heard\s+(me\s+)?wrong|you\s+got\s+(it|that)\s+wrong|i\s+said\s+no\s+such\s+thing|none\s+of\s+(this|that)\s+is\s+what"
    r"|where\s+did\s+you\s+get\s+that|who\s+said\s+anything\s+about)\b", re.I)


ABOUT = re.compile(r"\b(?:tell|told|ask|asked|want|wanted)\s+(?:you\s+)?(?:to\s+|for\s+)?(?P<what>[^.!?;]+)", re.I)
STOP = set("a an the to you your me my it this that them of for and or but so just all any some i".split())


def _content(text):
    return {w[:6] for w in re.findall(r"[a-z0-9']+", str(text or "").lower()) if w not in STOP and len(w) > 1}


def is_denial(text, heard=""):
    """'I didn't say that' / 'you misheard'. "I didn't tell you to open 21 best restaurants" is NOT one when what it
    names isn't in what Jarvis heard them say last: that's a complaint about what Jarvis DID, and their request stands."""
    m = DENIAL.search(text or "")
    if not m:
        return False
    about = ABOUT.search(text[m.start():m.end() + 80]) if re.search(r"tell|told|ask|want", m.group(0), re.I) else None
    if about and heard:
        named = _content(re.split(r"\b(?:i|but)\b", about.group("what"), flags=re.I)[0])
        if len(named) >= 2 and len(named & _content(heard)) < len(named) / 2:
            return False
    return True


def misheard_text():
    """The last thing they supposedly said before this one (what the denial is about), or ''."""
    users = [m for m in rt.recent if m["role"] == "user"]
    return users[-1]["text"] if users else ""


def on_user_turn(text):
    """Called at the start of every turn. -> the misheard text it discarded, or ''."""
    wrong = misheard_text()
    if not is_denial(text, wrong):
        return ""
    if not wrong or wrong.startswith(MISHEARD):
        return ""
    from room_agent.actions import pending

    # 1. state: nothing started from the misheard words goes on
    if rt.pending is not None:
        pending.cancel("they said they never asked for it")
    try:
        from room_agent import cognition
        from room_agent.cognition.goal import CANCELLED

        goal = cognition.active_goal()
        if goal is not None:
            goal.set(CANCELLED, "misheard: they never asked for it")
    except Exception as e:
        log.debug("corrections: no goal to cancel (%s)", e)
    # 2. memory: the misheard exchange is invalidated (not learned, not summarized; facts from it removed)
    removed = []
    if rt.writer is not None:
        removed = rt.writer.invalidate(wrong)
    # 3. the saved dialogue keeps it, marked, so a restart doesn't bring it back as a real request
    for m in reversed(rt.recent):
        if m["role"] == "user" and m["text"] == wrong:
            m["text"] = MISHEARD + wrong
            break
    try:
        rt.memory.save_recent(rt.recent)
    except Exception as e:
        log.debug("corrections: couldn't save the marked dialogue (%s)", e)
    rt.turn.correction = wrong
    log.info("STATE: correction: they never said %r -> request discarded%s", wrong[:80],
             f", removed from memory: {'; '.join(removed)}" if removed else "")
    return wrong


def context_lines(user_text):
    wrong = getattr(rt.turn, "correction", "")
    if not wrong:
        return []
    return [f"- correction: they say you misheard them; they never said {wrong[:160]!r}. That request is DISCARDED: don't do "
            "it, don't continue or mention it again, and it isn't true about them. Acknowledge briefly (e.g. 'my bad, I "
            "misheard you'), then answer what they say now; only ask what they meant if you really can't tell."]


def register():
    from room_agent.actions import core

    core.register_context(context_lines, order=15)
