"""Choosing the microphone and speaker, and following the Windows default when it changes."""

import logging
import os
import threading
import time

import sounddevice as sd

from room_agent.config import FOLLOW_DEFAULT_DEVICE, MIC_DEVICE, SPEAKER_DEVICE

log = logging.getLogger("room-agent")
CHECK_S = 2.0  # how often the Windows default devices are looked at


def resolve_device(spec, kind):
    """Turn MIC_DEVICE / SPEAKER_DEVICE (index or name fragment) into a device index."""
    spec = spec.strip()
    if not spec:
        return None
    if spec.isdigit():
        return int(spec)
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    for i, d in enumerate(sd.query_devices()):
        if spec.lower() in d["name"].lower() and d[key] > 0:
            return i
    raise SystemExit(f"No {kind} device matching '{spec}'. Run: python main.py --list-devices")


def setup_devices():
    """MIC_DEVICE / SPEAKER_DEVICE if set, else the system default (None lets PortAudio take the default)."""
    sd.default.device = (resolve_device(MIC_DEVICE, "input"), resolve_device(SPEAKER_DEVICE, "output"))
    mic, spk = sd.default.device
    log.info("mic: %s | speaker: %s", sd.query_devices(mic, "input")["name"], sd.query_devices(spk, "output")["name"])


def windows_defaults():
    """(default mic id, default speaker id) as Windows has them right now, or None if it can't tell."""
    if os.name != "nt":
        return None
    import comtypes
    from pycaw.pycaw import AudioUtilities

    try:
        comtypes.CoInitialize()
    except OSError:
        pass
    enum = AudioUtilities.GetDeviceEnumerator()
    ids = []
    for flow in (1, 0):  # capture, render; role 0 = the "Default Device" in Windows' sound settings
        try:
            ids.append(enum.GetDefaultAudioEndpoint(flow, 0).GetId())
        except Exception:
            ids.append(None)  # (no device of that kind connected)
    return tuple(ids)


def _rescan():
    """PortAudio reads the device list (and the defaults) only when it starts: restart it to see the current ones.
    Only allowed while no stream is open (AudioEngine.reopen calls this in between)."""
    sd._terminate()
    sd._initialize()
    setup_devices()


def follow_defaults(engine):
    """When MIC_DEVICE / SPEAKER_DEVICE are blank, switch the engine to the new Windows default mic or speaker
    whenever it changes (headphones connected, default changed in Settings...)."""
    if not FOLLOW_DEFAULT_DEVICE or (MIC_DEVICE.strip() and SPEAKER_DEVICE.strip()):
        return
    try:
        last = windows_defaults()
    except Exception as e:
        log.info("can't watch the default audio devices (%s)", e)
        return
    if last is None:
        return

    def watch():
        nonlocal last
        while True:
            time.sleep(CHECK_S)
            try:
                now = windows_defaults()
            except Exception as e:
                log.debug("default device check failed: %s", e)
                continue
            mic_changed = now[0] != last[0] and not MIC_DEVICE.strip()
            spk_changed = now[1] != last[1] and not SPEAKER_DEVICE.strip()
            last = now
            if not (mic_changed or spk_changed):
                continue
            time.sleep(0.5)  # (Windows announces a new Bluetooth device a moment before it's ready)
            log.info("the Windows default %s changed: switching", " and ".join(
                k for k, c in (("mic", mic_changed), ("speaker", spk_changed)) if c))
            try:
                engine.reopen(_rescan)
            except Exception as e:
                log.error("couldn't switch audio devices (%s); retrying on the next change", e)
                last = (None, None)  # (so the next look tries again)

    threading.Thread(target=watch, daemon=True, name="audio-devices").start()
