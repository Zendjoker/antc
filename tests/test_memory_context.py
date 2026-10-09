"""Memory and context, offline: the real name vs the form of address, memories found by topic and recent context (not
just shared words), summaries chosen by relevance and open threads (not just the newest two), and an explicit
response-style request kept.

Run:  .venv\\Scripts\\python -m tests.test_memory_context
"""

import sqlite3

from tests.harness import setup_env

setup_env()

from room_agent import runtime as rt  # noqa: E402
from room_agent.conversation import policy  # noqa: E402
from room_agent.memory import Memory  # noqa: E402
from room_agent.tools.memory_tools import remember  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
mem = rt.memory

print("\nName vs form of address")
mem.set_key("name", "Adam", source="explicit")
rt.turn_text = "Call me boss from now on"
out = remember({"content": "Prefer to be called 'boss'", "key": "name", "value": "boss", "category": "profile"})
who = mem.identity()
t.check("'call me boss' (stored by the model as key=name) keeps the name Adam and saves boss as the form of address",
        who["name"] == "Adam" and who["address_as"] == "boss" and mem.get("name") == "Adam", (who, out))
t.check("...and says so", out.startswith("OK") and "boss" in out and "Adam" in out, out)
mem.set_key("name", "Bob", source="learned")
t.check("a background guess never replaces the name they gave", mem.identity()["name"] == "Adam")
rt.turn_text = "actually my name is Adem"
remember({"content": "name Adem", "key": "name", "value": "Adem"})
t.check("their own 'my name is ...' does change it", mem.identity()["name"] == "Adem", mem.identity())
mem.set_key("name", "Adam", source="explicit", override=True)

print("\nA database where 'boss' already replaced 'Adam' (as on Oct 9)")
legacy = Memory(rt.memory.path.parent / "legacy.db") if hasattr(rt.memory, "path") else None
if legacy is not None:
    legacy.set_key("name", "Adam", source="explicit")
    legacy.set_key("name", "boss", source="explicit", override=True)
    con = sqlite3.connect(legacy.path)
    before = con.execute("SELECT * FROM memories ORDER BY id").fetchall()
    who = legacy.identity()
    after = con.execute("SELECT * FROM memories ORDER BY id").fetchall()
    t.check("it reads as name Adam, called boss, without changing a row", who["name"] == "Adam"
            and who["address_as"] == "boss" and before == after, who)

print("\nMemories by topic, not only by shared words")
for fact in ("Adam wants to build websites for local restaurants as a side business",
             "Adam's sister Leila lives in Paris", "Adam likes to be teased a little when he wakes up (a wake-up roast)"):
    mem.add(fact, category="fact", source="user_statement")


def found(q, context=""):
    return " | ".join(f["content"] for f in mem.relevant(q, context=context))


t.check("'help me get more clients' finds the side business", "side business" in found("Help me figure out how to get "
                                                                                       "more clients."))
t.check("'I miss my sister' finds Leila", "Leila" in found("I really miss my sister."))
t.check("a device command brings in neither", "side business" not in found("Turn off the music.")
        and "Leila" not in found("Turn off the music."), found("Turn off the music."))
t.check("a short follow-up keeps the earlier topic ('Yeah, Daly City' after talk of restaurant websites)",
        "side business" in found("Yeah, Daly City", context="I think we could make money building websites"))
t.check("an emotional turn doesn't pull in the wake-up roast", "roast" not in found("I'm really stressed about money lately."))
for i in range(6):
    mem.add(f"Adam's client number {i} pays late", category="fact", source="learned")
topic_only = [f for f in mem.relevant("I want more customers") if "client" not in f["content"].lower()]
t.check("topic-only matches are bounded (at most 3)", len(topic_only) <= 3, [f["content"] for f in topic_only])

print("\nSummaries by relevance and open threads")
for s in ("Adam and Jarvis decided to focus the web-design side business on Daly City restaurants with no website, "
          "starting with three. Open thread: pick the first three.",
          "Adam asked for the weather; nothing else.", "Adam changed the volume a few times."):
    mem.add_summary(s)


def sums(q, back=False):
    return " | ".join(s["summary"] for s in mem.relevant_summaries(q, n=3, looking_back=back))


t.check("'what did we decide about the restaurant project?' gets the older decision", "Daly City" in sums(
    "Do you remember what we decided about the restaurant project?", back=True))
t.check("'let's continue the project' gets the open thread", "Open thread" in sums(
    "Let's continue the project we were working on.", back=True))
t.check("the newest summary is always there", "volume" in sums("Do you remember what we decided?", back=True))
t.check("an unrelated request doesn't drag the old decision in", "Daly City" not in sums("what time is it"),
        sums("what time is it"))

print("\nHow they asked Jarvis to talk")
rt.reply_style, rt.ack_word = None, ""
policy.style_request("Just say alright, you don't have to repeat everything that I say.")
t.check("'just say alright, you don't have to repeat everything' -> minimal, 'Alright'", rt.reply_style == "minimal"
        and rt.ack_word == "Alright" and policy.minimal_replies() == "Alright")
rt.reply_style, rt.ack_word = None, ""
t.check("...kept as a lasting preference (a new session still has it)", policy.minimal_replies() == "Alright")
policy.style_request("talk normally again")
t.check("'talk normally again' undoes it", policy.minimal_replies() == "" and rt.reply_style is None)
policy.style_request("for now just say ok")
t.check("'for now just say ok': this session only, 'Okay'", rt.reply_style == "minimal" and rt.ack_word == "Okay")
rt.reply_style, rt.ack_word = None, ""
t.check("...not kept after the session", policy.minimal_replies() == "")
for text in ("don't repeat that song", "stop talking about work", "I just say alright to everyone"):
    rt.reply_style = None
    policy.style_request(text)
    t.check(f"{text!r} isn't a style request", rt.reply_style is None)
t.done("MEMORY CONTEXT")
