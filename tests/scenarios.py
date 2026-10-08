"""Live scenario tests against the real model (needs the API key in .env; uses temp files, no audio):
    python tests/scenarios.py            all categories
    python tests/scenarios.py trace      three turns with the decision trace on
"""
import os
import pathlib
import re
import sys
import tempfile
import threading

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ.update(REMINDERS_FILE=str(tmp / "rem.json"), MEMORY_DB=str(tmp / "mem.db"), SETTINGS_FILE=str(tmp / "set.json"),
                  LEARNING_DB=str(tmp / "learning.db"), EXPERIENCE_DB=str(tmp / "experience.db"),
                  CONNECTIONS_FILE=str(tmp / "connections.json"), SOCIAL_MEANING="0",
                  WEATHER_LOCATION="San Francisco", TRACE="1" if "trace" in sys.argv else "0", PYTHONIOENCODING="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import logging  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")
if "trace" in sys.argv:
    logging.getLogger("room-agent").setLevel(logging.INFO)

from room_agent import runtime as rt  # noqa: E402
from room_agent.audio import tts  # noqa: E402
from room_agent.audio.fillers import LOOP  # noqa: E402
from room_agent.conversation.turn import take_turn  # noqa: E402
from room_agent.llm import claude  # noqa: E402
from room_agent.tools import registry, timers  # noqa: E402
from tests.harness import simulate_actions  # noqa: E402

simulate_actions()  # (the real model decides; the PC, apps, volume, email and calendar are never really touched)

rt.tts_enabled = True
tts.record_in_background = lambda texts: None  # don't re-record stock phrases on a voice change
import room_agent.tools.voice as voice_tools  # noqa: E402

voice_tools.tts.record_in_background = lambda texts: None
calls, spoken = [], []
_real_run_tool = claude.run_tool


def _recording_run_tool(name, args):
    out = _real_run_tool(name, args)
    calls.append((name, dict(args or {}), out))
    return out


claude.run_tool = _recording_run_tool


def _drain():
    while True:
        item = rt.speak_q.get()
        if isinstance(item, str) and item != LOOP:
            spoken.append(str(item))
        rt.speak_q.task_done()


threading.Thread(target=_drain, daemon=True).start()


def fresh():
    timers.cancel_timer("all")
    rt.pending, rt.last_reply, rt.turn_signal = None, "", ""
    return []


def turn(history, text):
    calls.clear()
    spoken.clear()
    signal = take_turn(history, text)
    return {"calls": list(calls), "reply": " ".join(spoken).strip(), "signal": signal or ""}


def ran(r, tool, **want):
    """The tool ran and succeeded (OK), with these argument values."""
    for name, args, out in r["calls"]:
        if name == tool and out.startswith("OK") and all(str(args.get(k)) == str(v) for k, v in want.items()):
            return True
    return False


def ok_tools(r):
    return [n for n, _, out in r["calls"] if out.startswith("OK")]


def asks(r):
    return "?" in r["reply"] and not ok_tools(r)


RESULTS = []


def scenario(category, name, steps, check, setup=None, carry=False):
    h = fresh()
    if setup:
        setup()
    outs, previous = [], ""
    for text in steps:
        spoken_text = f"{previous} {text}".strip() if carry and outs and outs[-1]["signal"] == "listen" else text
        outs.append(turn(h, spoken_text))
        previous = spoken_text
    try:
        ok, note = check(outs)
    except Exception as e:  # a broken check is a failure, not a crash
        ok, note = False, f"check error {e!r}"
    RESULTS.append((category, name, ok))
    tools = "; ".join(f"{n}({', '.join(f'{k}={v}' for k, v in a.items() if k != 'message')}) -> {o.split(':')[0]}"
                      for o in outs for n, a, o in o["calls"]) or "-"
    last = outs[-1]
    shown = last["reply"][:90] or (f"<{last['signal']}>" if last["signal"] else "(no reply)")
    print(f"[{'PASS' if ok else 'FAIL'}] {category:11} {name:34} {tools[:95]}\n         reply: {shown}  {note}")


def run_all():
    T = "timers"
    for phrase, secs in [("Set a timer for 5 seconds.", 5), ("can you throw a ten second timer on", 10),
                         ("timer for two minutes please", 120), ("start a 45 second countdown", 45),
                         ("set an alarm for like 8 seconds", 8)]:
        scenario(T, phrase[:34], [phrase], lambda o, s=secs: (ran(o[0], "set_timer", seconds=s), f"expect set_timer {s}s"))

    A = "alarms"
    for phrase, hhmm in [("Set an alarm for 7:30 tomorrow morning", "07:30"), ("wake me up at 6 am", "06:00"),
                         ("alarm at nine thirty tonight", "21:30")]:
        scenario(A, phrase[:34], [phrase], lambda o, t=hhmm: (ran(o[0], "set_alarm", time=t), f"expect set_alarm {t}"))
    scenario(A, "daily alarm", ["wake me up every day at 8"], lambda o: (ran(o[0], "set_alarm", time="08:00", daily=True), ""))

    R = "reminders"
    scenario(R, "relative reminder", ["remind me in 15 seconds to stretch"], lambda o: (ran(o[0], "set_timer", seconds=15), ""))
    scenario(R, "clock reminder", ["remind me at 9:30 pm to call my mom"], lambda o: (ran(o[0], "set_alarm", time="21:30"), ""))
    scenario(R, "missing time -> held -> answer", ["Remind me to turn the stove off.", "Five minutes."], carry=True,
             check=lambda o: (o[0]["signal"] == "listen" and not o[0]["reply"] and ran(o[1], "set_timer", seconds=300),
                              "held without speaking, then merged the answer"))
    scenario(R, "alarm missing time -> held -> answer", ["set an alarm", "seven thirty"], carry=True,
             check=lambda o: (o[0]["signal"] == "listen" and not o[0]["reply"] and ran(o[1], "set_alarm", time="07:30"), ""))

    M = "memory"
    scenario(M, "remember", ["Remember that my sister's name is Sara."], lambda o: (ok_tools(o[0]).count("remember") == 1, ""))
    scenario(M, "recall after remember", ["Remember my dog is called Biscuit.", "What's my dog's name?"],
             lambda o: ("biscuit" in o[1]["reply"].lower(), ""))
    scenario(M, "forget (explicit)", ["Remember my favorite color is green.", "Forget my favorite color."],
             lambda o: (any(n == "forget" for n, _, _ in o[1]["calls"]), "forget called"))
    scenario(M, "forget everything is guarded", ["Forget everything you know about me."],
             lambda o: (any(n == "forget" and (out.startswith("NEEDS_CONFIRMATION") or float(a.get("confidence", 0)) >= 0.8)
                            for n, a, out in o[0]["calls"]), "confirmed or high confidence"))

    V = "voice/modes"
    scenario(V, "switch voice (description)", ["switch to a British man voice"], lambda o: (ran(o[0], "set_voice"), ""))
    scenario(V, "switch voice (name)", ["use Laura's voice"], lambda o: (ran(o[0], "set_voice"), ""))
    scenario(V, "unknown voice is not faked", ["use the voice of Darth Vader"], lambda o: (not ran(o[0], "set_voice") or "darth" not in o[0]["reply"].lower(), ""))
    scenario(V, "talk slower", ["talk slower please"], lambda o: (ran(o[0], "set_speaking_rate"), ""))
    scenario(V, "talk softer", ["can you talk more softly"], lambda o: (ran(o[0], "set_speaking_style", style="soft"), ""))
    scenario(V, "let me finish (code handles it; the model must never go quiet)", ["let me finish my sentences"],
             lambda o: (not ran(o[0], "go_quiet"), "never quiet"))
    scenario(V, "wait longer before answering", ["wait longer before answering me"], lambda o: (ran(o[0], "set_listening_patience") and not ran(o[0], "go_quiet"), ""))
    scenario(V, "just listen is NOT quiet mode", ["just listen for a second"], lambda o: (not ran(o[0], "go_quiet"), "must not go quiet"))
    scenario(V, "hold on is NOT quiet mode", ["hold on, let me think"], lambda o: (not ran(o[0], "go_quiet"), "must not go quiet"))
    scenario(V, "quiet (explicit)", ["stay quiet until I call you"], lambda o: (ran(o[0], "go_quiet"), ""))
    scenario(V, "go to sleep", ["go to sleep"], lambda o: (ran(o[0], "go_quiet"), ""))

    W = "weather"
    scenario(W, "weather", ["what's the weather like?"], lambda o: (ran(o[0], "get_weather"), ""))
    scenario(W, "rain tomorrow", ["will it rain tomorrow?"], lambda o: (ran(o[0], "get_weather"), ""))
    scenario(W, "weather other city", ["how hot is it in Phoenix right now"], lambda o: (ran(o[0], "get_weather"), ""))

    S = "web search"
    scenario(S, "fact", ["who won the most recent Super Bowl"], lambda o: (ran(o[0], "web_search"), ""))
    scenario(S, "news", ["what's in the news today"], lambda o: (ran(o[0], "web_search", news=True), ""))

    I = "incomplete"
    for phrase in ["So basically what I'm trying to", "I was thinking maybe we could", "and then yesterday when I went to the"]:
        scenario(I, phrase[:34], [phrase], lambda o: (o[0]["signal"] == "listen" and not o[0]["reply"], "expect <listen>, no speech"))
    scenario(I, "finished by the next utterance", ["So what I'm trying to say is", "that I'd like a timer for 20 seconds"],
             lambda o: (o[0]["signal"] == "listen" and ran(o[1], "set_timer", seconds=20), "merged"), carry=True)
    for phrase in ["okay", "got it", "mm-hm", "thanks"]:
        scenario("no reply", phrase, ["What's two plus two?", phrase], lambda o: (o[1]["signal"] == "silent" and not o[1]["reply"], "expect <silent>"))

    U = "unsupported"
    for phrase in ["turn on the living room lights", "play some jazz on Spotify", "snooze my alarm for ten minutes",
                   "what's my exact GPS location right now", "text my mom that I'm running late", "open Chrome for me"]:
        scenario(U, phrase[:34], [phrase], lambda o: (not any(n in ("home_assistant",) for n, _, out in o[0]["calls"] if out.startswith("OK"))
                                                      and not re.search(r"\b(turned on|now playing|snoozed|texted|opened|sent)\b", o[0]["reply"], re.I), "no false claim"))

    F = "failures"
    real = registry.web_search

    def broken(*a, **k):
        raise RuntimeError("network down")

    registry.web_search = broken
    scenario(F, "search fails -> honest", ["who won the most recent Super Bowl"],
             lambda o: (any("FAILED" in out for _, _, out in o[0]["calls"]) and re.search(r"couldn|can't|unable|trouble|didn't|not able|isn't working|issue|problem", o[0]["reply"], re.I) is not None, "must say it failed"))
    registry.web_search = real
    real_save = timers._save
    timers._save = lambda: False
    scenario(F, "can't save -> warns", ["set a timer for 20 seconds"], lambda o: (any(out.startswith("OK") and "won't survive" in out for _, _, out in o[0]["calls"]), "tool result carries the warning"))
    timers._save = real_save

    total = len(RESULTS)
    passed = sum(ok for _, _, ok in RESULTS)
    print(f"\n{passed}/{total} passed")
    for cat, name, ok in RESULTS:
        if not ok:
            print(f"  FAILED: {cat} / {name}")


if "trace" in sys.argv:
    h = fresh()
    for text in ["Set a timer for five seconds", "set an alarm", "seven thirty", "okay"]:
        print(f"\n>>> {text}")
        r = turn(h, text)
        print(f"    spoken: {r['reply'] or ('<' + r['signal'] + '>')}")
    sys.exit(0)

run_all()
