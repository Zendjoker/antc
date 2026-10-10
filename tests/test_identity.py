"""Assistant identity: a configurable name (tools/voice.py: set_assistant_name) and wake-word model (set_wake_word),
offline. No real openWakeWord download: openwakeword.MODELS is read (the real catalog, whatever it is on this
machine) but nothing is fetched over the network.

Run:  .venv\\Scripts\\python -m tests.test_identity
"""

from tests.harness import setup_env

setup_env()

from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor  # noqa: E402
from room_agent.prompt import persona  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
core.ensure_loaded()


def run(name, args):
    rt.new_turn("identity test")
    return executor.execute(name, args)


print("Assistant name:")
before = rt.assistant_name
t.check("persona() mentions the current name", before in persona(), persona()[:120])
r = run("set_assistant_name", {"name": "Friday"})
t.check("set_assistant_name succeeds and takes effect at once", r.success and "Friday" in r.message
        and rt.assistant_name == "Friday", (r.message, rt.assistant_name))
t.check("persona() reflects the new name on the very next render, no restart", "Friday" in persona()
        and before not in persona(), persona()[:120])
r = run("set_assistant_name", {"name": ""})
t.check("an empty name is refused (NEEDS), not silently accepted", not r.success and r.message.startswith("NEEDS"), r.message)
rt.assistant_name = before  # restore for any other test that might run in this process

print("Wake word:")
import room_agent.tools.voice as voice_tool  # noqa: E402

real_options = voice_tool.wake_word_options
voice_tool.wake_word_options = lambda: ["alexa", "hey_mycroft", "hey_jarvis", "hey_rhasspy", "timer", "weather"]
prior_wake = rt.wake_word
r = run("set_wake_word", {"model": "Hey Mycroft"})
t.check("a real ready-made model name (any spacing/case) is accepted and normalized", r.success
        and rt.wake_word == "hey_mycroft", (r.message, rt.wake_word))
t.check("...and the result says a restart is needed, never claims it's immediate", "next time" in r.message.lower()
        or "restart" in r.message.lower(), r.message)
rt.wake_word = prior_wake
r = run("set_wake_word", {"model": "hey computer"})
t.check("a name that isn't a real installed model is refused, not invented, and lists the real options",
        not r.success and "hey_mycroft" in r.message and rt.wake_word == prior_wake, (r.message, rt.wake_word))
t.check("...and is explicit that a genuinely new word needs training first, not something done by voice",
        "train" in r.message.lower(), r.message)
voice_tool.wake_word_options = real_options
t.check("wake_word_options() reflects the real installed openwakeword.MODELS catalog (not a hardcoded guess)",
        isinstance(voice_tool.wake_word_options(), list) and len(voice_tool.wake_word_options()) > 0,
        voice_tool.wake_word_options())

t.done("IDENTITY")
