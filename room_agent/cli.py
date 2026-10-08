"""Command line entry: parses arguments, checks the setup, starts the audio engine and runs a loop."""

import argparse
import logging
import os
import sys
import threading

import sounddevice as sd

from room_agent import config
from room_agent import runtime as rt
from room_agent.audio.devices import setup_devices
from room_agent.config import EL_KEY, LLM_PROVIDER, STT_PROVIDER, TTS_PROVIDER, USER_NAME
from room_agent.diagnostics import check, show_memory
from room_agent.llm.memory_calls import memory_call_text, memory_call_tool
from room_agent.memory import MemoryWriter
from room_agent.prompt import capabilities


def main():
    p = argparse.ArgumentParser(description="Voice agent for your room")
    p.add_argument("--text", action="store_true", help="type instead of talk")
    p.add_argument("--mute", action="store_true", help="with --text: don't speak replies")
    p.add_argument("--check", action="store_true", help="verify keys and devices, then exit")
    p.add_argument("--list-devices", action="store_true", help="list audio devices and exit")
    p.add_argument("--memory", action="store_true", help="show everything it remembers about you, then exit")
    p.add_argument("--capabilities", action="store_true", help="show what it can and can't do right now, then exit")
    p.add_argument("--enroll-voice", action="store_true", help="record ~30 s of you reading, so only your voice can interrupt")
    p.add_argument("--connections", action="store_true", help="show connected accounts (Google...), then exit")
    p.add_argument("--connect", metavar="PROVIDER", help="connect an account in your browser, e.g. --connect google")
    p.add_argument("--access", default="gmail.read,calendar.read",
                   help="with --connect: what to allow, e.g. gmail.read,gmail.compose,calendar.read,calendar.write")
    p.add_argument("--setup-phone", action="store_true", help="set up phone mode (calls while you drive), see phone.md")
    p.add_argument("--test-call", action="store_true", help="phone mode: Jarvis calls your phone once, to test")
    p.add_argument("--disconnect", metavar="ACCOUNT", help="disconnect a Google account (revokes access, deletes its key)")
    a = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if a.memory:
        show_memory()
        return
    if a.setup_phone or a.test_call:
        from room_agent.phone import setup

        sys.exit(0 if (setup.setup() if a.setup_phone else setup.test_call()) else 1)
    if a.connections or a.connect or a.disconnect:
        sys.exit(_connections(a))
    if a.capabilities:
        for c in capabilities():
            print(f"  [{'yes' if c.available else ' no'}] {c.name}: {c.detail}")
        return
    if a.list_devices:
        print(sd.query_devices())
        return
    if a.enroll_voice:
        from room_agent.enroll import enroll_voice

        sys.exit(0 if enroll_voice() else 1)
    if a.check:
        sys.exit(0 if check() else 1)

    missing = [] if LLM_PROVIDER == "ollama" or os.getenv("ANTHROPIC_API_KEY") else ["ANTHROPIC_API_KEY"]
    if LLM_PROVIDER != "ollama" and config.LLM_DEFAULT == "openai" and not config.OPENAI_KEY:
        missing.append("OPENAI_API_KEY")
    if not a.text:
        missing += [] if STT_PROVIDER == "whisper" or config.DG_KEY else ["DEEPGRAM_API_KEY"]
    tts_local = TTS_PROVIDER == "piper"
    rt.tts_enabled = not a.mute and (not a.text or tts_local or bool(EL_KEY))
    if rt.tts_enabled and not tts_local and not EL_KEY:
        missing.append("ELEVENLABS_API_KEY")
    if missing:
        raise SystemExit(f"Missing in .env: {', '.join(missing)}  (copy .env.example to .env and fill it in)")

    if rt.tts_enabled or not a.text:
        from room_agent.audio.engine import AudioEngine

        setup_devices()
        rt.engine = AudioEngine(config.OUT_SR, aec=config.AEC, noise_suppression=config.NOISE_SUPPRESSION,
                                barge_in=config.BARGE_IN, speech_rms=config.SPEECH_RMS,
                                barge_ms=config.BARGE_MIN_SPEECH_MS, barge_vad=config.BARGE_VAD,
                                duck_gain=config.BARGE_DUCK_GAIN if config.BARGE_DUCK else 1.0)
        rt.engine.start(capture=not a.text)
        from room_agent.audio.devices import follow_defaults

        follow_defaults(rt.engine)  # (headphones connected later, or the default changed in Windows: switch to it)
    if rt.tts_enabled:
        from room_agent.audio.fillers import thinking_loop_worker
        from room_agent.audio.speaker import speaker_worker
        from room_agent.audio.tts import load_piper

        if tts_local:
            load_piper()
        threading.Thread(target=speaker_worker, daemon=True).start()
        threading.Thread(target=thinking_loop_worker, daemon=True).start()
    # memory is learned once per conversation (one model call instead of one per exchange); with a local model this
    # also keeps memory work from slowing a reply
    rt.writer = MemoryWriter(rt.memory, USER_NAME, memory_call_tool, memory_call_text,
                             defer=config.MEMORY_BATCH or LLM_PROVIDER == "ollama")

    from room_agent.conversation.loops import text_loop, voice_loop
    from room_agent.tools.timers import restore_timers

    restore_timers()
    from room_agent.tools.apps import warm

    warm()  # (finds the installed apps in the background)
    from room_agent.tools import location

    location.warm()  # (and where you are)
    from room_agent import control

    control.start()  # (the dashboard's live view and typed commands)
    from room_agent.tools.zigbee import hub

    hub.start()  # (Zigbee sensors and lights, if Zigbee2MQTT is here)
    if config.PHONE_MODE:
        from room_agent.phone import server

        server.start()  # (your iPhone and Twilio reach Jarvis through this; see phone.md)

    try:
        text_loop() if a.text else voice_loop()
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        # don't lose what was just said: summarize this run's last conversation and finish saving facts
        rt.writer.conversation_ended(rt.writer.last_summarized)
        rt.writer.flush(timeout=15)
        if rt.engine:
            rt.engine.close()
        sys.stdout.flush()
        os._exit(0)  # skip native-library teardown noise on exit


def _connections(a):
    """--connections / --connect google / --disconnect ACCOUNT (sign-in happens in the browser; no password here)."""
    from room_agent.integrations import provider
    from room_agent.integrations.base import IntegrationError

    g = provider("google")
    try:
        if a.connect:
            if a.connect != "google":
                print(f"Unknown provider {a.connect!r}. Available: google")
                return 1
            if not g.configured():
                print("Google isn't set up yet: add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env (see README).")
                return 1
            levels = {}
            for item in a.access.split(","):
                svc, _, level = item.strip().partition(".")
                if svc and level:
                    levels.setdefault(svc, []).append(level)
            print("Opening your browser to sign in to Google...")
            r = g.connect(levels)
            print(f"Connected {r['account']}: " + ", ".join(f"{k} ({'/'.join(v)})" for k, v in r["services"].items()))
            if r["missing"]:
                print("Not granted (you unticked them):", ", ".join(r["missing"]))
        elif a.disconnect:
            revoked = g.disconnect(a.disconnect)
            print("Disconnected" + (" and revoked at Google." if revoked else " here (Google couldn't be reached to revoke)."))
        print(f"Google: {g.connection_status()}" + (f", active account {g.active()}" if g.active() else ""))
        for acct in g.accounts():
            print(f"  {acct}: {g.connection_status(acct)}; {g.available_services(acct)}")
        return 0
    except IntegrationError as e:
        print(e.say())
        return 1
