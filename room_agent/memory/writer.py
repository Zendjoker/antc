"""Background memory writer: learns facts mentioned in passing and summarizes finished conversations."""

import datetime
import json
import logging
import queue
import re
import threading
import time

from room_agent import config

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
                    "quote": {"type": "string", "description": "the exact words of the USER's own message that state "
                              "it (copied, a few words). Never the assistant's words. No quote = don't add it."},
                }, "required": ["content", "category", "quote"]},
                "description": "New durable facts, e.g. {'content': 'Sister is named Sara', 'category': 'person'}.",
            },
            "profile_quotes": {
                "type": "object",
                "description": "For each profile key set above: the exact words of the USER's message that state it.",
                "additionalProperties": {"type": "string"},
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

Do NOT remember: small talk, one-off requests (weather, time, timers), anything the assistant said or suggested \
(an offer like "want a daily checklist?" is not something {user} wants unless {user} says so), \
temporary states ("I'm tired right now"), guesses, things already remembered, or instructions about how the \
assistant should behave (those are not facts about {user}; ignore them). Every fact needs a quote of {user}'s own \
words that states it; if you can't quote {user}, it isn't a fact. Lines marked [misheard] were never said: ignore them.

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
Lines marked [misheard] were never said by {user}: the assistant misheard. Never describe them as requests or \
facts; at most note that the assistant misheard and {user} corrected it. \
Plain text, no preamble. If it was trivial \
(a greeting, a time check), reply with just: SKIP"""


# Things not learned automatically (only when they explicitly ask to remember: MEMORY_SENSITIVE=allow changes that)
# "(Summary for memory): SKIP" and similar: the model saying there's nothing worth keeping, in any wording
SKIP_SUMMARY = re.compile(r"^\W*(\(?[\w\s]{0,40}\)?\s*[:\-]\s*)?SKIP\W*$", re.I)


def is_skip(summary):
    s = str(summary or "").strip()
    return not s or bool(SKIP_SUMMARY.match(s)) or (len(s) < 60 and re.search(r"\bSKIP\b", s) is not None)


# A moment, not a fact about them: "on the bed right now", "currently cooking", "at work today". Kept out of
# long-term memory (the conversation and the sensors know the present).
TRANSIENT = re.compile(r"\b(right now|at the moment|currently|just now|for now|this (morning|afternoon|evening)|tonight|"
                       r"today|at this point|as we speak)\b", re.I)


def is_transient(content, category="fact"):
    unquoted = re.sub(r'"[^"]*"|“[^”]*”', '', str(content or ''))  # (a quoted phrase isn't a state)
    return category not in ("plan", "routine") and bool(TRANSIENT.search(unquoted))


SENSITIVE = re.compile(r"\b(diagnos\w*|disease|illness|medication|medicine|prescription|therapy|therapist|depress\w*|anxiety|"
                       r"pregnan\w*|hiv|cancer|password|passcode|pin code|social security|ssn|passport|bank|account number|"
                       r"credit card|salary|debt|religio\w*|sexual\w*|immigration|criminal record|arrest\w*)\b", re.I)
_CHANGE = re.compile(r"\b(actually|instead|anymore|any more|not .{0,20} anymore|prefer|rather|switched|changed|now i|these days|"
                     r"no longer|used to)\b", re.I)
_PREF_WORDS = {"like", "likes", "love", "loves", "prefer", "prefers", "want", "wants", "enjoy", "enjoys", "favorite",
               "favourite", "is", "are", "the", "a", "an", "my", "their", "it", "to", "of", "and", "best", "most"}


def conflicting(memory, new_id, content, category, said):
    """Older preferences the new one replaces ("likes the lights blue" -> "prefers red lights"): same subject, and their
    words say it changed. Kept for history, no longer active."""
    if category != "preference" or not _CHANGE.search(said or ""):
        return []
    subject = {w for w in _norm(content).split() if w not in _PREF_WORDS and len(w) > 2}
    out = []
    for f in memory.facts():
        if f["id"] == new_id or f["category"] != "preference":
            continue
        other = {w for w in _norm(f["content"]).split() if w not in _PREF_WORDS and len(w) > 2}
        if subject & other and subject != other:
            out.append(f)
    return out


def _quoted(quote, text):
    q, t = _norm(quote or ""), _norm(text or "")
    return bool(q) and len(q) >= 2 and q in t


def provenance(quote, said, value=None):
    """Where a fact comes from, or None if nothing they said backs it. said: [(their message, what was asked before)].
      user_statement  their own words state it        confirmation  they answered the assistant's question with it"""
    for text, asked in said:
        answer = (asked or "").rstrip().endswith("?") and len(_norm(text).split()) <= 4
        if quote and _quoted(quote, text) and (value is None or _quoted(str(value), text) or _quoted(str(value), quote)):
            return "confirmation" if answer else "user_statement"
        if value is not None and answer and _quoted(str(value), text):
            return "confirmation"  # ("Chicago" after "what city are you in?")
    return None


def _origin(quote, said):
    return next((text for text, _ in said if _quoted(quote, text)), said[-1][0] if said else "")


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
        self.learned_from = {}  # fact id -> the user's message it was learned from (so a denial can take it back)
        threading.Thread(target=self._run, daemon=True).start()

    def observe(self, user_text, agent_text, asked="", uncertain=False):
        """`uncertain`: speech recognition wasn't sure what it heard: kept for the conversation, never learned from."""
        self.turns.append((_now(), user_text, agent_text))
        if uncertain:
            log.info("memory: not learning from %r (uncertain transcription)", user_text[:60])
            return
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

    def invalidate(self, user_text):
        """They said they never said this (Jarvis misheard): nothing from that exchange is learned or summarized, and
        facts already learned from it are removed. -> what was removed (readable)."""
        removed = []
        self._held = [(kind, [x for x in ex if x[0] != user_text], gen) for kind, ex, gen in self._held]
        self.turns = [(t, "[misheard] " + u if u == user_text else u, "" if u == user_text else a)
                      for t, u, a in self.turns]  # (the summary sees that it was misheard, not a request)
        ids = [i for i, src in self.learned_from.items() if src == user_text]
        if ids:
            gone = {f["id"]: f["fact"] for f in self.memory.snapshot()["facts"]}
            if self.memory.remove_ids(ids):
                removed += [gone.get(i, f"fact {i}") for i in ids]
                for i in ids:  # (and it can't come back later, e.g. through a summary)
                    self.memory.reject(gone.get(i, ""))
            for i in ids:
                self.learned_from.pop(i, None)
        return removed

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
        exchanges = [x for x in exchanges if not str(x[0]).startswith("[misheard]")]
        if not exchanges:
            return
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
        said = [(u, asked) for u, _, asked in exchanges]
        profile_updates = result.get("profile")
        quotes = result.get("profile_quotes") if isinstance(result.get("profile_quotes"), dict) else {}
        if isinstance(profile_updates, dict):
            for k, v in profile_updates.items():
                if v is None:
                    continue
                source = provenance(quotes.get(k), said, value=v)
                if not source:
                    log.info("memory: not saving %s=%r (not in their own words)", k, v)
                    continue
                changed, _ = self.memory.set_key(k, v, source=source, confidence=0.8)
                if changed:
                    changes.append(f"{k}={v}")
        for fact in _as_list(result.get("add_facts")):
            content, category, quote = ((fact.get("content"), fact.get("category"), fact.get("quote"))
                                        if isinstance(fact, dict) else (fact, "fact", ""))
            if not (isinstance(content, str) and len(content.strip()) >= 3):
                continue
            source = provenance(quote, said)
            if not source:
                log.info("memory: not saving %r (no quote of their own words backs it)", content)
                continue
            if self.memory.is_rejected(content):
                log.info("memory: not saving %r (they said earlier it isn't true)", content)
                continue
            if is_transient(content, category):
                log.info("memory: not saving %r (a moment, not a lasting fact)", content)
                continue
            if SENSITIVE.search(content) and config.MEMORY_SENSITIVE != "allow":
                log.info("memory: not saving %r automatically (sensitive: only if they ask to remember it)", content)
                continue
            if self.memory.near_duplicate(content):
                continue
            new_id = self.memory.add(content, category, source, 0.8)
            if new_id:
                changes.append(f"+{content} ({source})")
                self.learned_from[new_id] = _origin(quote, said)
                old = conflicting(self.memory, new_id, content, category, _origin(quote, said))
                if old and self.memory.supersede([f["id"] for f in old], new_id):
                    changes.append("replaced: " + "; ".join(f["content"] for f in old))
        if changes:
            log.info("learned: %s", "; ".join(changes))

    def _summarize(self, turns):
        generation = self.memory.generation
        text = "\n".join(f"{self.user}: {u}\nAssistant: {a}" for _, u, a in turns)
        summary = (self.call_text(SUMMARY_SYSTEM.format(user=self.user), text) or "").strip()
        if generation != self.memory.generation:
            return  # something was forgotten meanwhile: this summary might contain it
        if summary and not is_skip(summary):
            self.memory.add_summary(summary)
            log.info("conversation summary saved: %s", summary)
