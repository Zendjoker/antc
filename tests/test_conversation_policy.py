"""Response policy, multi-turn: what kind of turn it is and what kind of reply that calls for, through the real
conversation code with a scripted model (no API calls, no PC actions). Includes unseen phrasings, a misunderstanding
and correction, a joke, an interruption, returning to an earlier topic, and the "Want me to...?" habit.

Run:  .venv\\Scripts\\python -m tests.test_conversation_policy
"""

import time

from tests.harness import setup_env

setup_env()

from room_agent import runtime as rt  # noqa: E402
from room_agent.conversation import policy as P  # noqa: E402
from tests.harness import Checker, Conversation  # noqa: E402

t = Checker()
convo = Conversation()


def turn(text, reply="Okay.", scripts=None, calls=None):
    convo.say(text, calls=calls, reply=reply, scripts=scripts)
    first = convo.requests[0] if convo.requests else {}
    system = "\n".join(m["content"] for m in first.get("messages", []) if m["role"] == "system")
    return rt.turn.policy, system


print("Kinds of turns (including phrasings no rule was written for):")
cases = [("could you dim the lights a bit", P.COMMAND), ("set a timer for 12 minutes", P.COMMAND),
         ("pause the music", P.COMMAND), ("whats the weather like tomorrow", P.QUESTION), ("is the door open?", P.QUESTION),
         ("how long did I sleep", P.QUESTION), ("I just got back from the gym", P.CASUAL), ("lol that was a good one", P.CASUAL),
         ("ok thanks, bye", P.ENDING), ("that's all for now", P.ENDING), ("good night jarvis", P.ENDING)]
for text, want in cases:
    p, _ = turn(text)
    t.check(f"{text!r} -> {want}", p.kind == want, (p.kind, p.why))
p, _ = turn("ugh I'm so done with today, nothing works")
t.check("'ugh I'm so done with today...' -> emotional expression (from the tone reading, not a keyword)",
        p.kind == P.EMOTIONAL, (p.kind, p.why))

print("What the model is told:")
p, system = turn("lol you're ridiculous")
t.check("casual / joking -> told no offers of help, no 'want me to'", "turn_type: casual conversation" in system
        and "no 'want me to...'" in system, system[-500:])
p, system = turn("set a timer for 3 minutes", calls=[("set_timer", {"seconds": 180})], reply="Three minutes, go.")
t.check("command -> told to do it now and not ask permission", "turn_type: direct command" in system
        and "Don't ask 'want me to...?'" in system)

print("Clarification and continuation:")
turn("set a timer", scripts=[{"text": "Sure, for how long?"}])
p, system = turn("ten minutes", calls=[("set_timer", {"seconds": 600})], reply="Ten minutes, starting now.")
t.check("an answer to Jarvis's question -> clarification (never asked again)", p.kind == P.CLARIFICATION, (p.kind, p.why))
p, system = turn("do it again", calls=[("set_timer", {"seconds": 600})], reply="Another ten minutes.")
t.check("'do it again' -> continuation, resolved to the last action", p.kind == P.CONTINUATION and "set_timer" in p.reference,
        (p.kind, p.reference))
t.check("...and the model is told what 'it / again' means", "most likely means: set_timer" in system, system[-400:])

print("The 'Want me to...?' habit:")
from room_agent.tools import timers  # noqa: E402

timers.set_timer(900, label="pasta")
convo.say("cancel the pasta timer",
          scripts=[{"text": "Want me to cancel the pasta timer?"},
                   {"tools": [("cancel_timer", {"label": "pasta", "confidence": 0.95})]}, {"text": "Done, it's cancelled."}])
said = " ".join(convo.said())
t.check("after a clear command, 'Want me to...?' is never spoken; it's done instead", "Want me to" not in said
        and "cancelled" in said, convo.said())
t.check("...and the pasta timer really is gone", not any(i["label"] == "pasta" for i in timers._items.values()))
convo.say("hey how's it going", scripts=[{"text": "Pretty good! Want me to put some music on?"}])
t.check("in casual talk an offer isn't treated as a refused command (no false blocking)",
        "Want me to put some music on?" in " ".join(convo.said()), convo.said())

print("Misunderstanding, correction, and going back to a topic:")
turn("remind me about the dailies", scripts=[{"text": "What should be on your dailies list?"}])
p, system = turn("that's not what I said", scripts=[{"text": "My bad, I misheard you."}])
t.check("misunderstanding -> correction recognized", p.kind == P.CORRECTION, (p.kind, p.why))
t.check("...the correction line is in front of the model", "DISCARDED" in system)
p, system = turn("anyway, back to the timer, how long is left?", reply="About nine minutes.")
t.check("returning to an earlier topic -> a normal question again, no stale correction", p.kind == P.QUESTION
        and "DISCARDED" not in system, (p.kind, system[-200:]))

print("Interruption and wording:")
rt.turn_interrupted = True
p, system = turn("no wait, make it twenty minutes", calls=[("set_timer", {"seconds": 1200})], reply="Twenty it is.")
t.check("after an interruption the next turn is read fresh (a command)", p.kind in (P.COMMAND, P.CONTINUATION), (p.kind, p.why))
rt.turn_interrupted = False
rt.recent[:] = [{"role": "assistant", "text": t_, "time": ""} for t_ in ("Got it, done.", "Got it, all set.", "Got it.")]
lines = "\n".join(P.context_lines("thanks"))
t.check("a worn-out opener ('got it') is flagged so replies vary", "'got it'" in lines, lines)
t.done("CONVERSATION POLICY")
