"""Record a short reading session from your real mic, with the same audio pipeline Jarvis uses, so speech-to-text
settings can be compared on your own voice:  python tests/stt_capture.py   (about 4 minutes)
A local voice says each sentence through the speaker, then beeps: repeat it right after the beep, in the style it
asks for. An item is repeated if the mic barely heard you."""
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from room_agent import config  # noqa: E402
from room_agent.audio.devices import setup_devices  # noqa: E402
from room_agent.audio.engine import AudioEngine  # noqa: E402
from room_agent.audio.sounds import tone  # noqa: E402
from room_agent.audio.tts import piper_pcm  # noqa: E402

ITEMS = [
    ("natural", "What's the weather like in San Francisco today?", "Say it naturally."),
    ("natural", "Can you remind me to call my mom after dinner?", "Say it naturally."),
    ("fast", "Set a timer for ten minutes and then tell me what time it will be.", "Say it fast."),
    ("fast", "Turn off the lights, lock the door, and turn on the fan.", "Say it fast."),
    ("with a pause in the middle", "I want to ... uh ... set an alarm for seven thirty tomorrow morning.",
     "Say it with a pause in the middle: I want to, pause, set an alarm for seven thirty tomorrow morning."),
    ("short command", "Set a timer for five seconds.", "Say it like a command."),
    ("short command", "Switch to a British voice.", "Say it like a command."),
    ("short command", "Stay quiet until I call you.", "Say it like a command."),
    ("long, natural", "Yesterday I went to the store to buy some groceries, but I forgot my wallet at home, so I had to drive "
                      "all the way back and then return to the store again.", "Say it naturally."),
    ("long, with a pause", "So basically what I'm trying to say is ... that I need you to wake me up at six in the morning, "
                           "and if I don't answer, keep calling until I do.",
     "Say it with a pause after the word is: So basically what I'm trying to say is, pause, that I need you to wake me up at "
     "six in the morning, and if I don't answer, keep calling until I do."),
    ("natural", "Hey Jarvis, what's in the news today?", "Say it naturally."),
    ("natural", "Remind me in fifteen minutes to move my car.", "Say it naturally."),
]
HEARD = 120  # the loudest block after the beep must reach this RMS, otherwise I don't believe you spoke

setup_devices()
engine = AudioEngine(config.OUT_SR, aec=config.AEC, noise_suppression=config.NOISE_SUPPRESSION, barge_in=False)
raw, clean, voice, stamps, flags = [], [], [], [], []

_clean = engine._clean


def clean_and_keep(block):
    out = _clean(block)
    raw.append(block.copy())
    clean.append(out.copy())
    stamps.append(time.time())
    return out


_put = engine.mic_q.put


def put_and_keep(item, *a, **k):
    voice.append(item[1])
    return _put(item, *a, **k)


_on_mic = engine._on_mic


def on_mic(indata, frames, t, status):
    if status:
        flags.append(str(status))
    _on_mic(indata, frames, t, status)


engine._clean, engine.mic_q.put, engine._on_mic = clean_and_keep, put_and_keep, on_mic
engine.start(capture=True)
time.sleep(2)
print(f"\nSettings: AEC={config.AEC} noise suppression={config.NOISE_SUPPRESSION} SILENCE_S={config.SILENCE_S}")
def say(text):
    engine.play(b"".join(piper_pcm(text)))
    time.sleep(0.2)
    while engine.is_playing():
        time.sleep(0.05)


def loudest(start, end):
    lo, hi = np.searchsorted(stamps, start), np.searchsorted(stamps, end)
    levels = [np.sqrt(np.mean(c.astype(np.float32) ** 2)) for c in clean[lo:hi]]
    return max(levels) if levels else 0.0


say("Speech recording. I say a sentence, then beep. Repeat the sentence right after the beep, in the style I ask for.")
marks, kept = [], []
for i, (kind, text, how) in enumerate(ITEMS, 1):
    for attempt in range(3):
        print(f"[{i}/{len(ITEMS)}] ({kind}) attempt {attempt + 1}\n    {text}")
        say(f"Number {i}. {how if 'pause' in kind else how + ' ' + text}")
        time.sleep(0.3)
        engine.play(tone(880, 0.15))
        start = time.time() + 0.3
        time.sleep(max(7.0, 3.0 + 0.5 * len(text.split())))
        end = time.time()
        level = loudest(start, end)
        print(f"    loudest block after the beep: RMS {level:.0f}")
        if level >= HEARD:
            break
        print("    the mic barely heard anything, trying this one again")
        say("I did not hear you. Let's try that one again, a bit louder.")
    marks.append((start, end))
    kept.append((kind, text))
    print()
time.sleep(0.5)
out = pathlib.Path(__file__).with_name("stt_session.npz")
np.savez_compressed(out, raw=np.concatenate(raw), clean=np.concatenate(clean), voice=np.array(voice, dtype=np.float32),
                    stamps=np.array(stamps), marks=np.array(marks), texts=np.array([t for _, t in kept]),
                    kinds=np.array([k for k, _ in kept]), flags=np.array(flags or [""]))
print(f"saved {out.name}: {len(raw)} blocks, {len(flags)} audio-driver warnings")
engine.close()
sys.stdout.flush()
import os  # noqa: E402

os._exit(0)
