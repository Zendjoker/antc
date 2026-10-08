"""The running conversation: restoring it after a restart, keeping it trimmed, and saving each exchange."""

import logging

from room_agent import runtime as rt
from room_agent.audio.styles import strip_tags
from room_agent.config import MAX_HISTORY, RECENT_HOURS
from room_agent.memory import now_stamp
from room_agent.text import is_quiet_command, strip_stage_directions, strip_wake
from room_agent.truth import TIMER_LIMIT

log = logging.getLogger("room-agent")


def start_history():
    """Conversation history to start with: whatever you talked about recently, even before a restart.
    Quiet-mode commands and stage directions are left out, so an old "stay quiet" can't linger."""
    clean, skip_reply = [], False
    for m in rt.memory.load_recent(RECENT_HOURS):
        if m["role"] == "user":
            skip_reply = is_quiet_command(strip_wake(m["text"]))
            if not skip_reply:
                clean.append(m)
        elif TIMER_LIMIT.search(m["text"]):
            if clean and clean[-1]["role"] == "user":
                clean.pop()  # an old made-up "timers need a minute" reply would be copied again; drop it and what it answered
        elif not skip_reply and strip_stage_directions(m["text"]):
            clean.append({**m, "text": strip_stage_directions(m["text"])})
    while clean and clean[0]["role"] != "user":
        clean.pop(0)
    rt.recent = clean
    if rt.recent:
        log.info("picking up the conversation from %s (%d messages)", rt.recent[0]["time"], len(rt.recent))
    return [{"role": m["role"], "content": m["text"]} for m in rt.recent]


def reply_text(messages):
    """The words the agent said in these new history messages (Claude blocks or Ollama dicts)."""
    out = []
    for m in messages:
        if m["role"] != "assistant":
            continue
        content = m["content"]
        if isinstance(content, str):
            out.append(content)
        else:
            out += [b.get("text", "") if isinstance(b, dict) else b.text for b in content  # (dict blocks: OpenAI replies)
                    if (b.get("type") if isinstance(b, dict) else getattr(b, "type", "")) == "text"]
    return " ".join(t.strip() for t in map(strip_tags, out) if t and t.strip())


def remember_turn(user_text, agent_text, private=False):
    """After each exchange: keep it word for word (for restarts) and let the writer learn facts in the background."""
    asked = next((m["text"] for m in reversed(rt.recent) if m["role"] == "assistant"), "")
    stamp = now_stamp()
    rt.recent.append({"role": "user", "text": user_text, "time": stamp})
    if agent_text:
        rt.recent.append({"role": "assistant", "text": agent_text, "time": stamp})
    rt.recent = rt.recent[-60:]
    try:
        rt.memory.save_recent(rt.recent)
    except Exception as e:
        log.warning("couldn't save the conversation: %s", e)
    if rt.writer and not private:  # (email / calendar content stays in the service, not in long-term memory)
        # `asked`: so "Chicago" after "what city?" is understood; uncertain speech recognition is never learned from
        extra = {"uncertain": True} if getattr(rt.turn, "uncertain", False) else {}
        rt.writer.observe(user_text, agent_text, asked=asked, **extra)


def trim(history):
    """Keep context short, and always start on a plain user message (never mid tool call)."""
    while len(history) > MAX_HISTORY or (history and not isinstance(history[0]["content"], str)):
        history.pop(0)
        while history and history[0]["role"] != "user":
            history.pop(0)
