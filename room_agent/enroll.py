"""python main.py --enroll-voice: record about 30 s of you reading and store your voiceprint (voiceprint.npz).
Only that file leaves this step; the audio itself is not saved. Everything stays on this machine."""

import time

import numpy as np

from room_agent import config
from room_agent.audio import speaker_id
from room_agent.audio.devices import setup_devices
from room_agent.audio.engine import AudioEngine, FRAME_S

TARGET_S = 30.0  # seconds of speech to collect
MIN_S = 20.0  # fewer than this is refused
GIVE_UP_S = 180.0

TEXT = """Read this out loud in your normal speaking voice, at your normal distance from the mic:

  "Hey Jarvis, what's on my schedule for tomorrow? I want to wake up at seven, so set an alarm, and remind me to call my
  mom after dinner. Honestly, I was thinking maybe pasta tonight, or something quick like a sandwich.
  Can you tell me how the weather looks this weekend? If it's nice, I'll go for a long walk by the water.
  Stop talking for a second. Actually, never mind, keep going. What's the news today, and how long until my timer is done?
  Turn the lights down a little, play something calm, and don't let me forget to take out the trash before the morning."

Keep reading (repeat it or make up your own sentences) until the bar fills."""


def enroll_voice():
    setup_devices()
    engine = AudioEngine(config.OUT_SR, aec=config.AEC, noise_suppression=config.NOISE_SUPPRESSION, barge_in=False)
    engine.start(capture=True)
    extractor = speaker_id.make_extractor()
    print(TEXT)
    print()
    input("Press Enter when you're ready, then start reading... ")
    engine.drain_mic()
    frames, voiced, started, last_shown = [], 0, time.time(), -1
    try:
        while voiced * FRAME_S < TARGET_S and time.time() - started < GIVE_UP_S:
            item = engine.mic_q.get()
            if not isinstance(item, tuple):
                continue
            pcm, voice = item
            frames.append((pcm, voice))
            voiced += voice >= 0.5
            shown = int(voiced * FRAME_S)
            if shown != last_shown:
                last_shown = shown
                bar = "#" * int(30 * min(shown / TARGET_S, 1.0))
                print(f"\r  [{bar:<30}] {shown:2d}/{int(TARGET_S)} s of speech", end="", flush=True)
    except KeyboardInterrupt:
        print("\nstopped early")
    print()
    engine.close()

    # your speech only: runs of voiced frames (gaps up to 0.4 s bridged), padded by two frames, at least 1 s long
    voice = np.array([v for _, v in frames])
    runs, start, gap = [], None, 0
    for i, v in enumerate(voice):
        if v >= 0.5:
            start, gap = (i if start is None else start), 0
        elif start is not None:
            gap += 1
            if gap > 5:
                runs.append((start, i - gap + 1))
                start, gap = None, 0
    if start is not None:
        runs.append((start, len(voice)))
    clips = [np.concatenate([f for f, _ in frames[max(a - 2, 0): z + 2]]) for a, z in runs if (z - a) * FRAME_S >= 1.0]
    seconds = sum(len(c) for c in clips) / speaker_id.SR
    if seconds < MIN_S:
        print(f"Only {seconds:.0f} s of speech was heard (need {MIN_S:.0f}). Check the mic with --list-devices / MIC_DEVICE and try again.")
        return False
    level = float(np.sqrt(np.mean(np.concatenate(clips).astype(np.float32) ** 2)))
    centroid, sims, n = speaker_id.build_voiceprint(extractor, clips)
    path = speaker_id.save_voiceprint(centroid, seconds)
    print(f"Saved your voiceprint to {path.name}: {seconds:.0f} s of speech, {n} samples, speech level {level:.0f} RMS.")
    print(f"Consistency of your own voice: average {sims.mean():.2f}, lowest {sims.min():.2f} "
          f"(accept threshold {config.SPEAKER_ACCEPT:.2f}).")
    if level < 150:
        print("Warning: your speech level is low. Raise the mic gain or move closer, then enroll again for best results.")
    if sims.min() < 0.45:
        print("Warning: some of the recording didn't sound like the rest (noise or another voice). Enroll again somewhere quiet.")
    print("Restart Jarvis to use it. Turn it off any time with SPEAKER_VERIFY=0 in .env.")
    return True
