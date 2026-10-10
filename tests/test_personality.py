"""The personality profile (social/personality.py): a confident, loyal, street-smart friend with big-brother energy by
default, shaped per reply by the moment, and never at the expense of the truth, safety or a clear confirmation.

    profile + persona   the street profile fills persona.md's slots; the friend profile reads exactly as it always did;
                        the fixed prompt stays identical from request to request (it's cached)
    this reply          casual talk, a push, an excuse, frustration, something serious, a command, a question, a win,
                        late at night, driving, a yes/no on an action: what the code allows (energy, slang, swearing,
                        a nickname, motivation)
    hard limits         in code, on every sentence: one nickname per reply and never two replies in a row, never "man",
                        "dude", "sir", strong swearing never, mild only where allowed, never at anyone, no invented
                        feelings or experiences, no slogans, no catchphrase again soon; quoted text and facts untouched
    conversations       through the real turn code with a scripted model: casual, motivation, excuse, frustration,
                        serious, a successful command (verified, concise), an unbacked claim (still blocked), a
                        confirmation question (plain), and changes said out loud (kept in settings.json, undoable)

Offline: the model is scripted, every file is a temp file, nothing outside this process is touched.
Run:  .venv\\Scripts\\python -m tests.test_personality
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.harness import setup_env  # noqa: E402

TMP = setup_env(PERSONALITY="street")
from tests.harness import Checker, Conversation  # noqa: E402

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent import social  # noqa: E402
from room_agent.actions import core  # noqa: E402
from room_agent.conversation import policy as pol  # noqa: E402
from room_agent.social import habits  # noqa: E402
from room_agent.social import personality as P  # noqa: E402
from room_agent.social.strategy import ResponseStrategy  # noqa: E402

t = Checker()
P._hour = lambda: 15  # (the afternoon: "late at night" is tested on its own)
core.ensure_loaded()

# The lines persona.md had before the profile slots (the friend profile must give them back exactly)
ORIGINAL = {
    "identity": "more like a sharp friend hanging out than an assistant",
    "character": "- Be warm and easygoing, like a friend who's on your side. Honest, but kind: if you disagree, say it gently "
                 "or with a bit of humor. Never harsh, curt, preachy or bossy. Don't lecture or stack up warnings: react like "
                 "a friend would, one thought and maybe one question, two sentences at most.",
    "address": "- Don't assume {user}'s gender: no \"man\", \"bro\", \"dude\", \"sir\" or similar.",
}


def flavor_line(system):
    return next((ln for ln in system.split("\n") if "personality for this reply" in ln), "")


def nicknames(text, terms=("boss", "bro", "brother")):
    return len(habits.vocatives(text, terms))


def fresh():
    """A new conversation moment: no recent replies, no mood."""
    social.reset()
    rt.pending = None


# ---------------------------------------------------------------------------------------------- profile + persona
print("Profile and persona:")
from room_agent.prompt import fixed_prompt, persona  # noqa: E402

raw = config.PERSONA_FILE.read_text(encoding="utf-8")
street = persona()
t.check("default profile is street (PERSONALITY=street)", P.profile().preset == "street" and P.profile().terms()[:1] == ["boss"],
        P.profile())
t.check("street persona: big-brother friend, never an assistant", "big-brother energy, never an assistant" in street
        and "street English" in street, street[:300])
t.check("...'boss' now and then, at most once a reply; never 'man', 'dude' or 'sir'",
        "Call them \"boss\" now and then: at most once in a reply" in street and "Never \"man\", \"dude\" or \"sir\"" in street)
t.check("...challenges excuses with respect, motivation specific, celebrates real wins",
        all(x in street for x in ("call it out straight, with respect", "Motivation is specific", "Celebrate real wins")))
t.check("...serious moments drop the act; no pretending; facts and confirmations never bend",
        all(x in street for x in ("drop the act", "Don't pretend", "never bends the facts")))
t.check("...no slot left unfilled, {user} filled in", not re.search(r"\{(identity|character|address|user)\}", street))
friend = P.profile()
friend = P.Profile(preset="friend", **P.PRESETS["friend"])
want = raw
for k, v in ORIGINAL.items():
    want = want.replace("{" + k + "}", v)
t.check("friend profile: persona.md reads exactly as it did before (byte for byte)", P.fill(raw, friend) == want)
custom = "You are a calm butler for {user}."
t.check("a persona file of their own (no slots): street adds its lines at the end; friend leaves it untouched",
        P.fill(custom).startswith(custom) and "street English" in P.fill(custom) and P.fill(custom, friend) == custom)
one, two = fixed_prompt(), fixed_prompt()
t.check("the fixed prompt is identical request to request (the provider caches it)", one == two and street in one)

# ---------------------------------------------------------------------------------------------- this reply (pure rules)
print("What each moment allows (decided in code):")
S = P.profile()


def c(mode="casual", kind=pol.CASUAL, values=None, nicknamed=(), hour=15, profile=None, **kw):
    return P.compute(profile or S, ResponseStrategy(mode=mode, **kw.pop("strategy", {})), kind, values or {},
                     list(nicknamed), hour, **kw)


f = c()
t.check("casual talk: full energy and slang, mild swearing ok, 'boss' allowed once",
        f.energy == "high" and f.slang == "full" and f.profanity and f.address == "boss" and f.casual_ok, f)
t.check("...but never two replies in a row with a nickname", c(nicknamed=[False, True]).address == "")
t.check("...'bro' / 'brother' once in a while in relaxed moments, 'boss' the rest",
        c(nicknamed=[True, False, True, False]).address in ("bro", "brother") and c(nicknamed=[True, False]).address == "boss")
f = c("task", pol.COMMAND)
t.check("a command: concise, no swearing, light slang, energy no higher than medium",
        f.concise and not f.profanity and f.slang == "light" and f.energy == "medium", f)
t.check("...and told: no pep talk, confirm in a few words", "no pep talk: answer or confirm it in a few words" in P.render(f))
f = c("task", pol.QUESTION)
t.check("a question: just the answer, no pep talk", f.concise and "no pep talk: just answer" in P.render(f), P.render(f))
f = c("emotional", values={"serious": 0.6})
line = P.render(f)
t.check("something serious: no slang, no swearing, no nickname, no hype, quiet and steady",
        f.serious and f.slang == "off" and not f.profanity and not f.address
        and all(x in line for x in ("no slang", "no swearing", "no nicknames", "quiet, steady")), line)
t.check("an emergency is serious too", c("urgent", values={"urgent": 0.7}).serious)
f = c("focused", values={"frustration": 0.6})
t.check("frustrated with something: calm (never heated), no swearing", f.energy == "medium" and not f.profanity
        and f.slang == "light" and "no swearing" in P.render(f), f)
t.check("late at night: the energy steps down", c(hour=1).energy == "medium" and c(hour=15).energy == "high")
t.check("driving: concise, no swearing", c(driving=True).concise and not c(driving=True).profanity)
f = c(confirming=True)
t.check("a yes/no about an action: plain words, no nickname, no slang, no swearing",
        f.confirming and not f.address and f.slang == "off" and not f.profanity and "exactly what will happen" in P.render(f))
f = c(values={"win": 0.6})
t.check("a real win: celebrate it big and name what they did", f.celebrate and f.energy == "high"
        and "celebrate it big and name exactly what they did" in P.render(f))
f = c(values={"excuse": 0.55})
t.check("an excuse: called out with respect, then one concrete step", f.motivation == "challenge"
        and "no insults" in P.render(f) and "one concrete next step" in P.render(f))
f = c(values={"motivate": 0.6})
t.check("asking for a push: about what they're working on, ending on the next action, no generic hype",
        f.motivation == "push" and "no generic hype" in P.render(f))
calm = P.Profile(**{**P.PRESETS["street"], "intensity": "low", "profanity": "off", "never_call": ["bro", "brother"]})
f = c(profile=calm)
t.check("their preferences hold: low intensity, no swearing, a nickname they stopped never comes back",
        f.energy == "low" and not f.profanity and f.terms == ["boss"], f)
nomo = P.Profile(**{**P.PRESETS["street"], "motivation": False})
t.check("motivation off: no push, no challenge", c(profile=nomo, values={"excuse": 0.6, "motivate": 0.6}).motivation == "")
t.check("friend profile: nothing extra in the context", P.render(c(), friend) == "")

# ---------------------------------------------------------------------------------------------- hard limits
print("Hard limits, sentence by sentence:")
relaxed = c()
none = c(nicknamed=[True])
E = habits.enforce
t.check("one nickname per reply: the second is taken out", E("Bet, boss, let's go.", relaxed) == "Bet, boss, let's go."
        and E("Boss, that's the move.", relaxed, said=["Yo, boss."]) == "That's the move.")
t.check("...none at all right after a reply that had one", E("Bet, boss.", none) == "Bet.")
t.check("never 'man', 'dude' or 'sir'", E("Alright man, it's 5 PM.", relaxed) == "Alright, it's 5 PM."
        and E("Okay, dude.", relaxed) == "Okay." and E("Thanks sir.", relaxed) == "Thanks.")
t.check("'bro' only when it's relaxed", E("Restart the router, bro.", c("focused")) == "Restart the router.")
t.check("a mention isn't a nickname: 'your boss emailed' stays", E("Your boss emailed you about Friday.", relaxed)
        == "Your boss emailed you about Friday." and E("Boss wants it by Friday.", none) == "Boss wants it by Friday.")
t.check("strong swearing never; the rest of the sentence stays", E("That's fucking awesome, boss!", relaxed) == "That's awesome, boss!")
t.check("mild swearing only where allowed, and once", E("That's a damn good week.", relaxed) == "That's a damn good week."
        and E("Damn, that's a damn good week.", relaxed) == "Damn, that's a good week."
        and E("Hell yeah.", relaxed, said=["That's a damn good week."]) == "Yeah.")
t.check("...gone in a command, a confirmation or a report of what happened",
        E("Damn, that's set.", c("task", pol.COMMAND)) == "That's set."
        and E("Hell yeah, it's done.", relaxed, reporting=True) == "Yeah, it's done."
        and E("Want me to send it, boss? Damn.", relaxed, confirming=True) == "Want me to send it?")
t.check("never swearing at them or insulting them", E("You're lazy, bro.", relaxed) == ""
        and E("Shut up and do it.", relaxed) == "" and E("Quit stalling, you idiot.", relaxed) == "")
t.check("what's quoted or read out is never rewritten", E("He wrote: \"you're an idiot, damn it\".", c(confirming=True))
        == "He wrote: \"you're an idiot, damn it\".")
t.check("...and a reply passing on outside content (an email, a page) keeps its words exactly; only 'boss' stays capped",
        E("Mike's email says the deal is fucked, man, and calls you a clown.", relaxed, outside=True)
        == "Mike's email says the deal is fucked, man, and calls you a clown."
        and E("Boss, Mike says hi, boss.", relaxed, outside=True) == "Boss, Mike says hi.")
t.check("street style is for talking with them, never for messages written for someone else",
        "Anything you write or say for someone else (an email, a text, a message, a call) is in their voice" in street)
t.check("no invented experiences or feelings", E("I've been there, it's rough.", relaxed) == "It's rough."
        and E("I'm so proud of you, boss!", relaxed) == "That's big, boss!"
        and E("I'm proud of how you handled that.", relaxed) == "Respect for how you handled that.")
t.check("no slogans or quotes", E("As they say, no pain no gain.", relaxed) == ""
        and E("Rise and grind.", relaxed) == "")
t.check("a catchphrase isn't said again soon (this reply or the last few)", E("Let's get it.", relaxed, recent=["Let's get it, boss."]) == ""
        and E("You got this, open the doc.", relaxed, said=["You got this."]) == "Open the doc.")
t.check("...but ordinary words that happen to match stay", E("Let's go over the plan.", relaxed, recent=["Let's go!"])
        == "Let's go over the plan." and E("Is this for real?", relaxed, recent=["For real."]) == "Is this for real?")
t.check("facts stay exact: numbers, times, names", E("Bet, boss, timer's set for 10 minutes at 7:45 PM for Jamal.", relaxed)
        == "Bet, boss, timer's set for 10 minutes at 7:45 PM for Jamal.")

# ---------------------------------------------------------------------------------------------- conversations
print("Conversations (real turn code, scripted model):")
convo = Conversation()

fresh()
results, system, _ = convo.say("yo what's good", reply="Yo, what's good, boss? Ready when you are, boss.")
said = " ".join(convo.said())
t.check("casual: the model is told what fits this reply", "you may call them \"boss\" once" in flavor_line(system),
        flavor_line(system))
t.check("...and 'boss' is spoken once, not in every sentence", nicknames(said) == 1 and "Ready when you are" in said, said)
results, system, _ = convo.say("nothing much, just chilling", reply="Bet, boss. Same here.")
t.check("...the next reply gets no nickname (never back to back)", "no nickname this time" in flavor_line(system)
        and nicknames(" ".join(convo.said())) == 0, convo.said())

fresh()
results, system, _ = convo.say("motivate me, I need a push to finish my resume tonight",
                               reply="Let's get it, boss. Open the resume and fix the top section by 9. As they say, "
                                     "winners never quit.")
said = " ".join(convo.said())
t.check("motivation: told to make it specific and end on the next action", "they want a push" in flavor_line(system)
        and "one concrete next action" in flavor_line(system), flavor_line(system))
t.check("...the concrete step is spoken, the slogan isn't", "fix the top section by 9" in said and "winners" not in said, said)

fresh()
results, system, _ = convo.say("I'll do it tomorrow, I'm too tired to study tonight",
                               reply="Nah, that's an excuse. You're lazy, bro. Do twenty minutes of flashcards right now.")
said = " ".join(convo.said())
t.check("an excuse: called out with respect and one concrete step", "they're making an excuse" in flavor_line(system)
        and "no insults" in flavor_line(system), flavor_line(system))
t.check("...the insult is never spoken; the call-out and the step are", "lazy" not in said and "that's an excuse" in said
        and "twenty minutes of flashcards" in said, said)

fresh()
results, system, _ = convo.say("ugh this wifi still isn't working, wtf",
                               reply="Damn, that's annoying, bro. Restart the router and give it a minute.")
said = " ".join(convo.said())
t.check("frustration: calm, no swearing (never heated back)", "no swearing" in flavor_line(system)
        and "mode focused" in system, flavor_line(system))
t.check("...spoken without the swearing or the 'bro', the fix intact", "Damn" not in said and "bro" not in said
        and "Restart the router" in said, said)

fresh()
results, system, _ = convo.say("my mom is in the hospital, they think it's cancer",
                               reply="Damn, bro, I've been there. That's a lot to carry. I'm here if you want to talk about it.")
said = " ".join(convo.said())
t.check("serious: the act drops (no slang, swearing, nicknames, jokes or hype)", "something heavy" in flavor_line(system),
        flavor_line(system))
t.check("...spoken plainly, with no invented experience", said == "That's a lot to carry. I'm here if you want to talk about it.",
        said)

fresh()
results, system, _ = convo.say("can you set a timer for 10 minutes for the pasta, I'm cooking",
                               calls=[("set_timer", {"seconds": 600, "label": "pasta"})],
                               reply="Bet, boss, ten minutes on the clock. Damn, let's get it, you got this!")
said = " ".join(convo.said())
t.check("a command: really done (the tool's own result)", results and str(results[0]).startswith("OK"), results)
t.check("...told: no pep talk, confirm in a few words", "no pep talk: answer or confirm it in a few words" in flavor_line(system),
        flavor_line(system))
t.check("...the confirmation is spoken, without swearing, one nickname at most", "ten minutes on the clock" in said
        and "Damn" not in said and nicknames(said) <= 1, said)

fresh()
results, system, _ = convo.say("I finally finished the whole marathon training plan, 16 weeks straight!",
                               reply="Hell yeah, boss! Sixteen weeks without missing, that's a damn serious streak. "
                                     "I'm so proud of you.")
said = " ".join(convo.said())
t.check("a real win: told to celebrate it big and name it", "real win" in flavor_line(system), flavor_line(system))
t.check("...celebrated with what they did; one mild swear, no invented pride", "Sixteen weeks without missing" in said
        and len(habits.PROFANE.findall(said)) == 1 and "proud" not in said, said)

fresh()
convo.say("open spotify", reply="Bet, boss, I opened Spotify for you.")
said = " ".join(convo.said())
t.check("personality never makes a claim true: an unbacked 'I opened Spotify' is still not spoken", "opened" not in said,
        said)

fresh()
before = P.profile()
results, system, _ = convo.say("play something chill", calls=[("set_personality", {"profanity": "off"})],
                               reply="Want me to switch the swearing off for good, boss?")
said = " ".join(convo.said())
t.check("a change they didn't ask for in their own words isn't made (their yes first)",
        results and "NEEDS_CONFIRMATION" in str(results[0]) and P.profile().profanity == before.profanity, results)
t.check("...and the yes/no question is plain: no nickname", "Want me to switch the swearing off for good?" in said
        and nicknames(said) == 0, said)
rt.pending = None

print("Changes said out loud:")
fresh()
convo.model.requests.clear()
convo.say("stop swearing", reply="SHOULD NOT BE NEEDED")
saved = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8")).get("personality", {})
t.check("'stop swearing' -> done at once (no model call), kept in settings.json",
        not convo.requests and P.profile().profanity == "off" and saved.get("profanity") == "off", (convo.said(), saved))
t.check("...the persona now says no swearing at all", "No swearing at all." in persona())
results, system, _ = convo.say("I just closed my first client!", reply="Hell yeah! That's a damn big deal, you closed it.")
said = " ".join(convo.said())
t.check("...even a celebration is spoken without it", "no swearing" in flavor_line(system) and not habits.PROFANE.search(said)
        and "you closed it" in said, said)
cap = core.get("set_personality")
state0 = {"saved": dict(saved), "profile": {}}
fresh()
convo.say("stop calling me bro", reply="SHOULD NOT BE NEEDED")
t.check("'stop calling me bro' -> never 'bro' again (persona and code)", "bro" not in P.profile().terms()
        and "\"bro\"" not in persona(), (P.profile().terms(), persona()[-400:]))
t.check("...a 'bro' the model writes anyway isn't spoken", E("Yo bro, nice.", P.compute(P.profile(), ResponseStrategy(mode="casual"),
                                                                                      pol.CASUAL, {}, [], 15)) == "Yo, nice.")
t.check("undo puts the profile back the way it was", cap.undo({}, state0, None).startswith("OK")
        and "bro" in P.profile().terms() and P.profile().profanity == "off")
fresh()
convo.say("go back to your old personality",  # (a reflex where it can be; otherwise the model's call: same result)
          scripts=[{"tools": [("set_personality", {"preset": "friend"})]}, {"text": "Okay."}])
t.check("'go back to your old personality' -> the original friend persona, exactly", P.profile().preset == "friend"
        and persona() == want.replace("{user}", config.USER_NAME).strip())
results, system, _ = convo.say("yo what's up", reply="Not much, bro. Chilling.")
t.check("...no personality line, and the original habits only (nothing extra taken out)",
        flavor_line(system) == "" and " ".join(convo.said()) == "Not much, bro. Chilling.", convo.said())
t.check("...no extra stock phrases either", "Talk to me, boss." not in rt.phrases.pools["wake"])
fresh()
convo.say("go back to your default personality", scripts=[{"tools": [("set_personality", {"reset": True})]}, {"text": "Okay."}])
t.check("'go back to your default personality' -> street again, defaults back", P.profile().preset == "street"
        and P.profile().profanity == "mild" and "Talk to me, boss." in rt.phrases.pools["wake"], P.profile())

print("What memory says to call them, greetings:")
real_addr = P._memory_address
P._memory_address = lambda: "Chief"
P._cache["key"] = None
t.check("'call me Chief' (memory's address_as) leads the nicknames, and 'chief' is no longer banned",
        P.profile().terms()[0] == "Chief" and "\"Chief\"" in persona()
        and E("Yo, chief.", c(profile=P.profile())) == "Yo, chief.")
P._memory_address = real_addr
P._cache["key"] = None
from room_agent.conversation import greet  # noqa: E402

stock = [P.greeting_line(greet.FALLBACK["day"][0]) for _ in range(40)]  # ("Hey, you're back!")
t.check("a stock greeting: their nickname now and then, never 'man'/'dude'", set(stock) <= {"Hey, you're back!", "Hey, you're back, boss!"}
        and len(set(stock)) == 2, set(stock))
line = P.greeting_line("Yo man, welcome back, boss. Damn, long day, boss?", stock=False)
t.check("a greeting the model wrote follows the same limits", line == "Yo, welcome back, boss. Damn, long day?", line)

t.done()
