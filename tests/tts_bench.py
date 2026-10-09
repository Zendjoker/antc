"""ElevenLabs benchmark: which model and transport give the first sound soonest, with valid audio, for the sentences
Jarvis actually says. Measures instead of trusting the docs:

    HTTP streaming   /v1/text-to-speech/{voice}/stream   (what the speaker uses today), per model
    WebSocket        /v1/text-to-dialogue/stream-input   (the real-time path for eleven_v4 / v4_turbo), one connection
                     kept open across a reply, like a conversation would
    normalization    apply_text_normalization auto vs on: latency, and how numbers come out (Whisper listens back)
    seed             the same request twice with a seed: identical audio? (for fair A/B listening)

Only talks to ElevenLabs: no reasoning model, nothing on the PC. Costs characters from your ElevenLabs quota (the
estimate is printed first; --reps lowers it).

    .venv\\Scripts\\python -m tests.tts_bench               needs ELEVENLABS_API_KEY (dashboard Settings)
    .venv\\Scripts\\python -m tests.tts_bench --selftest    against a local fake ElevenLabs: checks this script, no key
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import statistics
import threading
import time
from pathlib import Path

from tests.harness import ROOT, setup_env

SENTENCES = ["Yeah, what happened?", "No way, you actually got it working.", "Turn off the stove and cover the pan with a lid.",
             "I found it, the backend is using the wrong port.", "You've got a dentist appointment at three, then nothing until dinner.",
             "I'm really sorry. Do you want to talk about it?"]
NUMBERS = "It was $1,250, due on 10/09/2026 at 3:45 PM, about 15% more than last time."
OUT = Path(ROOT) / "bench"


def _pcm_ok(pcm):
    import numpy as np

    if len(pcm) < 2000 or len(pcm) % 2:
        return False
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(x ** 2))) > 50  # (not silence)


def http_stream(base, key, voice, model, text, sr, normalization="auto", seed=None):
    """-> {ok, ttfb_s, total_s, audio_s, status, bytes, pcm}"""
    import requests

    body = {"text": text, "model_id": model, "apply_text_normalization": normalization}
    if seed:
        body["seed"] = seed
    t0 = time.perf_counter()
    first, pcm = None, b""
    try:
        with requests.post(f"{base}/v1/text-to-speech/{voice}/stream?output_format=pcm_{sr}", headers={"xi-api-key": key},
                           json=body, stream=True, timeout=(5, 30)) as r:
            if r.status_code != 200:
                return {"ok": False, "status": r.status_code, "error": r.text[:200]}
            for chunk in r.iter_content(4096):
                if chunk and first is None:
                    first = time.perf_counter() - t0
                pcm += chunk
    except Exception as e:
        return {"ok": False, "status": None, "error": f"{e.__class__.__name__}: {e}"[:200]}
    return {"ok": _pcm_ok(pcm), "status": 200, "ttfb_s": first, "total_s": time.perf_counter() - t0,
            "audio_s": len(pcm) / 2 / sr, "bytes": len(pcm), "pcm": pcm}


async def _ws_reply(ws_base, key, voice, model, sentences, sr, normalization="auto"):
    """One connection, one reply: each sentence sent + flushed as it would be when the model produces it.
    -> {ok, connect_s, ttfb_s per sentence, audio_s per sentence}"""
    import aiohttp

    url = (f"{ws_base}/v1/text-to-dialogue/stream-input?model_id={model}&output_format=pcm_{sr}"
           f"&apply_text_normalization={normalization}")
    out = {"ok": True, "ttfb_s": [], "audio_s": [], "errors": []}
    t0 = time.perf_counter()
    async with aiohttp.ClientSession() as session:
        try:
            ws = await session.ws_connect(url, headers={"xi-api-key": key}, timeout=aiohttp.ClientWSTimeout(ws_close=5))
        except Exception as e:
            return {"ok": False, "error": f"connect: {e.__class__.__name__}: {e}"[:200]}
        out["connect_s"] = time.perf_counter() - t0
        try:
            await ws.send_json({"voices": [voice]})
            for i, sentence in enumerate(sentences):
                t = time.perf_counter()
                await ws.send_json({"inputs": [{"text": sentence + " ", "voice_id": voice, "new_turn": i == 0}]})
                await ws.send_json({"flush": True})
                first, pcm = None, b""
                while True:
                    try:
                        msg = await ws.receive(timeout=1.5 if first is not None else 20)
                    except asyncio.TimeoutError:
                        break  # (no more audio for this sentence)
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        out["errors"].append(f"socket {msg.type.name}: {getattr(ws, 'close_code', None)}")
                        out["ok"] = False
                        break
                    data = json.loads(msg.data)
                    if data.get("error"):
                        out["errors"].append(f"{data.get('error')}: {data.get('message')}"[:200])
                        out["ok"] = False
                        break
                    if data.get("audio"):
                        if first is None:
                            first = time.perf_counter() - t
                        pcm += base64.b64decode(data["audio"])
                    if data.get("is_final_audio_for_turn") or data.get("is_final"):
                        break
                if not out["ok"]:
                    break
                out["ttfb_s"].append(first)
                out["audio_s"].append(len(pcm) / 2 / sr)
                out["ok"] = out["ok"] and first is not None and _pcm_ok(pcm)
            await ws.send_json({"close_socket": True})
        finally:
            await ws.close()
    return out


def ws_reply(*a, **k):
    return asyncio.run(_ws_reply(*a, **k))


def sec(x):
    return f"{x}s" if x is not None else "-"


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 3) if xs else None


def run(base, ws_base, key, voice, models, ws_models, sr, reps, listen=True):
    results = {"when": time.strftime("%Y-%m-%d %H:%M"), "voice": voice, "http": {}, "websocket": {}, "normalization": {}, "seed": {}}
    for model in models:
        rows = [http_stream(base, key, voice, model, s, sr) for _ in range(reps) for s in SENTENCES]
        good = [r for r in rows if r["ok"]]
        results["http"][model] = {"ok": f"{len(good)}/{len(rows)}", "ttfb_median_s": med([r["ttfb_s"] for r in good]),
                                  "ttfb_p90_s": round(sorted(r["ttfb_s"] for r in good)[int(len(good) * 0.9) - 1], 3) if good else None,
                                  "errors": sorted({f"{r.get('status')}: {r.get('error', '')[:120]}" for r in rows if not r["ok"]})}
        print(f"  HTTP stream  {model:22} ok {results['http'][model]['ok']:6} first audio median "
              f"{sec(results['http'][model]['ttfb_median_s'])}  {results['http'][model]['errors'][:1]}")
    for model in ws_models:
        replies = [ws_reply(ws_base, key, voice, model, SENTENCES[:3], sr) for _ in range(reps)]
        good = [r for r in replies if r.get("ok")]
        firsts = [r["ttfb_s"][0] for r in good if r["ttfb_s"]]
        later = [x for r in good for x in r["ttfb_s"][1:]]
        results["websocket"][model] = {"ok": f"{len(good)}/{len(replies)}", "connect_median_s": med([r.get("connect_s") for r in good]),
                                       "first_sentence_ttfb_median_s": med(firsts), "later_sentences_ttfb_median_s": med(later),
                                       "errors": sorted({e for r in replies for e in ([r["error"]] if r.get("error") else r.get("errors", []))})}
        w = results["websocket"][model]
        print(f"  WebSocket    {model:22} ok {w['ok']:6} connect {sec(w['connect_median_s'])}, first sentence "
              f"{sec(w['first_sentence_ttfb_median_s'])}, later {sec(w['later_sentences_ttfb_median_s'])}  {w['errors'][:1]}")
    for model in models:
        row = {}
        for norm in ("auto", "on"):
            r = http_stream(base, key, voice, model, NUMBERS, sr, normalization=norm)
            row[norm] = {"ok": r["ok"], "ttfb_s": r.get("ttfb_s"), "status": r.get("status")}
            if r["ok"] and listen:
                from tests.voice_audition import heard

                row[norm]["heard"] = heard(r["pcm"], sr, "en")
        results["normalization"][model] = row
        print(f"  numbers      {model:22} auto: {row['auto'].get('heard', row['auto'])!r}\n  {'':35}on:   {row['on'].get('heard', row['on'])!r}")
    for model in models[:1]:
        a = http_stream(base, key, voice, model, SENTENCES[1], sr, seed=1234)
        b = http_stream(base, key, voice, model, SENTENCES[1], sr, seed=1234)
        same = a["ok"] and b["ok"] and hashlib.sha1(a["pcm"]).digest() == hashlib.sha1(b["pcm"]).digest()
        results["seed"][model] = {"identical_audio": same, "lengths": [a.get("bytes"), b.get("bytes")]}
        print(f"  seed         {model:22} same seed -> identical audio: {same}")
    return results


# ---------------------------------------------------------------------------------------------------- self-test
def fake_elevenlabs(port):
    """A local stand-in speaking the same protocol: HTTP stream + Text to Dialogue WebSocket, deterministic audio,
    eleven_v4 refused over HTTP (422) so the error path is exercised too."""
    from aiohttp import WSMsgType, web

    import numpy as np

    def audio(text, seed=None):
        rng = np.random.default_rng(int(hashlib.sha1(f"{text}{seed}".encode()).hexdigest()[:8], 16))
        n = 24000 * max(1, len(text) // 15) // 4
        return (np.sin(np.arange(n) / 9.0) * 3000 + rng.normal(0, 200, n)).astype(np.int16).tobytes()

    async def stream(request):
        if request.headers.get("xi-api-key") != "fake-key":
            return web.json_response({"detail": "invalid key"}, status=401)
        body = await request.json()
        if body["model_id"] == "eleven_v4":
            return web.json_response({"detail": "model not available on this endpoint"}, status=422)
        resp = web.StreamResponse()
        await resp.prepare(request)
        await asyncio.sleep(0.08)
        pcm = audio(body["text"], body.get("seed"))
        for i in range(0, len(pcm), 4096):
            await resp.write(pcm[i:i + 4096])
        await resp.write_eof()
        return resp

    async def dialogue(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if request.headers.get("xi-api-key") != "fake-key":
            await ws.send_json({"error": "authentication_required", "message": "no key", "code": 1008})
            await ws.close()
            return ws
        buffered = ""
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                break
            data = json.loads(msg.data)
            if "inputs" in data:
                buffered += "".join(x["text"] for x in data["inputs"])
            if data.get("flush") and buffered:
                await asyncio.sleep(0.05)
                pcm = audio(buffered)
                for i in range(0, len(pcm), 8192):
                    await ws.send_json({"audio": base64.b64encode(pcm[i:i + 8192]).decode(), "alignment": None})
                await ws.send_json({"is_final_audio_for_turn": True})
                buffered = ""
            if data.get("close_socket"):
                await ws.send_json({"is_final": True})
                await ws.close()
        return ws

    app = web.Application()
    app.router.add_post("/v1/text-to-speech/{voice}/stream", stream)
    app.router.add_get("/v1/text-to-dialogue/stream-input", dialogue)
    loop = asyncio.new_event_loop()
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", port).start())
    threading.Thread(target=loop.run_forever, daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--models", default="")
    a = ap.parse_args()
    setup_env()
    from room_agent.config import EL_FALLBACK_MODEL, EL_KEY, EL_MODEL, EL_VOICE, OUT_SR

    models = [m for m in (a.models.split(",") if a.models else [EL_MODEL, "eleven_v4", EL_FALLBACK_MODEL]) if m]
    models = list(dict.fromkeys(models))
    ws_models = [m for m in models if m.startswith("eleven_v4") or m.startswith("eleven_v3")]
    if a.selftest:
        fake_elevenlabs(8772)
        time.sleep(0.3)
        res = run("http://127.0.0.1:8772", "ws://127.0.0.1:8772", "fake-key", "voice1", models, ws_models, OUT_SR, 1, listen=False)
        bad = run("http://127.0.0.1:8772", "ws://127.0.0.1:8772", "wrong-key", "voice1", models[:1], models[:1], OUT_SR, 1, listen=False)
        ok = (res["http"][models[0]]["ok"] == f"{len(SENTENCES)}/{len(SENTENCES)}" and res["http"]["eleven_v4"]["ok"].startswith("0/")
              and "422" in res["http"]["eleven_v4"]["errors"][0] and res["websocket"][models[0]]["ok"] == "1/1"
              and res["websocket"][models[0]]["later_sentences_ttfb_median_s"] is not None and res["seed"][models[0]]["identical_audio"]
              and bad["http"][models[0]]["ok"].startswith("0/") and bad["websocket"][models[0]]["ok"] == "0/1")
        print("SELFTEST", "PASSED: the benchmark measures, records refusals and bad keys, reads the WebSocket protocol"
              if ok else "FAILED")
        raise SystemExit(0 if ok else 1)
    if not EL_KEY or EL_KEY in ("...",):
        raise SystemExit("No ElevenLabs key: add ELEVENLABS_API_KEY in the dashboard Settings (or .env), then run this again.")
    chars = sum(map(len, SENTENCES)) * a.reps * len(models) + sum(map(len, SENTENCES[:3])) * a.reps * len(ws_models) \
        + len(NUMBERS) * 2 * len(models) + len(SENTENCES[1]) * 2
    print(f"ElevenLabs benchmark: models {models}, WebSocket {ws_models}, {a.reps} reps -> about {chars} characters of quota")
    res = run("https://api.elevenlabs.io", "wss://api.elevenlabs.io", EL_KEY, EL_VOICE, models, ws_models, OUT_SR, a.reps)
    OUT.mkdir(exist_ok=True)
    (OUT / "tts_bench.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    paths = [(f"HTTP {m}", v["ttfb_median_s"]) for m, v in res["http"].items() if v["ttfb_median_s"]] + \
            [(f"WebSocket {m} (later sentences)", v["later_sentences_ttfb_median_s"]) for m, v in res["websocket"].items()
             if v["later_sentences_ttfb_median_s"]]
    if paths:
        best = min(paths, key=lambda p: p[1])
        print(f"\nFastest first audio: {best[0]} at {best[1]}s (saved to bench/tts_bench.json)")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
