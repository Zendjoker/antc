"""Choosing the microphone and speaker, and following the Windows default when it changes."""

import logging
import os
import threading
import time

import sounddevice as sd

from room_agent.config import FOLLOW_DEFAULT_DEVICE, MIC_DEVICE, SPEAKER_DEVICE

log = logging.getLogger("room-agent")
CHECK_S = 2.0  # how often the Windows default devices are looked at
# What's in use now: MIC_DEVICE / SPEAKER_DEVICE at startup, changed live by switch() (the dashboard's settings).
chosen = {"input": MIC_DEVICE, "output": SPEAKER_DEVICE}
_ALIASES = ("microsoft sound mapper", "primary sound")  # Windows' "whatever the default is" entries


def _channels(kind):
    return "max_input_channels" if kind == "input" else "max_output_channels"


def resolve_device(spec, kind):
    """Turn MIC_DEVICE / SPEAKER_DEVICE (index or name fragment) into a device index. An exact name wins over a
    fragment, so "Headphones" doesn't pick "Headphones (2- Shure MV6)" when both exist."""
    spec = spec.strip()
    if not spec:
        return None
    if spec.isdigit():
        return int(spec)
    devices = [(i, d) for i, d in enumerate(sd.query_devices()) if d[_channels(kind)] > 0]
    for i, d in devices:
        if d["name"].strip().lower() == spec.lower():
            return i
    for i, d in devices:
        if spec.lower() in d["name"].lower():
            return i
    raise SystemExit(f"No {kind} device matching '{spec}'. Run: python main.py --list-devices")


def setup_devices():
    """The chosen mic / speaker if set, else the system default (None lets PortAudio take the default)."""
    sd.default.device = (resolve_device(chosen["input"], "input"), resolve_device(chosen["output"], "output"))
    mic, spk = sd.default.device
    log.info("mic: %s | speaker: %s", sd.query_devices(mic, "input")["name"], sd.query_devices(spk, "output")["name"])


def device_options(kind, rescan=False):
    """The connected mics ("input") or speakers ("output") as [{"value", "label"}], for the settings page. One entry
    per device (Windows lists each one once per audio API): the value is the name resolve_device() finds first,
    the label its full name (the first API, MME, cuts names at 31 characters). Only call rescan=True from a
    process with no audio stream open (the dashboard), PortAudio can't rescan under an open stream."""
    if rescan:
        sd._terminate()
        sd._initialize()
    devices = [d for d in sd.query_devices() if d[_channels(kind)] > 0]
    api = sd.default.hostapi  # (Windows: MME, the API the agent's streams use)
    out, seen = [], set()
    for d in devices:
        name = d["name"].strip()
        if d["hostapi"] != api or name.lower().startswith(_ALIASES) or name.lower() in seen:
            continue
        seen.add(name.lower())
        full = max((o["name"].strip() for o in devices if o["name"].strip().startswith(name)), key=len, default=name)
        out.append({"value": name, "label": full})
    return out


def switch(engine, mic=None, speaker=None):
    """Use another mic and/or speaker right now (None = leave that one as it is, "" = the Windows default).
    Checks that both exist before touching the running streams; on failure nothing changes."""
    new = dict(chosen)
    if mic is not None:
        new["input"] = str(mic).strip()
    if speaker is not None:
        new["output"] = str(speaker).strip()
    if new == chosen:
        return
    old = dict(chosen)

    def choose():
        sd._terminate()
        sd._initialize()
        for kind in ("input", "output"):
            try:
                resolve_device(new[kind], kind)
            except SystemExit as e:
                raise ValueError(str(e)) from None
        chosen.update(new)
        setup_devices()

    try:
        engine.reopen(choose)
    except Exception:
        chosen.update(old)
        engine.reopen(setup_devices)  # (back on the devices that worked)
        raise


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
    """While the mic / speaker setting is blank, switch the engine to the new Windows default mic or speaker
    whenever it changes (headphones connected, default changed in Settings...). Watches all the time, so a device
    set back to "System default" from the dashboard follows Windows again without a restart."""
    if not FOLLOW_DEFAULT_DEVICE:
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
            mic_changed = now[0] != last[0] and not chosen["input"].strip()
            spk_changed = now[1] != last[1] and not chosen["output"].strip()
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
