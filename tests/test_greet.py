"""Greeting you when you come home, offline: when it greets (and when it must not), what it says, and how the voice
loop is woken for it. No audio, no real model (a stub writes the line), no PC actions.

Run:  .venv\\Scripts\\python -m tests.test_greet
"""

import queue
import time

from tests.harness import setup_env

setup_env(GREET_ON_ARRIVAL="1", GREET_AWAY_MIN="10", GREET_COOLDOWN_MIN="30")

from room_agent import runtime as rt  # noqa: E402
from room_agent.conversation import greet  # noqa: E402
from room_agent.conversation.states import State  # noqa: E402
from room_agent.llm import memory_calls  # noqa: E402
from room_agent.phone import state as phone  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()


class Engine:
    user_voice_at = 0.0


rt.engine = Engine()
now = time.time()
rt.recent[:] = [{"role": "user", "text": "heading to the gym, back in an hour", "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 3600))},
                {"role": "assistant", "text": "Have a good one!", "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 3600))}]
rt.engine.user_voice_at = now - 3500
rt.state.go(State.WAKE_WORD_ONLY, "test")

print("When it greets:")
t.check("door opens after an hour of a quiet room -> greet", greet.should_greet(now)[0], greet.should_greet(now))
rt.engine.user_voice_at = now - 60
t.check("voice in the room a minute ago -> you're leaving, no greeting", not greet.should_greet(now)[0], greet.should_greet(now))
rt.engine.user_voice_at = now - 3500
for state, why in ((State.LISTENING, "already talking"), (State.QUIET, "quiet mode"), (State.SPEAKING, "speaking")):
    rt.state.go(state, "test")
    t.check(f"{why} -> no greeting", not greet.should_greet(now)[0])
rt.state.go(State.WAKE_WORD_ONLY, "test")
rt.ringing = {"label": "pasta"}
t.check("an alarm ringing -> no greeting", not greet.should_greet(now)[0])
rt.ringing = None
phone.driving = {"since": now, "source": "test"}
t.check("out driving (the door opening is someone else) -> no greeting", not greet.should_greet(now)[0])
phone.driving = None
greet.last_greeting["at"] = now - 300
t.check("greeted 5 minutes ago -> not again", not greet.should_greet(now)[0])
greet.last_greeting["at"] = 0.0

print("What it says:")
seen = {}


def stub(system, prompt):
    seen.update(system=system, prompt=prompt)
    return '"Yo, you\'re back! How was the gym?"'


memory_calls.memory_call_text = stub
line = greet.compose(now)
t.check("the model writes it, quotes stripped", line == "Yo, you're back! How was the gym?", line)
t.check("...knowing where you went and how long you were out", "heading to the gym" in seen["prompt"] and "hour" in seen["prompt"],
        seen.get("prompt", "")[:200])
t.check("...told to sound like a friend, never an assistant", all(w in seen["system"] for w in ("how are you doing", "assistant", "At most 12 words")))
memory_calls.memory_call_text = lambda s, p: (_ for _ in ()).throw(ConnectionError("offline"))
line = greet.compose(now)
t.check("model unavailable -> a varied human stock line instead", line in sum(greet.FALLBACK.values(), []), line)
memory_calls.memory_call_text = lambda s, p: "word " * 40
t.check("a rambling answer is replaced too", greet.compose(now) in sum(greet.FALLBACK.values(), []))
t.check("stock lines never assume who you are ('he', 'sir', 'bro')", not any(w in " ".join(sum(greet.FALLBACK.values(), [])).lower().split()
                                                                             for w in ("he", "sir", "bro", "dude", "man")))
memory_calls.memory_call_text = lambda s, p: "Hey, you're back!"

print("Waking the voice loop:")
greet.on_door({"device": "Door sensor", "at": now})
for _ in range(50):
    if greet.request.is_set():
        break
    time.sleep(0.05)
t.check("door.opened -> a greeting is waiting and the loop is woken", greet.request.is_set() and greet.pending["text"] == "Hey, you're back!")
from room_agent.audio import mic  # noqa: E402


class Oww:
    def reset(self):
        pass

    def predict(self, pcm):
        return {"hey_jarvis": 0.0}


q = queue.Queue()
t.check("the wake-word wait returns at once for it (no wake word needed)", mic.wait_for_wake(q, Oww()) is None)
t.check("...and the loop takes the line once", greet.take() == "Hey, you're back!" and greet.take() == "" and not greet.request.is_set())
greet.pending.update(text="old line", at=time.time() - 120)
t.check("a greeting that waited too long is dropped (you've been home a while)", greet.take() == "")
rt.engine.user_voice_at = time.time()
greet.last_greeting["at"] = 0.0
greet.on_door({"device": "Door sensor", "at": time.time()})
time.sleep(0.3)
t.check("door opening while you're in the room (leaving) -> nothing", not greet.request.is_set())
t.done("GREETING")
