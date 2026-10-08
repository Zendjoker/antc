"""Greeting you when you come home: the door opens after the room has been quiet for a while, and Jarvis says one short,
natural line, like a friend who's been hanging out in your room, then listens for your answer (no wake word needed).

    door.opened (tools/zigbee.py) -> should_greet() -> compose() a line -> the voice loop speaks it and starts a
    conversation (conversation/loops.py, through `request`)

Not when: you're already talking with it, it's in quiet mode, an alarm is ringing, you're on a call or out driving,
there was voice in the room recently (you're leaving, not arriving), or it greeted you a moment ago.
"""

import logging
import random
import threading
import time

from room_agent import config
from room_agent import runtime as rt

log = logging.getLogger("room-agent")
request = threading.Event()  # set when there's a greeting to say: the voice loop wakes up for it
pending = {"text": "", "at": 0.0}
last_greeting = {"at": 0.0}

# used when the model can't be asked (no budget, offline, slow): still varied, still human
FALLBACK = {
    "morning": ["Morning! Early start, huh?", "Oh hey, you're up and about.", "There you are. Morning!"],
    "day": ["Hey, you're back!", "Oh, there you are.", "Yo, welcome back.", "Ayy, look who's back."],
    "evening": ["Hey, welcome back! Long day?", "There you are. How'd it go out there?", "Yo, you're home. Good timing."],
    "night": ["Hey, you made it back.", "Late one, huh? Welcome back.", "Oh hey, night owl."],
}


def part_of_day(hour):
    return "morning" if 5 <= hour < 12 else "day" if hour < 17 else "evening" if hour < 23 else "night"


def last_activity():
    """When there was last a sign of you in the room: your voice, or a conversation."""
    voice = getattr(rt.engine, "user_voice_at", 0.0) if rt.engine else 0.0
    convo = 0.0
    if rt.recent:
        try:
            convo = time.mktime(time.strptime(rt.recent[-1]["time"][:19], "%Y-%m-%d %H:%M:%S"))
        except (ValueError, KeyError, TypeError):
            convo = 0.0
    return max(voice, convo)


def should_greet(now=None):
    """-> (yes, why not)."""
    from room_agent.conversation.states import State

    now = now or time.time()
    if not config.GREET_ON_ARRIVAL:
        return False, "greetings are off"
    if rt.state.state is not State.WAKE_WORD_ONLY:
        return False, f"busy ({rt.state.state.name.lower()})"  # talking already, quiet mode, starting up...
    if rt.ringing:
        return False, "an alarm is ringing"
    try:
        from room_agent.phone import state as phone

        if phone.in_call or phone.is_driving():
            return False, "on a call or out driving"
    except Exception:
        pass
    away = now - last_activity()
    if away < config.GREET_AWAY_MIN * 60:
        return False, f"there was someone here {int(away)}s ago (leaving, not arriving)"
    if now - last_greeting["at"] < config.GREET_COOLDOWN_MIN * 60:
        return False, "greeted a moment ago"
    return True, ""


def _away_words(seconds):
    if seconds >= 20 * 3600:
        return "since yesterday or longer"
    if seconds >= 3600:
        return f"about {round(seconds / 3600)} hour{'s' if seconds >= 5400 else ''}"
    return f"about {max(10, round(seconds / 600) * 10)} minutes"


def compose(now=None):
    """One short line, written by the model from what it knows (time, how long you were out, what you said you were
    doing); a varied stock line if the model can't be asked quickly."""
    now = now or time.time()
    when = part_of_day(time.localtime(now).tm_hour)
    away = now - last_activity()
    earlier = [f"{m['role']}: {m['text'][:140]}" for m in rt.recent[-6:]]
    from room_agent.prompt import persona

    system = (persona() + "\n\nRight now you're not answering a question: your friend just walked into the room. Say ONE "
              "short spoken line to greet them, the way a close friend hanging out in their room would. Warm, casual, a "
              "bit playful if it fits. If the earlier conversation says where they went or what they were doing, mention "
              "it naturally. Never say 'how are you doing', 'how can I help', 'welcome home, sir' or anything that sounds "
              "like an assistant or a robot. No questions about what they need. At most 12 words. Just the line, nothing "
              "else: no quotes, no tags.")
    prompt = (f"Time: {time.strftime('%A %I:%M %p', time.localtime(now)).replace(' 0', ' ')} ({when}). They were out for "
              f"{_away_words(away)}.\nEarlier conversation (oldest first):\n" + ("\n".join(earlier) or "(none today)"))
    box = {}

    def ask():
        try:
            from room_agent.llm.memory_calls import memory_call_text

            box["text"] = memory_call_text(system, prompt)
        except Exception as e:
            log.info("greeting: model unavailable (%s), using a stock line", e.__class__.__name__)

    worker = threading.Thread(target=ask, daemon=True)
    worker.start()
    worker.join(4.0)  # (they're walking in: a stock line now beats a perfect one later)
    line = (box.get("text") or "").strip().strip('"').split("\n")[0]
    if not line or len(line.split()) > 20:
        line = random.choice(FALLBACK[when])
    return line


def _note(event, note):
    try:
        from room_agent.tools.zigbee import hub

        hub.annotate(event.get("device", "Door sensor"), "opened", note)
    except Exception as e:
        log.debug("greeting: couldn't note the door event (%s)", e)


def on_door(event):
    """The door opened. That proves the door opened, not who came in: if the room had been quiet for a while it may be
    an arrival, and the proactive policy (proactive.py) decides whether a greeting fits right now."""
    from room_agent import proactive

    device = event.get("device", "Door sensor")
    away = time.time() - last_activity()
    if away < config.GREET_AWAY_MIN * 60:
        _note(event, "noticed; no greeting: someone was already in the room (more likely leaving or passing)")
        proactive.notify(proactive.Event("arrival", f"door:{device}", f"{device} opened (someone was already in the room)"))
        log.info("door opened: no greeting (voice in the room %ds ago)", int(away))
        return
    try:
        from room_agent.phone import state as phone

        if phone.is_driving():  # (they're out: whoever opened it isn't them; the phone watcher handles that)
            _note(event, "noticed while you were out driving; no greeting")
            return
    except Exception:
        pass
    ev = proactive.Event("arrival", f"door:{device}",
                         f"{device} opened after the room was quiet for {_away_words(away)} (maybe someone arriving)")
    decision, why = proactive.decide(ev, enabled=config.GREET_ON_ARRIVAL)
    if decision != proactive.SPEAK_NOW:
        reason = {"in a conversation: not interrupting": "we were already talking", "quiet mode: not interrupting":
                  "Jarvis was in quiet mode"}.get(why, why)
        _note(event, "noticed; no greeting because " + reason)
        return

    def work():
        line = compose()
        ok2, why2 = should_greet()  # (things may have changed while it was written)
        if not ok2:
            log.info("door opened: greeting dropped (%s)", why2)
            return
        last_greeting["at"] = time.time()
        _note(event, f"greeted them: {line!r}")
        pending.update(text=line, at=time.time())
        log.info("door opened after a while: greeting you (%r)", line)
        request.set()

    threading.Thread(target=work, daemon=True, name="greeting").start()


def take():
    """The greeting to say now, if one is waiting (and still fresh)."""
    request.clear()
    text, at = pending["text"], pending["at"]
    pending.update(text="", at=0.0)
    return text if text and time.time() - at < 30 else ""


def _on_presence(event):
    from room_agent import proactive

    what = "someone detected" if event["name"].endswith("detected") else "nobody detected anymore"
    proactive.decide(proactive.Event("presence", f"presence:{event.get('device')}:{what}", f"{event.get('device')}: {what}"))


def start():
    from room_agent.actions.events import events

    events.on("door.opened", on_door)
    events.on("presence.*", _on_presence)
