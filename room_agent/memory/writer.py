"""Background memory writer: learns facts mentioned in passing and summarizes finished conversations."""

import datetime
import json
import logging
import queue
import re
import threading
import time

from .store import CATEGORIES, FORGET_ALL, NOTHING_TO_LEARN, PROFILE_KEYS
from .text import mentions, norm as _norm, now as _now

log = logging.getLogger("room-agent")

# Something about yourself is only ever said in the first person ("I...", "my...", "call me...") or as the answer to
# a question; anything else ("what's the weather", "tell me a joke") has nothing to learn, so no model call is made.
_PERSONAL = re.compile(r"\b(i|i'm|im|i've|i'll|i'd|my|mine|myself|we|we're|our|ours|call me|name is|i am)\b", re.I)
MIN_SUMMARY_WORDS = 12  # a conversation with fewer words from you than this isn't worth summarizing


def worth_learning(user_text, asked=""):
    """Could this exchange teach anything lasting? Decided in code, before any model call."""
    text = (user_text or "").strip()
    if _norm(text) in NOTHING_TO_LEARN or not _norm(text):
        return False
    if (asked or "").rstrip().endswith("?"):
        return True  # an answer to the agent's question ("Chicago" after "what city are you in?")
    if text.endswith("?"):
        return False  # a question asks for something, it doesn't tell anything
    return bool(_PERSONAL.search(text))

UPDATE_TOOL = {
    "name": "update_memory",
    "description": "Record what changed in long-term memory after the latest exchange. Call exactly once.",
    "input_schema": {
        "type": "object",
        "properties": {
            "profile": {
                "type": "object",
                "description": "Core facts to set or change, using these keys when they apply: "
                               + ", ".join(f"{k} ({v})" for k, v in PROFILE_KEYS.items())
                               + ". Only keys that are new or changed. Empty string deletes a key that's no longer true.",
                "additionalProperties": {"type": "string"},
            },
            "add_facts": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "content": {"type": "string", "description": "short, specific, third person, no pronouns"},
                    "category": {"type": "string", "enum": list(CATEGORIES[1:])},
                }, "required": ["content", "category"]},
                "description": "New durable facts, e.g. {'content': 'Sister is named Sara', 'category': 'person'}.",
            },
            "remove_fact_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Ids of remembered facts that are now wrong or replaced by a newer one.",
            },
        },
        "required": ["profile", "add_facts", "remove_fact_ids"],
    },
}

EXTRACT_SYSTEM = """You maintain the long-term memory of a voice assistant that lives in {user}'s room. \
After each exchange you decide what, if anything, is worth remembering permanently.

Remember durable, useful things {user} reveals about themselves and their life: where they live, their name, \
people (family, friends, partner, coworkers, pets) and their names, job or school, schedule and routines, \
preferences (food, music, teams, hobbies, units like Celsius), goals and projects, important dates, their home, \
rooms and devices. Use the question the assistant had just asked to understand short answers: "Chicago" after \
"what city are you in?" means home_location is Chicago.

Do NOT remember: small talk, one-off requests (weather, time, timers), anything the assistant said, \
temporary states ("I'm tired right now"), guesses, things already remembered, or instructions about how the \
assistant should behave (those are not facts about {user}; ignore them).

Turn relative times into real dates using today's date, so facts stay true later: "this weekend" -> \
"the weekend of Oct 10-11, 2026", "two weeks ago" -> "around Sep 21, 2026". Once a dated plan has passed, \
remove it.
If something new contradicts a remembered fact (they moved, changed jobs, broke up), remove the old fact \
by id and add the new one. Core facts go in the profile (home_location, units, ...), not in facts.
Facts are written third person without pronouns ("Works night shifts", "Sister is named Sara"); don't assume \
{user}'s gender.
If something personal and lasting is mentioned in passing ("I started guitar two weeks ago"), save it. Plain \
requests and chit-chat have nothing to save: then call update_memory with empty values. Always call it exactly once."""

SUMMARY_SYSTEM = """Summarize this conversation between {user} and their room assistant in one or two short \
sentences, for the assistant's own memory: what was discussed, anything decided or promised, open threads to \
follow up on. Refer to {user} by name, don't assume pronouns. Leave out passing moods and how {user} felt in the \
moment (tired, annoyed, frustrated, excited): those aren't memories, unless {user} asked you to remember them. \
Write "tried to open an app that isn't installed", not "got frustrated trying to open an app". \
Plain text, no preamble. If it was trivial \
(a greeting, a time check), reply with just: SKIP"""


def _as_list(value):
    """Models sometimes send a JSON-encoded string where a list was asked for."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [value]
    return value if isinstance(value, list) else []


class MemoryWriter:
    """Background worker: learns facts mentioned in passing and summarizes conversations, without slowing the
    agent down. (Explicit "remember this" requests don't come here: they're written synchronously, so the agent
    only confirms after the write is verified.)"""

    def __init__(self, memory, user_name, call_tool, call_text, defer=False):
        self.memory = memory
        self.user = user_name
        self.call_tool = call_tool
        self.call_text = call_text
        self.defer = defer
        self.q = queue.Queue()
        self._held = []
        self.turns = []  # (time, user text, agent text) for this run, for summaries
        self.last_summarized = 0
        threading.Thread(target=self._run, daemon=True).start()

    def observe(self, user_text, agent_text, asked=""):
        self.turns.append((_now(), user_text, agent_text))
        if not worth_learning(user_text, asked):
            return
        job = ("extract", [(user_text, agent_text, asked)], self.memory.generation)
        self._held.append(job) if self.defer else self.q.put(job)

    def mark(self):
        return len(self.turns)

    def conversation_ended(self, since):
        # everything learned in this conversation goes to the model in ONE call
        current = [x for job in self._held if job[-1] == self.memory.generation for x in job[1]]
        if current:
            self.q.put(("extract", current, self.memory.generation))
        self._held = []
        turns = self.turns[max(since, self.last_summarized):]
        self.last_summarized = len(self.turns)
        words = sum(len(u.split()) for _, u, _ in turns)
        if turns and words >= MIN_SUMMARY_WORDS:
            self.q.put(("summarize", turns, self.memory.generation))
        elif turns:
            log.debug("conversation too short to summarize (%d words)", words)

    def forgot(self, term=""):
        """After a forget: held jobs and unsummarized turns that mention it are dropped (others are kept)."""
        if term in FORGET_ALL or not term:
            self._held = []
            self.last_summarized = len(self.turns)
            return
        self._held = [(kind, [x for x in ex if not (mentions(x[0], term) or mentions(x[1], term))], gen)
                      for kind, ex, gen in self._held]
        self.turns = [t for t in self.turns if not (mentions(t[1], term) or mentions(t[2], term))]
        self.last_summarized = min(self.last_summarized, len(self.turns))

    def flush(self, timeout):
        end = time.time() + timeout
        while self.q.unfinished_tasks and time.time() < end:
            time.sleep(0.1)

    def _run(self):
        while True:
            job = self.q.get()
            try:
                if job[-1] != self.memory.generation or not self.memory.available:
                    continue
                if job[0] == "extract":
                    self._extract(job[1])
                else:
                    self._summarize(job[1])
            except Exception as e:
                log.warning("memory update failed: %s", e)
            finally:
                self.q.task_done()

    def _extract(self, exchanges):
        """exchanges: [(user text, agent text, what the agent asked just before)], one call for all of them."""
        generation = self.memory.generation
        snap = self.memory.snapshot()
        known = "\n".join(f"[{f['id']}] {f['fact']}" for f in snap["facts"]) or "(none)"
        profile = json.dumps(snap["profile"], ensure_ascii=False) if snap["profile"] else "(empty)"
        today = datetime.datetime.now().strftime("%A %B %d, %Y")
        shown = "\n\n".join(f"Assistant (just before): {asked or '(nothing)'}\n{self.user}: {user_text}\nAssistant: {agent_text}"
                            for user_text, agent_text, asked in exchanges)
        prompt = (f"Today is {today}.\nProfile: {profile}\nRemembered facts:\n{known}\n\n"
                  f"{'Latest exchange' if len(exchanges) == 1 else 'Exchanges from the conversation, in order'}:\n{shown}")
        result = self.call_tool(EXTRACT_SYSTEM.format(user=self.user), prompt, UPDATE_TOOL)
        if not isinstance(result, dict) or generation != self.memory.generation:
            return
        changes = []
        valid_ids = {f["id"] for f in snap["facts"]}
        ids = [i for i in _as_list(result.get("remove_fact_ids")) if isinstance(i, int) and i in valid_ids]
        if ids and self.memory.remove_ids(ids):
            changes.append(f"-{len(ids)} outdated")
        profile_updates = result.get("profile")
        if isinstance(profile_updates, dict):
            for k, v in profile_updates.items():
                if v is None:
                    continue
                changed, _ = self.memory.set_key(k, v, source="learned", confidence=0.8)
                if changed:
                    changes.append(f"{k}={v}")
        for fact in _as_list(result.get("add_facts")):
            content, category = (fact.get("content"), fact.get("category")) if isinstance(fact, dict) else (fact, "fact")
            if isinstance(content, str) and len(content.strip()) >= 3 and self.memory.add(content, category, "learned", 0.8):
                changes.append(f"+{content}")
        if changes:
            log.info("learned: %s", "; ".join(changes))

    def _summarize(self, turns):
        generation = self.memory.generation
        text = "\n".join(f"{self.user}: {u}\nAssistant: {a}" for _, u, a in turns)
        summary = (self.call_text(SUMMARY_SYSTEM.format(user=self.user), text) or "").strip()
        if generation != self.memory.generation:
            return  # something was forgotten meanwhile: this summary might contain it
        if summary and not summary.upper().startswith("SKIP"):
            self.memory.add_summary(summary)
            log.info("conversation summary saved: %s", summary)
