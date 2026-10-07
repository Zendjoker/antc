"""Read-only reports: --check (are the services reachable?) and --memory (what does it remember?)."""

import requests

from room_agent import runtime as rt
from room_agent.audio.devices import setup_devices
from room_agent.audio.stt import load_whisper
from room_agent.audio.tts import load_piper
from room_agent.audio import voices
from room_agent.config import (DG_KEY, EL_KEY, EL_MODEL, HA_TOKEN, HA_URL, LLM_PROVIDER, MEMORY_DB, MODEL, OLLAMA_MODEL,
                               OLLAMA_URL, OUT_SR, RECENT_HOURS, STT_PROVIDER, TTS_PROVIDER, USER_NAME, WEATHER_LOCATION,
                               WHISPER_MODEL)
from room_agent.llm.client import client
from room_agent.memory import PROFILE_KEYS
from room_agent.tools.home_assistant import ha_headers
from room_agent.tools.weather import get_weather
from room_agent.tools.web import web_search


def check():
    """Verify every configured service without a full conversation."""
    ok = True

    def report(name, passed, detail=""):
        nonlocal ok
        ok &= passed
        print(f"  [{'OK ' if passed else 'FAIL'}] {name} {detail}")

    print("Checking setup...")
    if LLM_PROVIDER == "ollama":
        try:
            names = [m["name"] for m in requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).json()["models"]]
            have = OLLAMA_MODEL in names or f"{OLLAMA_MODEL}:latest" in names
            report("Ollama (local AI)", have, OLLAMA_MODEL if have else f"model missing, run: ollama pull {OLLAMA_MODEL}")
        except Exception:
            report("Ollama (local AI)", False, "not running. Install it from ollama.com and open it")
    else:
        try:
            client().models.retrieve(MODEL)
            report("Claude", True, MODEL)
        except Exception as e:
            report("Claude", False, str(e)[:150])
        from room_agent import config
        from room_agent.llm.budget import budget

        if config.OPENAI_KEY:
            try:  # (looking a model up is free: no tokens)
                from room_agent.llm.openai_backend import openai_client

                openai_client().models.retrieve(config.OPENAI_MODEL)
                report("OpenAI (cheap default model)", True, config.OPENAI_MODEL)
            except Exception as e:
                report("OpenAI (cheap default model)", False, str(e)[:150])
        elif config.LLM_DEFAULT == "openai":
            report("OpenAI (cheap default model)", False, "OPENAI_API_KEY is missing in .env")
        print(f"  [info] spent today: ${budget.total():.3f} of ${config.DAILY_BUDGET_USD:.2f} "
              f"({', '.join(f'{k} ${v:.3f}' for k, v in budget.today().items()) or 'nothing yet'})")

    if STT_PROVIDER == "whisper":
        try:
            load_whisper()
            report("Whisper (local STT)", True, WHISPER_MODEL)
        except BaseException as e:
            report("Whisper (local STT)", False, str(e))
    else:
        try:
            r = requests.get("https://api.deepgram.com/v1/projects", headers={"Authorization": f"Token {DG_KEY}"}, timeout=10)
            report("Deepgram", r.ok, "" if r.ok else f"status {r.status_code}")
        except Exception as e:
            report("Deepgram", False, str(e))

    if TTS_PROVIDER == "piper":
        try:
            load_piper()
            report("Piper (local voice)", True, voices.current.piper)
        except BaseException as e:
            report("Piper (local voice)", False, str(e))
    else:
        try:
            # A tiny real TTS call: works even for keys restricted to text-to-speech only
            r = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voices.current.eleven}?output_format=pcm_{OUT_SR}",
                headers={"xi-api-key": EL_KEY},
                json={"text": "ok", "model_id": EL_MODEL},
                timeout=15,
            )
            detail = "" if r.ok else f"status {r.status_code}: {r.text[:150]}"
            report("ElevenLabs", r.ok, detail)
        except Exception as e:
            report("ElevenLabs", False, str(e))

    if HA_URL and HA_TOKEN:
        try:
            r = requests.get(f"{HA_URL}/api/", headers=ha_headers(), timeout=10)
            report("Home Assistant", r.ok, "" if r.ok else f"status {r.status_code}")
        except Exception as e:
            report("Home Assistant", False, str(e))
    else:
        print("  [skip] Home Assistant (not configured)")

    if WEATHER_LOCATION:
        try:
            report("Weather", True, get_weather()[:80])
        except Exception as e:
            report("Weather", False, str(e))
    else:
        print("  [skip] Weather (WEATHER_LOCATION not set)")

    try:
        found = web_search("breaking news", news=True)
        report("Web search", found.startswith("OK") and "came back empty" not in found, "DuckDuckGo")
    except Exception as e:
        report("Web search", False, str(e)[:100])

    memory = rt.memory
    report("Persistent memory", memory.available,
           f"{MEMORY_DB.name}, {memory.count()} items" if memory.available else str(memory.error))
    try:
        from livekit import rtc  # noqa: F401

        report("Echo cancellation + noise suppression", True, "WebRTC")
    except ImportError:
        report("Echo cancellation + noise suppression", False, "pip install -r requirements/base.txt")

    from room_agent.audio import speaker_id

    if speaker_id.gate.active():
        report("Speaker verification", True, "only your voice can interrupt")
    else:
        print(f"  [skip] Speaker verification ({speaker_id.gate.error}); anyone's voice can interrupt")

    try:
        setup_devices()
        report("Audio devices", True)
    except BaseException as e:
        report("Audio devices", False, str(e))

    print("All good." if ok else "Fix the FAIL lines in .env, then run --check again.")
    return ok


def show_memory():
    memory = rt.memory
    if not memory.available:
        print(f"Persistent memory is unavailable: {memory.error}")
        return
    snap = memory.snapshot()
    print(f"What it remembers about {USER_NAME} ({MEMORY_DB.name}, SQLite):\n")
    for k, v in snap["profile"].items():
        print(f"  {PROFILE_KEYS.get(k, k.replace('_', ' '))}: {v}")
    for f in snap["facts"]:
        print(f"  - {f['fact']}   ({f['category']}, {'told directly' if f['source'] == 'explicit' else f['source']}, "
              f"{f.get('saved') or '?'})")
    if not (snap["profile"] or snap["facts"]):
        print("  nothing yet")
    if snap["summaries"]:
        print("\nPast conversations:")
        for x in snap["summaries"][-10:]:
            print(f"  {x.get('date', '?')}  {x['summary']}")
    recent = memory.load_recent(RECENT_HOURS)
    if recent:
        print(f"\nLast {len(recent)} messages (picked up after a restart within {RECENT_HOURS:g} hours)")
