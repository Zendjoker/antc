"""Generalization set for the delivery reading: what someone says -> SocialState -> ResponseStrategy -> SpeechDirector.

Written BEFORE the meaning reader (social/meaning.py) and never copied into any rule, cue list or anchor. DEV is what
thresholds may be calibrated on; TEST is held out and only measured. A category is judged on the DELIVERY the director
gives a fixed, neutral reply ("Alright. I got it."), so only the reading of the user's turn varies.

    low         quieter and warm, slower (never sad acting)
    urgent      clear and firm, a little quicker
    serious     calm or quiet and warm, no reaction
    excited     livelier (upbeat / excited)
    frustrated  calm and direct
    joking      lightly amused / playful
    neutral     plain: no direction at all (the most important one: plain requests must stay plain)
"""

DEV = {
    "low": ["I'm so tired.", "Ugh, I'm beat.", "I just want to lie down and not think.", "I've got nothing left today.",
            "Honestly I'm running on fumes."],
    "urgent": ["Quick, the kitchen is on fire.", "Help, water is pouring through the ceiling!",
               "My kid swallowed something, what do I do?", "The car is smoking on the highway, what do I do?"],
    "serious": ["My grandma is in the hospital.", "My dad got some bad test results.",
                "I think my relationship is falling apart."],
    "excited": ["I finally fixed it!", "We won the game!", "I got the job!!"],
    "frustrated": ["This shit still doesn't work.", "Why does it keep crashing?"],
    "joking": ["haha you're useless today", "lol you're so slow"],
    "neutral": ["What's the weather tomorrow?", "Set a timer for ten minutes.", "Open Spotify.", "Play some jazz.",
                "What's on my calendar today?"],
}

TEST = {
    "low": ["Man, it's been a long day.", "I'm completely drained.", "That was exhausting.",
            "I can barely keep my eyes open.", "What a week. I'm done.", "I'm just not feeling it today.",
            "Today kind of wore me out.", "I didn't sleep at all last night."],
    "urgent": ["Quick, the server's down.", "There's smoke coming out of my laptop!", "The pipe under the sink just burst.",
               "I smell gas in the apartment.", "My friend fainted and isn't waking up.",
               "The production database is deleting rows right now, how do I stop it?",
               "I locked myself out and the oven is still on.", "My dog is choking!"],
    "serious": ["My uncle passed away this morning.", "The doctor found a lump.", "I got laid off today.",
                "My best friend stopped talking to me and I don't know why.", "We had to put our cat down.",
                "I've been feeling really anxious lately."],
    "excited": ["Bro, it actually works now!", "They said yes to my proposal!", "I just passed my driving test!",
                "The app hit a thousand users today!", "Guess who just got promoted!"],
    "frustrated": ["It's frozen again, unbelievable.", "I've restarted it five times and nothing changes.",
                   "Seriously, why is this so hard?", "Every single time I try, it errors out.",
                   "Nothing I do makes any difference with this thing."],
    "joking": ["you're the worst assistant ever lmao", "haha okay genius", "nice one, Einstein 😂"],
    "neutral": ["Remind me to call mom at six.", "How many ounces in a cup?", "Turn the volume down a bit.",
                "The meeting got moved to Thursday.", "My brother is coming over tonight.",
                "I need to fix the sink this weekend.", "Tell me a joke.", "What's the capital of Australia?",
                "Add milk to the shopping list.", "How long does it take to boil an egg?"],
}

# TEST was looked at while diagnosing the first reader, so it became calibration data (with DEV). TEST2 was written
# after that, avoiding the cue words in signals.py and the descriptions in meaning.py, and is the held-out set now.
TEST2 = {
    "low": ["I've been up since five and I'm fading fast.", "My brain is mush after all those meetings.",
            "I could fall asleep standing up.", "Feeling pretty flat today.", "That workout destroyed me.",
            "Not much left in me tonight."],
    "urgent": ["The bathtub is overflowing onto the floor!", "Someone is trying to break into my car right now.",
               "My phone battery is swelling and getting hot.", "The baby fell off the bed and is crying hard.",
               "Our website is returning errors for every customer right now!", "The breaker sparked and the lights went out.",
               "The toaster is shooting flames."],
    "serious": ["My mom's illness came back and it's worse this time.", "My parents are splitting up.",
                "I failed the exam I needed to graduate.", "My grandpa doesn't recognize me anymore.", "We lost the baby.",
                "I think I'm going to lose the house."],
    "excited": ["We're having a baby!", "My band just got booked for a festival!", "I ran my first marathon today!",
                "The investors said yes!", "I just bought my first car!"],
    "frustrated": ["The printer jammed for the third time this morning.", "Why won't this stupid update install?",
                   "I keep getting logged out every two minutes.", "This app is a mess, nothing loads properly.",
                   "I've been on hold for an hour and they hung up on me."],
    "joking": ["lmao you really tried", "haha you're such a nerd"],
    "neutral": ["What's the score of the Lakers game?", "Can you read me my last email?", "How do I say thank you in Japanese?",
                "Turn on the living room lights.", "My sister's birthday is next Friday.",
                "What should I cook with chicken and rice?", "Is it going to rain this afternoon?",
                "Find me a good movie for tonight.", "How far is the airport from here?", "I'm going to the gym later."],
}
SEEN = {k: DEV[k] + TEST[k] for k in DEV}
# pressure without a problem is not an emergency; a problem under pressure is, whatever is going wrong
PRESSURE = {"urgent": ["Hurry, my laptop won't boot and I present in five minutes!",
                       "Quick, the payment page is throwing errors for everyone.",
                       "I need help right now, the garage door is stuck halfway and the car is under it.",
                       "Quick, my sink is overflowing!"],
            "neutral": ["Quick question, what's the capital of Peru?", "Can you quickly add eggs to my list?",
                        "Hurry up and play the next song."]}

REPLY = "Alright. I got it."
# the wrong WAY, worse than plain: livelier or amused about something bad, or any acting on a plain request
HARMFUL = {"low": {"upbeat", "excited", "playful", "lightly amused"}, "serious": {"upbeat", "excited", "playful", "lightly amused"},
           "urgent": {"upbeat", "excited", "playful", "lightly amused", "quiet"}, "frustrated": {"upbeat", "playful", "lightly amused"},
           "excited": {"quiet", "sincere"}, "joking": {"clear", "firm"}}


def judged(category, perf):
    """Does this SpeechPerformance fit the category?"""
    d = set(perf.direction)
    if category == "neutral":
        return not d and not perf.reaction
    if category == "low":
        return bool(d & {"quiet", "warm", "calm"}) and perf.pace <= 1.0 and not d & {"upbeat", "excited", "playful"}
    if category == "urgent":
        return {"clear", "firm"} <= d
    if category == "serious":
        return bool(d & {"quiet", "calm", "warm"}) and not perf.reaction and not d & {"upbeat", "playful", "lightly amused"}
    if category == "excited":
        return bool(d & {"upbeat", "excited"})
    if category == "frustrated":
        return {"calm", "direct"} <= d
    if category == "joking":
        return bool(d & {"lightly amused", "playful"})
    raise ValueError(category)


def run(sets, failures_before=0):
    """-> {category: [(sentence, ok, direction, why)]} through the real social layer + director."""
    from room_agent import runtime as rt
    from room_agent import social
    from room_agent.social import meaning
    from room_agent.speech import director

    meaning.load()  # (deterministic: don't race the background load)
    out = {}
    for category, sentences in sets.items():
        rows = []
        for s in sentences:
            social.reset()
            rt.new_turn(s)
            strategy = social.on_user_turn(s)
            p = director.direct(REPLY, strategy, social.state.snapshot())
            rows.append((s, judged(category, p), p.direction, strategy.mode))
        out[category] = rows
    return out


def harmful(category, direction):
    return bool(direction) if category == "neutral" else bool(set(direction) & HARMFUL.get(category, set()))


def report(results, title):
    total = ok = bad = 0
    print(f"\n{title}")
    for category, rows in results.items():
        good = sum(r[1] for r in rows)
        total, ok = total + len(rows), ok + good
        print(f"  {category:10} {good}/{len(rows)}")
        for s, passed, direction, mode in rows:
            if not passed:
                wrong = harmful(category, direction)
                bad += wrong
                print(f"      {'WRONG' if wrong else 'miss '}: {s!r:60} -> {direction or 'plain'} ({mode})")
    print(f"  overall    {ok}/{total} ({ok / total:.0%}), wrong-direction: {bad}")
    return ok, total, bad


if __name__ == "__main__":
    import sys

    from tests.harness import setup_env

    setup_env(SOCIAL_MEANING="1")
    which = sys.argv[1] if len(sys.argv) > 1 else "dev"
    report(run({"dev": DEV, "test": TEST, "seen": SEEN, "test2": TEST2}[which]), f"{which.upper()} set")
