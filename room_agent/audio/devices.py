"""Choosing the microphone and speaker."""

import logging

import sounddevice as sd

from room_agent.config import MIC_DEVICE, SPEAKER_DEVICE

log = logging.getLogger("room-agent")


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
    sd.default.device = (resolve_device(MIC_DEVICE, "input"), resolve_device(SPEAKER_DEVICE, "output"))
    mic, spk = sd.default.device
    log.info("mic: %s | speaker: %s", sd.query_devices(mic, "input")["name"], sd.query_devices(spk, "output")["name"])
