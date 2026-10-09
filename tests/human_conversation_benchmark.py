"""Human Conversation Benchmark (offline): 20 multi-turn scenarios, most taken from the Oct 9 2026 live logs, run through
the REAL turn pipeline (turn completion, reflex, cognition, policy, router, prompt/context assembly, tool selection,
executor gates, claim check, code-finished replies) with a scripted model.

What it measures: what CODE decides and gives the model at every turn: whether a pause ends the turn, which model is
picked, what the conversation state is (policy kind, intent), whether earlier turns / memories / summaries are in the
request, which tools are offered, what the executor lets through, and what code itself says. The scripted model plays
what a capable model would plausibly do at each turn, so a failure here is one that a better model alone can't fix.

What it does NOT measure: the wording, judgment or warmth of real model replies, speech recognition, live timing.

Run:  .venv\\Scripts\\python -m tests.human_conversation_benchmark      (measurement script, not part of python -m tests)
"""

import asyncio  # noqa: F401  (media's winrt shim uses it)
import datetime
import json
import threading
from collections import defaultdict
from types import SimpleNamespace as NS

from tests.harness import setup_env

setup_env()

from room_agent import config  # noqa: E402
from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core  # noqa: E402
from room_agent.llm import router  # noqa: E402
from room_agent.text import join_fragments, looks_unfinished  # noqa: E402
from room_agent.tools import media  # noqa: E402
from room_agent.tools.zigbee import hub  # noqa: E402
from tests.harness import Conversation, simulate_actions  # noqa: E402

# ---------------------------------------------------------------- fakes (no PC, no devices, no network, no cost)
class Endpoint:
    def __init__(self):
        self.level, self.muted = 0.5, 0

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, v, _):
        self.level = v

    def GetMute(self):
        return self.muted

    def SetMute(self, m, _):
        self.muted = m


EP = Endpoint()
media._endpoint = lambda: EP


class Session:
    source_app_user_model_id = "SpotifyAB.Spotify_x!Spotify"

    def __init__(self):
        self.status, self.stamp = media.PLAYING, 0

    async def try_get_media_properties_async(self):
        return NS(title="Muhuuuuu", artist="Lazare")

    def get_playback_info(self):
        return NS(playback_status=self.status)

    def get_timeline_properties(self):
        return NS(position=datetime.timedelta(seconds=40), last_updated_time=self.stamp)

    async def _do(self, fn):
        fn()
        self.stamp += 1
        return True

    def try_play_async(self):
        return self._do(lambda: setattr(self, "status", media.PLAYING))

    def try_pause_async(self):
        return self._do(lambda: setattr(self, "status", media.PAUSED))

    def try_skip_next_async(self):
        return self._do(lambda: None)

    def try_skip_previous_async(self):
        return self._do(lambda: None)


SESSION = Session()


async def _fake_session():
    return SESSION


media._session = _fake_session
B = "zigbee2mqtt"


class FakeZ2M:
    def publish(self, topic, payload):
        name, cmd = topic[len(B) + 1:-len("/set")], json.loads(payload)
        threading.Timer(0.05, lambda: hub.handle(f"{B}/{name}", json.dumps(cmd).encode())).start()


hub.client = FakeZ2M()
hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
hub.handle(f"{B}/bridge/devices", json.dumps([
    {"friendly_name": "Door sensor", "ieee_address": "0x1", "power_source": "Battery",
     "definition": {"model": "MCCGQ11LM", "vendor": "Aqara", "description": "Door and window sensor",
                    "exposes": [{"property": "contact"}]}},
    {"friendly_name": "LED strip", "ieee_address": "0x4", "power_source": "Mains (single phase)",
     "definition": {"model": "LGYCDD01LM", "vendor": "Aqara", "description": "LED Strip T1",
                    "exposes": [{"type": "light", "features": [{"name": "state", "property": "state"},
                                                               {"name": "brightness", "property": "brightness",
                                                                "value_max": 254}]}]}}]).encode())
hub.handle(f"{B}/Door sensor", b'{"contact": true}')
hub.handle(f"{B}/LED strip", b'{"state": "OFF", "brightness": 200}')
simulate_actions(keep_groups=("media", "zigbee"))  # (apps, browser, missions...: report OK without running)

CLAUDE = []


def _fake_claude(history):
    CLAUDE.append(rt.turn_text)
    history.append({"role": "assistant", "content": "Okay."})


router.ask_claude = _fake_claude
convo = Conversation()

# ---------------------------------------------------------------- memory as it is on a lived-in install
mem = rt.memory
mem.set_key("name", "Adam", source="explicit")
mem.set_key("home_location", "Daly City", source="explicit")
for fact in ("Adam's sister Leila lives in Paris and they talk every Sunday",
             "Adam wants to build websites for local restaurants as a side business",
             "Adam likes to be teased a little when he wakes up (a wake-up roast)",
             "Adam's dentist appointment is on Friday at 3 pm"):
    mem.add(fact, category="fact", source="user_statement")
for s in ("Adam and Jarvis decided to focus the web-design side business on Daly City restaurants with no website, "
          "starting with three, and to build demo sites before any outreach. Open thread: pick the first three.",
          "Adam asked for the weather and the door status; nothing else.",
          "Adam played music and changed the volume a few times."):
    mem.add_summary(s)

# ---------------------------------------------------------------- bookkeeping
RESULTS = []          # (scenario, check, ok, cause, detail)
CAUSES = ("model capability", "routing", "context assembly", "memory", "turn-taking", "tool/confirmation logic",
          "response generation")


def chk(scn, name, ok, cause, detail=""):
    RESULTS.append((scn, name, bool(ok), cause, str(detail)[:220]))
    print(f"    {'ok  ' if ok else 'FAIL'} [{cause}] {name}" + (f"   ({str(detail)[:220]})" if not ok and detail else ""))


def fresh():
    rt.pending, rt.last_reply, rt.conversation_turn, rt.reply_style, rt.ack_word = None, "", None, None, ""
    return []


def turn(text, hist, scripts=None, final=False):
    """One live-like voice turn (final=False: code's need note is appended, as on every voice turn). -> facts."""
    CLAUDE.clear()
    rt.turn.via = ""
    results, system, tools = convo.say(text, history=hist, scripts=scripts if scripts is not None else [{"text": "Okay."}],
                                       final=final)
    reqs = list(convo.requests)
    first = reqs[0] if reqs else {}
    if CLAUDE:
        route = "claude"
    elif not reqs:
        route = "reflex" if getattr(rt.turn, "via", "") == "reflex" else "code"
    else:
        route = first.get("model")
    intent = getattr(rt.turn, "intent", None)
    return NS(results=results, system=system, tools=tools, route=route, calls=len(reqs), said=" ".join(convo.said()),
              kind=getattr(getattr(rt.turn, "policy", None), "kind", ""), level=getattr(rt.turn, "level", ""),
              intent=intent.describe() if intent else "", msgs=json.dumps(first.get("messages", []))[:200000])


def assistant(hist, text):
    hist.append({"role": "assistant", "content": text})
    rt.last_reply = text


def section(n, title, meaning):
    print(f"\n{n:2}. {title}\n    meaning: {meaning}")
    return f"{n:02d} {title}"


CHEAP, TALK = config.OPENAI_MODEL, config.OPENAI_CONVERSATION_MODEL

# ================================================================ the scenarios
s = section(1, "Hesitation: 'Can you make a research?' then the topic", "one request: research restaurants without a "
            "website in Daly City (live 07:34: answered after the first fragment, invented a topic)")
chk(s, "a Whisper-punctuated opener with no object ('Can you make a research?') waits for the rest",
    looks_unfinished("Can you make a research?"), "turn-taking", "looks_unfinished -> False: answered at once")
chk(s, "...without the question mark it does wait", looks_unfinished("Can you make a research"), "turn-taking")
h = fresh()
r = turn("Can you make a research?", h)
chk(s, "if answered alone, nothing is started from a topic-less request", not r.results, "tool/confirmation logic",
    r.results)

s = section(2, "Pause with a filler, then the rest", "'I want you to, um,' ... 'find three restaurants without "
            "websites in Daly City' = one business request")
chk(s, "'I want you to, um,' is held as unfinished", looks_unfinished("I want you to, um,"), "turn-taking")
whole = join_fragments("I want you to, um,", "find three restaurants without websites in Daly City")
h = fresh()
r = turn(whole, h, scripts=[{"tool": ("start_business_mission", {"category": "restaurants", "location": "Daly City",
                                                                  "count": 3, "website_filter": "none"})},
                            {"text": "On it - I'll find three and check each one's website."}])
chk(s, "the joined request reads as a business mission", "business" in r.intent or "route=business" in r.intent,
    "routing", r.intent)
chk(s, "...and the mission tool is offered", "start_business_mission" in r.tools, "routing", sorted(r.tools)[:12])

s = section(3, "'Turn on the LED' heard as 'turn on the lead' after restaurant talk", "turn the LED strip on (live 07:40: "
            "announced a business mission instead)")
h = fresh()
h += [{"role": "user", "content": "find three restaurants in Daly City that don't have a website"},
      {"role": "assistant", "content": "Those are your best leads to spot the ones you can pitch a site to."}]
r = turn("turn on the lead.", h)
chk(s, "the light is switched by the fast path (no model call)", r.route == "reflex", "routing", (r.route, r.intent))
chk(s, "...or at least set_light is offered and the mission tools aren't", r.route == "reflex"
    or ("set_light" in r.tools and "start_business_mission" not in r.tools), "routing",
    ("set_light" in r.tools, "start_business_mission" in r.tools))

s = section(4, "'Close the tab' (live 07:43: closed all of Chrome)", "close only the active Chrome tab")
h = fresh()
h += [{"role": "user", "content": "open Google Chrome and search on it, what's the best thing to do in cybersecurity."},
      {"role": "assistant", "content": "Here are the results in Chrome."}]
r = turn("Close the tab.", h, scripts=[{"tool": ("close_app", {"app_name": "Google Chrome", "confidence": 0.95})},
                                       {"text": "Done."}])
chk(s, "a tool that closes ONE tab is offered", any("tab" in x for x in r.tools if x.startswith("close")),
    "tool/confirmation logic", sorted(x for x in r.tools if "close" in x or "browser" in x))
chk(s, "closing the whole browser for a tab request is refused before it runs",
    not any(str(x).startswith("OK") for x in r.results), "tool/confirmation logic", r.results)

s = section(5, "'I said close the tab, not Chrome.'", "a complaint/correction about what was just done")
r = turn("I said close the tab, not Chrome.", h)
chk(s, "read as a correction, not casual chat", r.kind == "correction", "context assembly", r.kind)
chk(s, "the earlier exchange is in the request", "close the tab" in r.msgs.lower() and "chrome" in r.msgs.lower(),
    "context assembly")

s = section(6, "Misheard place corrected", "'...in Daily City' -> 'No, I said Daly City, not daily city.'")
h = fresh()
turn("find three restaurants without websites in Daily City", h, scripts=[{"text": "Daily City - is that near Daly "
                                                                              "City in California?"}])
rt.last_reply = "Daily City - is that near Daly City in California?"
r = turn("No, I said Daly City, not daily city.", h)
chk(s, "read as a correction", r.kind == "correction", "context assembly", r.kind)
chk(s, "the original request is still in the request", "three restaurants" in r.msgs, "context assembly")
chk(s, "the mission tool is still offered for the corrected place", "start_business_mission" in r.tools, "routing",
    sorted(r.tools)[:12])

s = section(7, "Changing the timer: '10 minutes' ... 'actually make it 15'", "replace the 10-minute timer with 15")
h = fresh()
turn("Set a timer for 10 minutes", h, scripts=[{"tool": ("set_timer", {"seconds": 600})}, {"text": "Ten minutes."}])
r = turn("actually make it 15", h)
chk(s, "understood as continuing the timer, not a new topic", r.kind in ("task continuation", "correction",
                                                                         "clarification"), "context assembly", r.kind)
chk(s, "the timer tools (set and cancel) are offered", {"set_timer", "cancel_timer"} <= r.tools,
    "routing", sorted(x for x in r.tools if "timer" in x))
chk(s, "stays on the cheap model", r.route == CHEAP, "routing", r.route)

s = section(8, "Brainstorm then a short yes", "'I think we could make money building websites for restaurants' -> "
            "Jarvis offers to find some in Daly City -> 'Yeah, do it.'")
h = fresh()
r1 = turn("I think we could make money building websites for restaurants", h)
chk(s, "the idea is talked through on the conversation model", r1.route == TALK, "routing", r1.route)
assistant(h, "That's worth exploring. Want me to find a few restaurants around Daly City without a website?")
r = turn("Yeah, do it.", h, scripts=[{"tool": ("start_business_mission", {"category": "restaurants",
                                                                          "location": "Daly City", "count": 3,
                                                                          "website_filter": "none"})},
                                     {"text": "Started - I'll tell you what I find."}])
chk(s, "the agreed action's tool is offered on the 'yes'", "start_business_mission" in r.tools, "routing")
chk(s, "...and it isn't refused for lack of their own words ('do it' agreed to Jarvis's offer)",
    any(str(x).startswith("OK") for x in r.results), "tool/confirmation logic", r.results)

s = section(9, "Emotional", "'I'm really stressed about money lately.' -> listen, be warm, no tools")
h = fresh()
r = turn("I'm really stressed about money lately.", h)
chk(s, "conversation model", r.route == TALK, "routing", r.route)
chk(s, "no action tools forced into an emotional turn (<= 15 tools offered)", len(r.tools) <= 15, "context assembly",
    f"{len(r.tools)} tools: " + ", ".join(sorted(r.tools)[:20]))
chk(s, "an unrelated memory (the wake-up roast) isn't injected", "wake-up roast" not in r.system, "memory")

s = section(10, "Personal memory", "'I really miss my sister.' -> Jarvis knows Leila lives in Paris")
h = fresh()
r = turn("I really miss my sister.", h)
chk(s, "the sister memory is in the context", "Leila" in r.system, "memory")
chk(s, "conversation model", r.route == TALK, "routing", r.route)

s = section(11, "Business brainstorm uses the known project", "'Help me figure out how to get more clients.' -> "
            "the restaurant-websites side business is the context")
h = fresh()
r = turn("Help me figure out how to get more clients.", h)
chk(s, "the side-business memory is in the context", "websites for local restaurants" in r.system, "memory",
    [ln for ln in r.system.splitlines() if "relevant_persistent" in ln])
chk(s, "conversation model", r.route == TALK, "routing", r.route)

s = section(12, "Remembering a decision", "'Do you remember what we decided about the restaurant project?'")
h = fresh()
r = turn("Do you remember what we decided about the restaurant project?", h)
chk(s, "the decision (Daly City, three, demos first) is in the context", "demo sites" in r.system
    or "starting with three" in r.system, "memory", [ln[:160] for ln in r.system.splitlines() if "previous_conv" in ln])
chk(s, "conversation model", r.route == TALK, "routing", r.route)

s = section(13, "Continue the project", "'Let's continue the project we were working on.'")
h = fresh()
r = turn("Let's continue the project we were working on.", h)
chk(s, "the project and its open thread are in the context", "Open thread" in r.system or "pick the first three" in r.system,
    "memory")
chk(s, "conversation model (a project discussion)", r.route == TALK, "routing", r.route)

s = section(14, "Ambiguous reference", "'Can you handle that thing from earlier?' after an unresolved dentist mention")
h = fresh()
h += [{"role": "user", "content": "remind me to call the dentist at some point"},
      {"role": "assistant", "content": "When should I remind you?"},
      {"role": "user", "content": "not sure yet"}, {"role": "assistant", "content": "Okay, just say when."}]
r = turn("Can you handle that thing from earlier?", h)
chk(s, "the earlier exchange is in the request", "dentist" in r.msgs, "context assembly")
chk(s, "the reminder tools are offered (the thing from earlier)", any("remind" in x for x in r.tools), "routing",
    sorted(r.tools)[:15])
chk(s, "a stronger model for the ambiguity", r.route == TALK, "routing", r.route)

s = section(15, "Casual vs task", "'I love this song.' (chat) then 'play it louder' (task)")
h = fresh()
r = turn("I love this song.", h)
chk(s, "chat: nothing is done", not r.results, "tool/confirmation logic", r.results)
chk(s, "chat: conversation model", r.route == TALK, "routing", r.route)
r = turn("turn it up", h)
chk(s, "task: done on the fast path (no model call)", r.route == "reflex", "routing", r.route)

s = section(16, "Interruption", "Jarvis is mid-answer; they cut in: 'wait, stop'")
h = fresh()
assistant(h, "Here are a few ideas: first, you could start by")
r = turn("(I cut you off mid-reply) wait, stop", h)
chk(s, "no action is taken", not r.results, "tool/confirmation logic", r.results)
chk(s, "nothing long is said (<= 4 words)", len(r.said.split()) <= 4, "response generation", r.said)

s = section(17, "Cancelling for good (live 07:40-07:41 loop)", "cancel the mission; then 'Yes, cancel mission for "
            "good.' / 'Oh my god, yes.'")
h = fresh()
r = turn("Cancel the mission.", h, scripts=[{"tool": ("stop_mission", {}), "text": "Cancelling the mission for good."},
                                            {"text": "Cancel it for good?"}])
chk(s, "with no mission at all, nobody is asked to confirm cancelling nothing",
    not any("NEEDS_CONFIRMATION" in str(x) for x in r.results), "tool/confirmation logic", r.results)
chk(s, "'Cancelling the mission for good.' isn't spoken while it waits for a yes", "Cancelling" not in r.said,
    "response generation", r.said)
rt.last_reply = "Cancel it for good?"
r = turn("Yes, cancel mission for good.", h, scripts=[{"tool": ("stop_mission", {})}, {"text": "Done."}])
chk(s, "'Yes, cancel mission for good.' is accepted as the yes", not any("NEEDS_CONFIRMATION" in str(x) for x in r.results),
    "tool/confirmation logic", r.results)
r = turn("Oh my god, yes.", h, scripts=[{"tool": ("stop_mission", {})}, {"text": "Done."}])
chk(s, "'Oh my god, yes.' is accepted as the yes", not any("NEEDS_CONFIRMATION" in str(x) for x in r.results),
    "tool/confirmation logic", r.results)

s = section(18, "Concise commands", "'Turn off music.' / 'Put volume 50%.' -> just do it, a word at most")
h = fresh()
SESSION.status = media.PLAYING
r = turn("Turn off music.", h, scripts=[{"tool": ("play_pause", {"action": "pause"})}, {"text": "Paused the music."}])
chk(s, "'Turn off music.' runs on the fast path (no model call)", r.calls == 0, "routing", f"{r.calls} model calls")
chk(s, "...or at most one model call", r.calls <= 1, "response generation", f"{r.calls} model calls, said {r.said!r}")
r = turn("Put volume 50%.", h, scripts=[{"tool": ("set_volume", {"percent": 50})}, {"text": "Done."}])
chk(s, "'Put volume 50%.' runs on the fast path", r.calls == 0, "routing", f"{r.calls} model calls")
chk(s, "the reply doesn't repeat their command back ('it's at 50')", "50" not in r.said, "response generation", r.said)

s = section(19, "Style instruction, then a command", "'Just say alright, you don't have to repeat everything that I "
            "say.' then 'Lower the volume, please.'")
h = fresh()
r = turn("Just say alright, you don't have to repeat everything that I say.", h,
         scripts=[{"text": "Alright."}])
chk(s, "the request is kept (session style or a saved preference)", rt.reply_style == "minimal" or "learn_preference"
    in r.tools, "memory", (rt.reply_style, "learn_preference" in r.tools))
r = turn("Lower the volume, please.", h, scripts=[{"tool": ("volume_down", {})}, {"text": "Alright."}])
chk(s, "the next command is acknowledged with just 'Alright.'", r.said.strip() == "Alright.", "response generation",
    r.said)

s = section(20, "'Call me boss', token usage, then 'what's my name?'", "address him as boss, keep the name Adam; "
            "report token usage")
h = fresh()
turn("Call me boss from now on", h, scripts=[{"tool": ("remember", {"content": "Prefer to be called 'boss'", "key": "name",
                                                                     "value": "boss", "category": "profile"})},
                                             {"text": "Got it, boss."}])
chk(s, "the real name stays Adam", mem.get("name") == "Adam", "memory", mem.get("name"))
r = turn("how much token did we consume", h)
chk(s, "a usage report is available (a tool or the fast path)", r.route in ("reflex", "code")
    or any("usage" in x or "token" in x for x in r.tools), "tool/confirmation logic", sorted(r.tools)[:10])
r = turn("What's my name?", h)
chk(s, "the context gives the name Adam and 'boss' only as a form of address", "name: Adam" in r.system
    or "name Adam" in r.system, "memory", [ln[:120] for ln in r.system.splitlines() if "known_user" in ln])

# ================================================================ summary
print("\n" + "=" * 100)
scen = defaultdict(list)
for sc, name, ok, cause, det in RESULTS:
    scen[sc].append(ok)
n_ok = sum(ok for *_, ok, _c, _d in [(r[0], r[1], r[2], r[3], r[4]) for r in RESULTS])
print(f"scenarios: {len(scen)} (fully passing {sum(all(v) for v in scen.values())}); "
      f"checks: {len(RESULTS)}, passed {n_ok}, failed {len(RESULTS) - n_ok}")
by = defaultdict(list)
for sc, name, ok, cause, det in RESULTS:
    if not ok:
        by[cause].append(f"{sc}: {name}")
for cause in CAUSES:
    if by[cause]:
        print(f"\n[{cause}] {len(by[cause])} failing checks")
        for x in by[cause]:
            print("   - " + x)
import os  # noqa: E402

os._exit(0)
