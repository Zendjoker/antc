"""Latency profile of what can be measured offline (no paid APIs): Jarvis's own code per turn (context assembly, the
turn pipeline with a scripted model, a deterministic smart-home command), local Whisper on recorded speech, local Piper
time-to-first-audio. Prints p50 / p95 over repeated runs.

Not measured here (needs a live session or paid calls): endpointing in a real room, the LLM's time to first token,
ElevenLabs, the speaker.   Run:  .venv\\Scripts\\python -m tests.latency_profile [--quick]
"""

import json
import os
import re
import statistics
import sys
import threading
import time

from tests.harness import setup_env

setup_env()

from room_agent import runtime as rt  # noqa: E402
from tests.harness import Conversation  # noqa: E402

QUICK = "--quick" in sys.argv
N = 10 if QUICK else 40


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def report(name, ms):
    print(f"  {name:44} p50 {pct(ms, 50):8.2f} ms   p95 {pct(ms, 95):8.2f} ms   (n={len(ms)})", flush=True)
    return {"name": name, "p50_ms": round(pct(ms, 50), 2), "p95_ms": round(pct(ms, 95), 2), "n": len(ms)}


def timed(fn, n=N):
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t0) * 1000)
    return out


results = []
print("Jarvis's own code (model replaced by a script, so model/network time is excluded):")
from room_agent import prompt  # noqa: E402
from room_agent.actions import core  # noqa: E402

core.ensure_loaded()
rt.new_turn("what time is it")
results.append(report("prompt + context assembly (simple turn)", timed(prompt.system_parts)))
from room_agent.llm.openai_backend import OpenAICall  # noqa: E402
from room_agent.tools.registry import active_tools  # noqa: E402

hist = [{"role": "user", "content": "what time is it"}]
results.append(report("request build (system prompt, tools, history)",
                      timed(lambda: OpenAICall(hist, *prompt.system_parts(), active_tools()))))
convo = Conversation()
results.append(report("whole basic turn, code only (1 scripted call)", timed(lambda: convo.say("how's it going?", reply="Good, you?"))))
results.append(report("tool turn, code only (set_timer + reply)",
                      timed(lambda: convo.say("set a timer for 3 minutes", calls=[("set_timer", {"seconds": 180})], reply="Okay."))))

# a smart-home command through the deterministic path, device answering after 50 ms (the real T1 strip answers ~100 ms)
from room_agent.tools import zigbee  # noqa: E402

B = "zigbee2mqtt"


class FakeZ2M:
    def publish(self, topic, payload):
        name = topic[len(B) + 1:-len("/set")]
        threading.Timer(0.05, lambda: zigbee.hub.handle(f"{B}/{name}", payload.encode() if isinstance(payload, str) else payload)).start()


zigbee.hub.client = FakeZ2M()
zigbee.hub.handle(f"{B}/bridge/state", b'{"state": "online"}')
zigbee.hub.handle(f"{B}/bridge/devices", json.dumps([{"friendly_name": "LED strip", "ieee_address": "0x4", "power_source": "Mains",
    "definition": {"model": "LGYCDD01LM", "description": "LED Strip T1", "exposes": [{"type": "light", "features": [
        {"property": "state"}, {"property": "brightness", "value_max": 254}]}]}}]).encode())
zigbee.hub.handle(f"{B}/LED strip", b'{"state": "OFF"}')
on = [True]


def led():
    on[0] = not on[0]
    convo.say("turn on the led strip" if on[0] else "turn off the led strip", reply="SHOULD NOT BE CALLED")


results.append(report("smart-home command, deterministic (no model), incl. 50 ms device", timed(led, max(10, N // 2))))

print("Local speech recognition (Whisper, on your recorded speech):")
try:
    import numpy as np

    from room_agent import config
    from room_agent.audio import stt

    rec = np.load(os.path.join(os.path.dirname(__file__), "stt_session.npz"), allow_pickle=True)
    sr, frame = 16000, len(rec["clean"]) // len(rec["stamps"])  # (marks are clock times; stamps say when each frame came)

    def cut(a, b):
        i, j = np.searchsorted(rec["stamps"], a), np.searchsorted(rec["stamps"], b)
        return rec["clean"][max(0, i - 4) * frame:(j + 4) * frame].astype(np.int16)  # (+-0.3 s, like the recorder keeps)

    clips = [(cut(a, b), str(txt)) for (a, b), txt in zip(rec["marks"], rec["texts"])]
    clips = [(c, t) for c, t in clips if len(c) > 1600]
    stt.load_whisper()
    stt.whisper_text(clips[0][0])  # (warm-up, as Jarvis does at start)
    ms, words_ok, words_all = [], 0, 0
    for i in range(max(len(clips), N // 4)):
        c, ref = clips[i % len(clips)]
        c = c.copy()
        c[-1] ^= i + 1  # (a different fingerprint each time, so the repeat cache doesn't hide the real cost)
        t0 = time.perf_counter()
        got = stt.whisper_text(c)
        ms.append((time.perf_counter() - t0) * 1000)
        if i < len(clips):
            rw, gw = set(re.findall(r"[a-z']+", ref.lower())), set(re.findall(r"[a-z']+", got.lower()))
            words_ok, words_all = words_ok + len(rw & gw), words_all + len(rw)
    print(f"  (accuracy on your 12 recorded sentences: {words_ok}/{words_all} reference words recognized)")
    results.append(report(f"Whisper {config.WHISPER_MODEL} on {config.WHISPER_DEVICE} ({sum(len(c) for c, _ in clips) / len(clips) / sr:.1f}s average clip)", ms))
except Exception as e:
    print(f"  (skipped: {type(e).__name__}: {e})")

print("Local voice (Piper, time to first audio chunk):")
try:
    from room_agent.audio import tts

    tts.load_piper()
    ms = []
    for i in range(max(5, N // 4)):
        t0 = time.perf_counter()
        next(iter(tts.piper_pcm(f"Okay, it's at thirty percent now, number {i}.")))
        ms.append((time.perf_counter() - t0) * 1000)
    results.append(report("Piper first audio (one sentence)", ms))
except Exception as e:
    print(f"  (skipped: {type(e).__name__}: {e})")

out = os.environ.get("LATENCY_OUT")
if out:
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
os._exit(0)
