"""Texts to your phone and VIPs by voice, offline: Twilio is a stub (no text is sent, nothing is charged).

Run:  .venv\\Scripts\\python -m tests.test_texts
"""

from tests.harness import setup_env

setup_env(TWILIO_ACCOUNT_SID="AC" + "0" * 32, TWILIO_AUTH_TOKEN="fake-token", TWILIO_NUMBER="+15550001111",
          MY_PHONE="+15552223333", SMS_DAILY_LIMIT="3", VIP_SENDERS="boss@work.com")

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor  # noqa: E402
from room_agent.computer.context import desk  # noqa: E402
from room_agent.phone import texts  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
core.ensure_loaded()
sent = []


class FakeHTTP:
    status = 201

    def post(self, url, data=None, auth=None, timeout=None):
        sent.append((url, dict(data)))

        class R:
            status_code = FakeHTTP.status

            def json(self):
                return {"sid": "SM1234567890abcd"}

        return R()


from room_agent.phone import twilio  # noqa: E402

real_init = twilio.Twilio.__init__
twilio.Twilio.__init__ = lambda self, sid, token, http=None: real_init(self, sid, token, FakeHTTP())


def run(name, args, said):
    rt.new_turn(said)
    return executor.execute(name, args)


print("Texting:")
r = run("text_me", {"message": "Buy milk on the way home"}, "text me buy milk on the way home")
t.check("'text me ...' -> one SMS, to THEIR number, from Jarvis's", r.success and len(sent) == 1
        and sent[0][1]["To"] == "+15552223333" and sent[0][1]["From"] == "+15550001111"
        and sent[0][1]["Body"] == "Jarvis: Buy milk on the way home", sent)
desk.saw_page("opera", 1, "github.com/Zendjoker/antc", "antc - Opera")
r = run("text_me", {"message": "", "attach": "page"}, "send that link to my phone")
t.check("'send that link to my phone' -> the open page's real address", r.success
        and sent[-1][1]["Body"] == "Jarvis: https://github.com/Zendjoker/antc", sent[-1])
r = run("text_me", {"message": "x", "attach": "source 4"}, "send source 4 to my phone")
t.check("asking for a link that doesn't exist -> nothing sent", not r.success and len(sent) == 2, r.message)
run("text_me", {"message": "third"}, "text me third")
r = run("text_me", {"message": "fourth"}, "text me fourth")
t.check("daily limit (3 here) -> the 4th isn't sent", not r.success and "limit" in r.message and len(sent) == 3, r.message)
config.SMS_DAILY_LIMIT = 50
FakeHTTP.status = 400
r = run("text_me", {"message": "hi"}, "text me hi")
t.check("Twilio refuses -> 'not sent', with the likely reason; no account details in the message", not r.success
        and "Nothing was sent" in r.message and "fake-token" not in r.message, r.message)
FakeHTTP.status = 201
r = run("text_me", {"message": "hi"}, "what's the weather")
t.check("their words didn't ask for a text -> asks first (an email can't make Jarvis text)", r.message.startswith("NEEDS_CONFIRMATION"))
rt.pending = None
t.check("there is no way to text anyone else (no 'to' argument at all)", "to" not in core.get("text_me").parameters["properties"])
config.TWILIO_ACCOUNT_SID = ""
r = run("text_me", {"message": "hi"}, "text me hi")
t.check("not set up -> not offered at all (UNAVAILABLE), nothing sent", r.message.startswith("UNAVAILABLE"))

print("VIPs by voice:")
r = run("add_vip", {"who": "Sarah"}, "always call me if Sarah emails")
t.check("'always call me if Sarah emails' -> added", r.success and "sarah" in texts.vips())
t.check("...together with the ones in .env", texts.vips() == ["boss@work.com", "sarah"], texts.vips())
t.check("...kept across restarts (settings.json)", "sarah" in config.SETTINGS_FILE.read_text(encoding="utf-8"))
r = run("add_vip", {"who": "sarah"}, "always call me if sarah emails")
t.check("adding twice -> 'already'", "already" in r.message and texts.vips().count("sarah") == 1)
r = run("list_vips", {}, "who do you call me about")
t.check("list them", "boss@work.com" in r.message and "sarah" in r.message)
r = run("remove_vip", {"who": "Sarah"}, "stop calling me about Sarah")
t.check("removed", r.success and "sarah" not in texts.vips())
r = run("remove_vip", {"who": "boss"}, "stop calling me about my boss")
t.check("one from .env can't be removed by voice -> says where to change it", not r.success and ".env" in r.message)
t.done("TEXTS")
