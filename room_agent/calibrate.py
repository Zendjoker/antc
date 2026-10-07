"""Run this once: python -m room_agent.calibrate. Stay quiet 3s, then talk 3s. It tells you what SPEECH_RMS to set.
Measures through the same echo cancellation + noise suppression the agent uses.
Uses MIC_DEVICE / SPEAKER_DEVICE from .env, so set those first."""
import os
import queue
import sys
import time

import numpy as np
import sounddevice as sd

from room_agent import config
from room_agent.audio.devices import setup_devices
from room_agent.audio.engine import AudioEngine

setup_devices()
print("Mic:", sd.query_devices(sd.default.device[0], "input")["name"])
engine = AudioEngine(config.OUT_SR, aec=config.AEC, noise_suppression=config.NOISE_SUPPRESSION, barge_in=False)
engine.start()


def measure(seconds, label):
    input(f"Press Enter, then {label} for {seconds}s...")
    engine.drain_mic()
    levels, voices, t0 = [], [], time.time()
    while time.time() - t0 < seconds:
        try:
            pcm, voice = engine.mic_q.get(timeout=1)
        except (queue.Empty, TypeError, ValueError):
            continue
        levels.append(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))
        voices.append(voice)
    return np.array(levels), np.array(voices)


quiet, quiet_v = measure(3, "stay QUIET")
talk, talk_v = measure(3, "TALK normally from where you'll usually sit")
q, t = np.percentile(quiet, 95), np.median(talk)
print(f"quiet ~{q:.0f}, talking ~{t:.0f}")
print(f"voice detector: quiet room {np.mean(quiet_v > config.VAD_THRESHOLD):.0%} voice, while talking {np.mean(talk_v > config.VAD_THRESHOLD):.0%} voice")
if t < q * 1.5 or np.mean(talk_v > config.VAD_THRESHOLD) < 0.4:
    print("Warning: it can barely tell your voice from the room. Move the mic closer or check MIC_DEVICE.")
print(f"Set SPEECH_RMS={int((q + t) / 2)} in .env")
sys.stdout.flush()
os._exit(0)
