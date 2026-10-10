"""ElevenLabs Text to Dialogue WebSocket: the documented real-time path for eleven_v4_turbo (and v3 conversational).

    wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input?model_id=...&output_format=pcm_...
    -> {"voices": [voice_id], "voice_settings": {...}}        once per connection (the key travels in the header)
    -> {"inputs": [{"text": ..., "voice_id": ..., "new_turn": true/false}]} + {"flush": true}   per sentence
    <- {"audio": base64 pcm} ... {"is_final_audio_for_turn": true} / {"is_final": true} / {"error": ...}

One connection is kept open across a reply (later sentences skip the handshake) and closed after a few idle seconds,
when the voice or model changes, or the moment speech is cancelled (you talked over it). A sentence is done at the
server's end-of-audio marker, or when no audio has come for END_IDLE_S after some did. Any problem raises DialogueError:
speaker.py then says the sentence another way (the fallback model over HTTP, then the local voice). Beta on
ElevenLabs' side ("rolling out gradually"): measure it with tests/tts_bench.py before relying on it.
"""

import asyncio
import base64
import json
import logging
import threading
import time

log = logging.getLogger("room-agent")

BASE = "wss://api.elevenlabs.io"
CONNECT_S = 5.0
FIRST_AUDIO_S = 10.0   # the first audio of a sentence must come within this
END_IDLE_S = 0.8       # after audio has come, this long without more (and no end marker) ends the sentence
IDLE_CLOSE_S = 12.0    # a connection unused this long is closed (the server drops idle ones at ~20 s)
POLL_S = 0.1


class DialogueError(Exception):
    pass


_loop = None
_loop_lock = threading.Lock()
_session = {"ws": None, "http": None, "key": None, "used": 0.0, "reply": None}
_lock = threading.Lock()  # (one sentence at a time: the speaker thread is the only caller)


def _run(coro, timeout):
    global _loop
    with _loop_lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, daemon=True, name="elevenlabs-dialogue").start()
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout)


async def _close_async():
    ws, http = _session["ws"], _session["http"]
    _session.update(ws=None, http=None, key=None, reply=None)
    try:
        if ws is not None and not ws.closed:
            try:
                await ws.send_json({"close_socket": True})
            except Exception:
                pass
            await ws.close()
    finally:
        if http is not None:
            await http.close()


def close():
    """Close the connection now (cancelled speech, a new voice): nothing more from it is played."""
    with _lock:
        if _session["ws"] is not None or _session["http"] is not None:
            try:
                _run(_close_async(), 5)
            except Exception as e:
                log.debug("dialogue socket close: %s", e)


async def _connect(api_key, voice, model, sr, normalization, settings):
    import aiohttp

    await _close_async()
    url = (f"{BASE}/v1/text-to-dialogue/stream-input?model_id={model}&output_format=pcm_{sr}"
           f"&apply_text_normalization={normalization}")
    http = aiohttp.ClientSession()
    try:
        ws = await http.ws_connect(url, headers={"xi-api-key": api_key}, timeout=aiohttp.ClientWSTimeout(ws_close=5),
                                   heartbeat=None)
        first = {"voices": [voice]}
        if settings:
            first["voice_settings"] = settings
        await ws.send_json(first)
    except Exception:
        await http.close()
        raise
    _session.update(ws=ws, http=http, key=(voice, model, sr, normalization, json.dumps(settings or {}, sort_keys=True)),
                    used=time.time(), reply=None)


async def _sentence(text, voice, new_turn, on_audio, cancelled):
    import aiohttp

    ws = _session["ws"]
    await ws.send_json({"inputs": [{"text": text + " ", "voice_id": voice, "new_turn": bool(new_turn)}]})
    await ws.send_json({"flush": True})
    played, first, t0 = 0, None, time.time()
    last = t0
    while True:
        if cancelled():
            raise DialogueError("cancelled")
        try:
            msg = await ws.receive(timeout=POLL_S)  # (short waits: being talked over is noticed within POLL_S)
        except asyncio.TimeoutError:
            if first is None and time.time() - t0 > FIRST_AUDIO_S:
                raise DialogueError("no audio came back")
            if first is not None and time.time() - last > END_IDLE_S:
                break  # (no end marker, and nothing more for a while: this sentence is done)
            continue
        if msg.type != aiohttp.WSMsgType.TEXT:
            raise DialogueError(f"socket {msg.type.name} ({getattr(ws, 'close_code', None)})")
        data = json.loads(msg.data)
        if data.get("error"):
            raise DialogueError(f"{data.get('error')}: {str(data.get('message') or '')[:160]}")
        if data.get("audio"):
            pcm = base64.b64decode(data["audio"])
            last = time.time()
            if first is None:
                first = last - t0
            if not cancelled():
                on_audio(pcm)
                played += len(pcm)
        if data.get("is_final_audio_for_turn") or data.get("is_final"):
            break
    return played, first


def speak(text, api_key, voice, model, sr, on_audio, cancelled, reply_id=None, normalization="auto", settings=None,
          timeout=30.0):
    """Say one sentence over the dialogue socket; audio goes to `on_audio(pcm)` as it arrives. -> (bytes, ttfb_s or None).
    `reply_id`: the reply this sentence belongs to (a new one starts a new turn: prosody resets). Raises DialogueError."""
    with _lock:
        key = (voice, model, sr, normalization, json.dumps(settings or {}, sort_keys=True))
        try:
            ws = _session["ws"]
            if ws is None or ws.closed or _session["key"] != key or time.time() - _session["used"] > IDLE_CLOSE_S:
                _run(_connect(api_key, voice, model, sr, normalization, settings), CONNECT_S + 1)
            new_turn = _session["reply"] != reply_id
            out = _run(_sentence(text, voice, new_turn, on_audio, cancelled), timeout)
            _session.update(used=time.time(), reply=reply_id)
            return out
        except DialogueError:
            _close_quietly()
            raise
        except Exception as e:  # (any transport problem is the same thing to the speaker: say it another way)
            _close_quietly()
            raise DialogueError(f"{e.__class__.__name__}: {str(e)[:160]}") from e


def _close_quietly():
    try:
        _run(_close_async(), 5)
    except Exception:
        _session.update(ws=None, http=None, key=None, reply=None)
