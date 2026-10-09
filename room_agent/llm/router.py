"""Picks the model for each turn, in code (no extra model call to decide):

  cheap model (OpenAI, LLM_DEFAULT)  clear commands, status checks and simple tool use
  conversation model (OpenAI,        natural conversation, open questions, ambiguous requests, project / business
    OPENAI_CONVERSATION_MODEL)       discussions (conversational(): from the turn's policy and intent, no model call)
  smart model (Claude, LLM_SMART)    you ask for it ("ask Claude", "think hard"), or the request is long/complex,
                                     DEEP reasoning, or the cheap model fails before saying anything
  local model (Ollama)               LLM_PROVIDER=ollama, or today's paid budget is used up
"""

import datetime
import logging
import re

import requests

from room_agent import runtime as rt
from room_agent import trace
from room_agent.audio.speaker import finish_speaking, say
from room_agent.config import (BUDGET_FALLBACK, LEVEL_DEEP, LLM_COMPLEX_TOPICS, LLM_COMPLEX_WORDS, LLM_DEFAULT,
                               LLM_PROVIDER, LLM_SMART, LLM_SMART_TRIGGERS, MODEL, OLLAMA_MODEL_DEEP, OLLAMA_URL, OPENAI_KEY,
                               OPENAI_CONVERSATION_MODEL, OPENAI_CONVERSATION_REASONING, OPENAI_MODEL)
from room_agent.llm.budget import budget
from room_agent.llm.claude import ask_claude
from room_agent.llm.ollama import ask_ollama
from room_agent.llm.openai_backend import ask_openai

log = logging.getLogger("room-agent")
NAMES = {"openai": OPENAI_MODEL, "claude": MODEL}


def choose(text):
    """(provider, why) for this request."""
    t = " ".join((text or "").lower().split())
    default = LLM_DEFAULT if (LLM_DEFAULT != "openai" or OPENAI_KEY) else "claude"
    if default == LLM_SMART:
        return default, "default"
    if any(trigger in t for trigger in LLM_SMART_TRIGGERS):
        return LLM_SMART, "you asked"
    if len(t.split()) >= LLM_COMPLEX_WORDS:
        return LLM_SMART, "long request"
    if any(topic in t for topic in LLM_COMPLEX_TOPICS):
        return LLM_SMART, "complex request"
    why = escalation(t)
    if why:
        return LLM_SMART, why
    return default, "default"


# Talking something through (not a command): ideas, plans, strategy, opinions, a business or project.
DISCUSSION = re.compile(r"\b(?:i think|i feel|what do you think|your (?:opinion|take|thoughts?)|ideas?|brainstorm\w*|"
                        r"strateg\w*|plans?|planning|project|business|clients?|customers?|money|grow\w*|should (?:i|we)|"
                        r"what if|how (?:could|can|should|would) (?:i|we)|is it worth|worth it|not working|makes? sense|"
                        r"pros and cons|figure out|advice)\b", re.I)
# What a clear command or status check acts on (understand.TurnIntent.scope / .op): these stay on the cheap model.
ACTION_SCOPES = {"tab", "page", "app", "window", "device", "media", "volume", "timer", "mission", "memory"}
ACTION_OPS = {"open", "close", "switch", "search", "set", "raise", "lower", "turn_on", "turn_off", "play", "pause",
              "resume", "stop", "cancel", "cancel_for_good", "start", "find", "remember", "report"}
OPEN_QUESTION_WORDS = 10  # a question at least this long, about no device / app / timer, is an open question


def conversational(text):
    """Why this turn deserves the conversation model, or "" for the cheap one. Decided from what code already read:
    the turn's kind (conversation/policy.py) and intent (cognition/understand.read_turn). A clear device / media / app
    command or a short status question stays cheap; answers to Jarvis's own questions and sign-offs too."""
    from room_agent.conversation import policy

    t = " ".join(str(text or "").split())
    kind = getattr(getattr(rt.turn, "policy", None), "kind", "")
    intent = getattr(rt.turn, "intent", None)
    if not t or kind in (policy.CLARIFICATION, policy.CONTINUATION, policy.ENDING):
        return ""
    discussing = bool(DISCUSSION.search(t))
    acting = intent is not None and (intent.scope in ACTION_SCOPES or intent.op in ACTION_OPS)
    if acting and not (discussing and kind != policy.COMMAND):
        return ""
    words = len(t.split())
    if discussing and words >= 4:
        return "project discussion"
    if kind in (policy.CASUAL, policy.EMOTIONAL, policy.CORRECTION) and words >= 3:
        return "conversation"
    if kind == policy.QUESTION and words >= OPEN_QUESTION_WORDS:
        return "open question"
    if kind == policy.COMMAND and intent is not None and intent.op is None and intent.scope is None and words >= 5:
        return "open request"
    return ""


def escalation(text):
    """ROUTER_ESCALATION (off by default: it costs more): measured difficulty from the request and this goal's failures
    -> the reason to use the smart model, or "". No model call."""
    from room_agent import config

    if not config.ROUTER_ESCALATION:
        return ""
    try:
        from room_agent import cognition
        from room_agent.cognition import understand

        fams = understand.from_words(text).families
        if len(fams) >= 3:
            return "escalated: needs several capabilities (" + ", ".join(fams) + ")"
        if "coding" in fams and len(fams) >= 2:
            return "escalated: coding combined with other work"
        if sum(cognition._state.get("failures", {}).values()) >= 2:
            return "escalated: repeated failures in this goal"
    except Exception:  # noqa: BLE001
        return ""
    return ""


def ask(history):
    """Answer the last message in `history`: speaks the reply and appends it (and any tool calls) to `history`."""
    budget.start_turn()
    level = getattr(rt.turn, "level", None)  # (cognition/levels.py: how much reasoning this request deserves)
    if LLM_PROVIDER == "ollama":
        return ask_ollama(history, model=OLLAMA_MODEL_DEEP if level == "DEEP" and OLLAMA_MODEL_DEEP else None)
    if budget.exceeded():
        return over_budget(history)
    provider, why = choose(rt.turn_text)
    if level == "DEEP" and LEVEL_DEEP == "smart" and provider != LLM_SMART:
        provider, why = LLM_SMART, "deep reasoning"
    model, reasoning = None, None
    if provider == "openai" and OPENAI_CONVERSATION_MODEL and OPENAI_CONVERSATION_MODEL != OPENAI_MODEL:
        talk = conversational(rt.turn_text)
        if talk:
            model, reasoning, why = OPENAI_CONVERSATION_MODEL, OPENAI_CONVERSATION_REASONING, talk
    name = model or NAMES.get(provider, provider)
    log.info("model: %s (%s)", name, why)
    trace.note("MODEL", f"{name} ({why})")
    if provider != "openai":
        return ask_claude(history)
    spoken_before, size_before = rt.spoken_count, len(history)
    try:
        return ask_openai(history, model=model, reasoning=reasoning)
    except Exception as e:
        if rt.spoken_count != spoken_before:
            raise  # it already said something: answering again from the top would repeat it
        del history[size_before:]
        log.warning("OpenAI failed (%s: %s), Claude answers instead", e.__class__.__name__, str(e)[:120])
        trace.note("MODEL", f"{MODEL} (fallback: OpenAI failed)")
        return ask_claude(history)


def _ollama_up():
    try:
        return requests.get(f"{OLLAMA_URL}/api/tags", timeout=2).ok
    except requests.RequestException:
        return False


def _plain(history):
    """Text-only copy of the conversation for the local model (tool details left out)."""
    out = []
    for m in history:
        c = m["content"]
        if not isinstance(c, str):
            c = " ".join(getattr(b, "text", None) or (b.get("text", "") if isinstance(b, dict) else "") for b in c).strip()
        if c:
            out.append({"role": m["role"], "content": c})
    return out


def over_budget(history):
    """Today's paid budget is used up: say so once, then use the free local model, or don't answer."""
    log.warning("daily budget reached: $%.2f of $%.2f", budget.total(), budget.limit)
    today = datetime.date.today().isoformat()
    local = BUDGET_FALLBACK == "ollama" and _ollama_up()
    if budget.warned_on != today:
        budget.warned_on = today
        say("Heads up, I've hit today's spending limit, so I'm switching to the free local model."
            if local else "I've hit today's spending limit for the AI, so I can't answer until tomorrow.")
        if not local:
            finish_speaking()
    elif not local:
        say("I'm still over today's spending limit, so I can't answer that until tomorrow.")
    if not local:
        return
    temp = _plain(history)
    n = len(temp)
    ask_ollama(temp)
    for m in temp[n:]:  # keep what the local model said, as plain text, in the real history
        if m["role"] == "assistant" and isinstance(m.get("content"), str) and m["content"].strip():
            history.append({"role": "assistant", "content": m["content"]})
