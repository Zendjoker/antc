"""Memory lifecycle, offline (fake memory model, temp database): provenance, a preference that changes, a false inference
that must never come back, relevance ranking, near-duplicates, expiry of dated plans, the sensitive-information policy,
user-requested forgetting, and persistence across a restart.

Run:  .venv\\Scripts\\python -m tests.test_memory_lifecycle
"""

import datetime

from tests.harness import setup_env

TMP = setup_env()

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.memory import MemoryWriter  # noqa: E402
from room_agent.memory.store import Memory  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()


class FakeMemoryModel:
    def __init__(self):
        self.propose, self.summary = {}, "SKIP"

    def tool(self, system, prompt, tool):
        return self.propose

    def text(self, system, prompt):
        return self.summary


mm = FakeMemoryModel()
w = MemoryWriter(rt.memory, "Adam", mm.tool, mm.text)


def learn(user, fact, quote, category="preference", asked=""):
    mm.propose = {"profile": {}, "remove_fact_ids": [], "add_facts": [{"content": fact, "category": category, "quote": quote}]}
    w.observe(user, "Got it.", asked=asked)
    w.flush(5)


def facts():
    return {f["fact"]: f for f in rt.memory.snapshot()["facts"]}


print("Provenance:")
learn("I like the lights blue", "Likes the lights blue", "I like the lights blue")
t.check("an explicit statement is stored as the user's own words", facts().get("Likes the lights blue", {}).get("source") == "user_statement",
        facts())
learn("no thanks", "Wants a morning playlist", "morning playlist", asked="Want me to make you a morning playlist?")
t.check("Jarvis's own suggestion never becomes a preference", "Wants a morning playlist" not in facts())

print("A preference that changes:")
learn("Actually, I prefer red lights", "Prefers red lights", "I prefer red lights")
f = facts()
t.check("'Actually, I prefer red' replaces 'likes blue' (no contradictory facts)", "Prefers red lights" in f
        and "Likes the lights blue" not in f, list(f))
old = rt.memory._q("SELECT superseded_by, active FROM memories WHERE content='Likes the lights blue'")
t.check("...the old one is kept for history, inactive and pointing at the new one", old and old[0]["active"] == 0
        and old[0]["superseded_by"], [dict(r) for r in old])
learn("I also like jazz", "Likes jazz", "I also like jazz")
t.check("an unrelated new preference doesn't replace anything", "Likes jazz" in facts() and "Prefers red lights" in facts())

print("A false inference never comes back:")
rt.memory.reject("Wants a daily checklist")
learn("I want my daily checklist ready", "Wants a daily checklist", "daily checklist")
t.check("'I never said I wanted a daily checklist' -> it can't be learned again (e.g. via a summary)",
        "Wants a daily checklist" not in facts(), list(facts()))
learn("I need a checklist for daily stuff", "Wants a checklist for daily stuff", "checklist for daily stuff")
t.check("...nor the same thing reworded", not any("checklist" in k.lower() for k in facts()), list(facts()))

print("Duplicates, relevance, expiry, sensitive things:")
learn("I like jazz music a lot", "Likes jazz", "I like jazz music")
t.check("the same fact said again isn't stored twice", sum(1 for k in facts() if k.lower() == "likes jazz") == 1)
rt.memory.add("Sister is named Sara", "person", "user_statement")
rt.memory.add("Sister might live in Boston", "person", "inference")
rel = [r["content"] for r in rt.memory.relevant("what's my sister up to")]
t.check("relevance: only memories about the request come back, trusted ones first", rel[:1] == ["Sister is named Sara"]
        and "Likes jazz" not in rel, rel)
past = (datetime.date.today() - datetime.timedelta(days=3)).strftime("%B %d, %Y")
future = (datetime.date.today() + datetime.timedelta(days=10)).strftime("%B %d, %Y")
rt.memory.add(f"Dentist appointment on {past}", "plan", "user_statement")
rt.memory.add(f"Trip to Tokyo on {future}", "plan", "user_statement")
n = rt.memory.expire_plans()
t.check("a dated plan that has passed expires; a future one stays", n == 1 and not any("Dentist" in k for k in facts())
        and any("Tokyo" in k for k in facts()), (n, list(facts())))
learn("I'm on anxiety medication", "Takes anxiety medication", "anxiety medication", category="fact")
t.check("sensitive health details aren't learned automatically", "Takes anxiety medication" not in facts())
config.MEMORY_SENSITIVE = "allow"
learn("I'm on anxiety medication", "Takes anxiety medication", "anxiety medication", category="fact")
t.check("...unless the user's policy allows it (MEMORY_SENSITIVE=allow)", "Takes anxiety medication" in facts())
config.MEMORY_SENSITIVE = "explicit"

print("Forgetting and restart:")
gone = rt.memory.forget("jazz")
t.check("'forget that I like jazz' removes it", gone and "Likes jazz" not in facts(), gone)
again = Memory(config.MEMORY_DB)
kept = {f["content"] for f in again.facts()}
t.check("after a restart: what was learned is still there, what was replaced or forgotten isn't",
        "Prefers red lights" in kept and "Likes the lights blue" not in kept and "Likes jazz" not in kept, kept)
t.check("...and the rejected inference is still blocked", again.is_rejected("Wants a daily checklist"))
t.done("MEMORY LIFECYCLE")
