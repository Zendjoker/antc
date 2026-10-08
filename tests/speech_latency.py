"""End-to-end spoken-response latency with the REAL model and the REAL configured voice engine, into a silent fake speaker
(nothing is played). For each request: user stop -> endpoint -> reasoning -> first sentence -> TTS -> first audio, per
stage (speech/timing.py). Results are saved to bench/speech_latency_<label>.json for before/after comparisons.

    .venv\\Scripts\\python -m tests.speech_latency before
"""

import json
import os
import statistics
import sys
import threading
import time
from types import SimpleNamespace as NS

from dotenv import dotenv_values

from tests.harness import ROOT, setup_env

label = sys.argv[1] if len(sys.argv) > 1 else "run"
real = dotenv_values(os.path.join(ROOT, ".env"))
keep = {k: v.split("#")[0].strip() for k, v in real.items() if k.startswith(("OLLAMA_", "LLM_", "TTS_", "PIPER_", "ELEVENLABS_",
                                                                              "OUTPUT_SAMPLE")) and v}
setup_env(**keep)
import logging  # noqa: E402

logging.basicConfig(level=logging.WARNING)
from room_agent import runtime as rt  # noqa: E402
from room_agent.audio import mic, speaker, tts  # noqa: E402
from room_agent.config import OUT_SR, SILENCE_S, TTS_PROVIDER  # noqa: E402


class SilentSpeaker:
    """Stands in for the audio engine: takes PCM, keeps a simulated playback clock, plays nothing."""

    def __init__(self):
        self.interrupted, self.end, self.capturing = threading.Event(), 0.0, False
        self._last_play = 0.0

    def play(self, pcm):
        now = time.time()
        self.end = max(self.end, now) + len(pcm) / 2 / OUT_SR
        self._last_play = now

    def queued_seconds(self):
        return max(0.0, self.end - time.time())

    def flush(self):
        self.end = time.time()

    def is_playing(self):
        return self.queued_seconds() > 0

    def wait_drained(self):
        pass


rt.engine, rt.tts_enabled = SilentSpeaker(), True
rt.writer = NS(observe=lambda *a, **k: None, conversation_ended=lambda *a: None, forgot=lambda *a: None)
if TTS_PROVIDER == "piper":
    tts.load_piper()
    list(tts.piper_pcm("Warming up."))  # (the first synthesis after loading is slow; a running Jarvis has done it)
threading.Thread(target=speaker.speaker_worker, daemon=True).start()
from room_agent.conversation.turn import take_turn  # noqa: E402

# the model decides for real, but nothing it does touches this PC: every state-changing action reports success unexecuted
# (reads like get_volume still run). Timing is what's measured here, not the actions.
from tests.harness import simulate_actions  # noqa: E402

simulate_actions()

REQUESTS = ["What time is it?", "Man, I'm exhausted.", "I finally fixed it!", "Bro, guess what.",
            "Explain in two sentences what a port conflict is.", "You're actually useless today, haha.",
            "Quick, something's wrong with my speakers.", "What's 15 percent of 80?"]
rows = []
for text in REQUESTS:
    now = time.time()
    rt.engine.flush()  # (the previous reply finished playing before they spoke)
    mic.last_speech_end, rt.turn_start = now - SILENCE_S, now  # (as if they stopped talking SILENCE_S ago)
    take_turn([], text, final=True)
    s = dict(rt.turn.timing)
    from room_agent.speech import timing

    summary = timing.summary(rt.turn.speech)
    rows.append({"request": text, **summary, "turn_s": round(time.time() - now, 2)})
    print(f"  {text[:44]:44} first sound {summary.get('time_to_first_sound_s', -1):5.2f}s | reasoning "
          f"{summary.get('reasoning_s', -1):5.2f}s | TTS ttfb {summary.get('tts_ttfb_s', -1):4.2f}s | queue "
          f"{summary.get('queue_s', -1):4.2f}s", flush=True)


def med(key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(statistics.median(vals), 3) if vals else None


report = {"label": label, "tts": TTS_PROVIDER, "model": os.environ.get("OLLAMA_MODEL"), "rows": rows,
          "median": {k: med(k) for k in ("time_to_first_sound_s", "endpointing_s", "reasoning_s", "queue_s", "tts_ttfb_s")}}
os.makedirs(os.path.join(ROOT, "bench"), exist_ok=True)
with open(os.path.join(ROOT, "bench", f"speech_latency_{label}.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, indent=1)
print("median:", report["median"])
os._exit(0)
