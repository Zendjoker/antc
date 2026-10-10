"""Voice audition: the same replies, in the same moments, spoken the PREVIOUS way (A) and the NEW way (B), for BLIND
listening, plus objective checks a person can't do by ear. Fixed replies through the real social reading and
SpeechDirector: the reasoning model is never called and nothing on the PC is touched (no tools run at all).

    A  previous delivery: the semantic text as-is (+ the social layer's style tag on tag models), speed only
    B  new delivery: SpeechDirector -> renderer (tags, punctuation, validated) -> speech-only normalization,
       pronunciations, previous_text for continuity
Engines: the local voice (Piper) always; ElevenLabs when a real key is set, with the SAME seed for A and B so delivery
is the only thing that differs.

    .venv\\Scripts\\python -m tests.voice_audition            generate clips + objective checks
    .venv\\Scripts\\python -m tests.voice_audition compare    four-way ElevenLabs comparison (paid: asks first, see below)
    .venv\\Scripts\\python -m tests.voice_audition serve      blind listening page on http://127.0.0.1:8773
    .venv\\Scripts\\python -m tests.voice_audition score      unblind your ratings

compare: the same scenarios, the same voice and seed, four ways (blind, random order per item):
    baseline           the delivery and model you use today (ELEVENLABS_MODEL, or AUDITION_BASELINE_MODEL)
    v4-turbo-plain     eleven_v4_turbo, the words only (no direction)
    v4-turbo-directed  eleven_v4_turbo through the new SpeechDirector (what Jarvis sends now)
    v4-directed        eleven_v4 through the new SpeechDirector (quality reference; not a real-time model)
It prints the characters it will use and waits for --yes; each sentence's time to first audio is measured (HTTP stream,
or the Text to Dialogue WebSocket when the HTTP stream refuses a model). --selftest runs it against a local fake.

Objective checks per clip (Whisper listens back): words heard vs the semantic text (did anything get dropped,
changed, or a [tag] read out loud?), duration, speaking rate, loudness. Clips stay in bench/audition/ (gitignored,
this PC only; fixed test sentences, nothing private); delete the folder any time.
"""

import html
import json
import os
import random
import re
import sys
import time
from pathlib import Path

import numpy as np

from tests.harness import ROOT, setup_env

OUT = Path(ROOT) / "bench" / "audition"
PORT = 8773  # (8770 is the phone line, 8771 the dashboard link)
SEED = int(os.getenv("AUDITION_SEED", "4242"))

SCENARIOS = [  # (id, kind, what they said, failed turns just before, Jarvis's reply sentences, language)
    ("plain", "neutral answer", "What's on my calendar tomorrow?", 0,
     ["You've got a dentist appointment at three, then nothing until dinner."], "en"),
    ("excited", "excited user", "Bro, it finally works!", 0, ["No way, you actually got it working.", "That's huge."], "en"),
    ("low", "low-energy user", "I'm completely drained.", 0, ["Yeah, you sound wiped.", "Want me to put on something quiet?"], "en"),
    ("mistake", "frustrated user, Jarvis's mistake", "This still doesn't work.", 2,
     ["Yeah, that's on me.", "Let me fix it properly this time."], "en"),
    ("cause", "frustrated user, cause found", "ugh why is this still broken", 2,
     ["I found it.", "The backend is using the wrong port."], "en"),
    ("urgent", "urgent", "Quick, the kitchen is on fire.", 0,
     ["Turn off the stove and cover the pan with a lid.", "Don't use water."], "en"),
    ("grief", "serious news", "My uncle passed away this morning.", 0,
     ["I'm really sorry.", "Do you want to talk about it, or should I just be here?"], "en"),
    ("joke", "joking", "haha you're useless today", 0, ["I'm doing my best over here."], "en"),
    ("surprise", "surprise", "Guess who just got promoted!", 0, ["Wait, you got the promotion?", "Congratulations, seriously."], "en"),
    ("explain", "technical explanation", "Explain the port problem.", 0,
     ["Two programs tried to listen on port 8000 at the same time, so the second one couldn't start because the first was "
      "already holding it."], "en"),
    ("numbers", "numbers and dates", "How much was the invoice?", 0, ["It was $1,250, due 2026-10-09."], "en"),
    ("email", "email and URL", "Where do I send it?", 0,
     ["Send it to adam.azzouz@gmail.com, or upload it at docs.example.com/invoices."], "en"),
    ("question", "curious question", "Bro, guess what.", 0, ["What happened?"], "en"),
    ("failure", "reporting a failure", "Open Photoshop.", 0, ["I couldn't open it, it doesn't look installed."], "en"),
    ("french", "multilingual", "Salut, ça va ?", 0, ["Ça va bien, et toi ?", "Tu veux que je lance de la musique ?"], "fr"),
    ("same-joke", "same words: joking", "haha that's hilarious", 0, ["Alright. I got it."], "en"),
    ("same-grief", "same words: serious", "My grandma is in the hospital.", 0, ["Alright. I got it."], "en"),
    ("same-urgent", "same words: urgent", "Quick, there's smoke coming out of my laptop!", 0, ["Alright. I got it."], "en"),
    ("same-task", "same words: plain task", "Add milk to the shopping list.", 0, ["Alright. I got it."], "en"),
]


# ---------------------------------------------------------------------------------------------------- generation
def _moment(user, fails):
    """Put the social layer where this scenario happens (no model call)."""
    from room_agent import runtime as rt
    from room_agent import social

    social.reset()
    for _ in range(fails):
        rt.new_turn("open spotify")
        social.on_user_turn("open spotify")
        social.state.update_last(failed=True)
    rt.new_turn(user)
    social.on_user_turn(user)


def _items(sentences):
    """The queued items exactly as speaker.say() builds them (semantic text + performance), in reply order."""
    from room_agent import runtime as rt
    from room_agent import speech
    from room_agent.audio.styles import Spoken

    items = []
    for s in sentences:
        item = Spoken(s)
        delivery = getattr(rt.turn, "delivery", None)
        item.style = rt.turn_style or (delivery.style if delivery else "")
        item.delivery = delivery
        rt.turn_speech.append(s)
        rt.spoken_count += 1  # (counted before it's performed, exactly like say())
        item.performance = speech.perform(s)
        items.append((item, list(rt.turn_speech)))
    return items


def piper_clip(items, new):
    from room_agent import speech
    from room_agent.audio import tts
    from room_agent.audio.styles import Spoken
    from room_agent.social.delivery import VoiceDelivery
    from room_agent import runtime as rt

    pcm, sent = b"", []
    for item, _ in items:
        if new:  # (exactly what speaker_worker does)
            from room_agent.speech import normalize

            text, p = speech.provider_text(item, "piper", None)
            parts = []
            for part in normalize.clauses(text):
                spoken = Spoken(part)
                spoken.delivery = VoiceDelivery(pace=p.pace * rt.speech_rate, energy=p.energy)
                parts.append(spoken)
        else:
            spoken = Spoken(str(item))
            spoken.delivery = item.delivery
            parts = [spoken]
        sent.append(" / ".join(parts))
        for spoken in parts:
            pcm += b"".join(tts.piper_pcm(spoken))
    return pcm, sent


def elevenlabs_clip(items, new, model, session):
    """One request per sentence, like the live speaker. -> (pcm, texts sent, request ids)."""
    from room_agent import runtime as rt
    from room_agent.audio import speaker, styles, voices
    from room_agent.config import EL_KEY, OUT_SR
    from room_agent.social.delivery import elevenlabs_speed
    from room_agent.speech import elevenlabs as el

    pcm, sent = b"", []
    for item, so_far in items:
        rt.turn_speech.clear()
        rt.turn_speech.extend(so_far)  # (previous_text: this reply up to here)
        if new:
            body = speaker._payload(item, model)
        else:
            tag = styles.STYLE_TAGS.get(item.style or "", "") if el.caps(model)["tags"] else ""
            body = {"text": f"{tag} {item}" if tag else str(item), "model_id": model}
            speed = elevenlabs_speed(rt.speech_rate, item.delivery)
            if speed != 1.0:
                body["voice_settings"] = {"speed": speed}
        body["seed"] = SEED
        sent.append(body["text"])
        r = session.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voices.current.eleven}?output_format=pcm_{OUT_SR}",
                         headers={"xi-api-key": EL_KEY}, json=body, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"ElevenLabs {model}: status {r.status_code}: {r.text[:200]}")
        pcm += r.content[: len(r.content) // 2 * 2]
    return pcm, sent


# ---------------------------------------------------------------------------------------------------- objective checks
_whisper = {}


def heard(pcm, sr, language):
    from scipy.signal import resample_poly

    if "m" not in _whisper:
        from faster_whisper import WhisperModel

        _whisper["m"] = WhisperModel("base.en", device="cpu", compute_type="int8")  # (the one Jarvis already has)
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
    audio = resample_poly(audio, 16000 // np.gcd(16000, sr), sr // np.gcd(16000, sr)).astype(np.float32)
    segs, _ = _whisper["m"].transcribe(audio, language="en", beam_size=5, vad_filter=False)
    return " ".join(s.text.strip() for s in segs)


def _words(text):
    from room_agent.speech import normalize

    t = normalize.speech_text(text, "full").lower()
    t = re.sub(r"(\d),(\d)", r"\1\2", t)
    return re.findall(r"[a-z0-9à-ÿ']+", t.replace("’", "'"))


def wer(ref, hyp):
    r, h = _words(ref), _words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return round(d[len(h)] / max(1, len(r)), 3)



def measures(pcm, sr, semantic, language, sent=()):
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    dur = len(x) / sr
    voiced = x[np.abs(x) > 300]
    said = heard(pcm, sr, language) if language == "en" else ""  # (English listener: other languages by ear only)
    tag_words = {w.lower() for t in sent for tag in re.findall(r"\[([^\]]+)\]", t) for w in re.findall(r"[a-z]+", tag.lower())}
    leaked = sorted({w.lower() for w in re.findall(r"[a-z]+", said.lower())} & tag_words  # (a sent [tag] heard as words)
                    - {w.lower() for w in re.findall(r"[a-z]+", semantic.lower())})
    return {"duration_s": round(dur, 2), "words_per_s": round(len(semantic.split()) / max(dur, 0.01), 2),
            "loudness_dbfs": round(20 * np.log10(max(1e-9, float(np.sqrt(np.mean((voiced / 32768) ** 2))) if voiced.size else 1e-9)), 1),
            "heard": said, "wer": wer(semantic, said) if said else None, "tag_words_spoken": leaked}


# ---------------------------------------------------------------------------------------------------- run
def generate():
    setup_env(SOCIAL_MEANING="1", SETTINGS_FILE=str(OUT / "settings.json"))
    import soundfile as sf

    from room_agent import runtime as rt
    from room_agent.audio import tts, voices
    from room_agent.config import EL_KEY, EL_MODEL, OUT_SR
    from room_agent.social import meaning

    meaning.load()
    rt.tts_enabled = True
    run_id = time.strftime("%Y%m%d-%H%M%S")
    folder = OUT / run_id
    (folder / "clips").mkdir(parents=True, exist_ok=True)
    engines = [("piper", None)]
    key_ok = bool(EL_KEY) and EL_KEY not in ("...", "") and not EL_KEY.startswith("el-test")
    if key_ok:
        engines.append(("elevenlabs", EL_MODEL))
    else:
        print("ElevenLabs: no real key (ELEVENLABS_API_KEY) -> local voice only. Add the key in the dashboard Settings and "
              "run this again for the ElevenLabs A/B.")
    tts.load_piper()
    list(tts.piper_pcm("Warming up."))
    rng = random.Random(f"{run_id}-{SEED}")
    key, rows = {}, []
    import requests

    session = requests.Session()
    for sid, kind, user, fails, reply, lang in SCENARIOS:
        for engine, model in engines:
            if engine == "piper" and lang != "en":
                continue  # (the local voice is English-only)
            clips = {}
            for cond in ("A", "B"):
                _moment(user, fails)
                items = _items(reply)
                voices.current.fallback = False
                t0 = time.time()
                try:
                    pcm, sent = piper_clip(items, cond == "B") if engine == "piper" else \
                        elevenlabs_clip(items, cond == "B", model, session)
                except Exception as e:
                    print(f"  {sid} {engine} {cond}: FAILED {e}")
                    break
                gen_s = time.time() - t0
                name = f"{rng.getrandbits(48):012x}.wav"
                sf.write(folder / "clips" / name, np.frombuffer(pcm, dtype=np.int16), OUT_SR, subtype="PCM_16")
                m = measures(pcm, OUT_SR, " ".join(reply), lang, sent)
                perf = [(it.performance.direction, it.performance.pace, it.performance.why) for it, _ in items]
                clips[cond] = name
                rows.append({"scenario": sid, "kind": kind, "engine": engine, "model": model, "condition": cond, "clip": name,
                             "sent": sent, "generation_s": round(gen_s, 2), **m,
                             "director": [{"direction": d, "pace": p, "why": w} for d, p, w in perf] if cond == "B" else None})
                print(f"  {sid:12} {engine:10} {cond}  {m['duration_s']:5.2f}s  WER {m['wer'] if m['wer'] is not None else '-'}  "
                      f"{'TAG SPOKEN ' + str(m['tag_words_spoken']) if m['tag_words_spoken'] else ''}  sent: {' | '.join(sent)[:90]}")
            if len(clips) == 2:
                order = ["A", "B"]
                rng.shuffle(order)  # (blind: which one is "1" is random per item)
                key[f"{sid}|{engine}"] = {"1": order[0], "2": order[1], "clips": {"1": clips[order[0]], "2": clips[order[1]]},
                                         "kind": kind, "user": user, "reply": " ".join(reply), "engine": engine}
    (folder / "key.json").write_text(json.dumps(key, indent=1, ensure_ascii=False), encoding="utf-8")
    (folder / "measures.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    (folder / "index.html").write_text(page(key, run_id), encoding="utf-8")
    (OUT / "latest.txt").write_text(run_id, encoding="utf-8")
    summarize_measures(rows)
    print(f"\n{len(key)} blind pairs in {folder}\nListen: .venv\\Scripts\\python -m tests.voice_audition serve")


def summarize_measures(rows):
    print("\nObjective (A previous vs B new):")
    for engine in sorted({r["engine"] for r in rows}):
        for cond in ("A", "B"):
            rs = [r for r in rows if r["engine"] == engine and r["condition"] == cond]
            if not rs:
                continue
            print(f"  {engine:10} {cond}: mean WER {np.mean([r['wer'] for r in rs if r['wer'] is not None]):.3f}  | clips with a tag read aloud "
                  f"{sum(bool(r['tag_words_spoken']) for r in rs)}/{len(rs)}  | mean words/s "
                  f"{np.mean([r['words_per_s'] for r in rs]):.2f}  | mean generation {np.mean([r['generation_s'] for r in rs]):.2f}s")
        for sid in ("numbers", "email"):
            pair = {r["condition"]: r for r in rows if r["engine"] == engine and r["scenario"] == sid}
            if len(pair) == 2:
                print(f"    {sid:8} A heard: {pair['A']['heard']!r}\n    {'':8} B heard: {pair['B']['heard']!r}")


# ---------------------------------------------------------------------------------------------------- blind page
def page(key, run_id):
    cards = []
    for i, (item_id, k) in enumerate(key.items()):
        engine = "ElevenLabs" if k["engine"] == "elevenlabs" else "local voice"
        clips = "".join(
            f'''<div class="clip"><div class="lab">Version {n}</div><audio controls preload="none" src="clips/{k["clips"][n]}"></audio>
            <div class="rate">{''.join(f'<label>{q}<select data-k="{html.escape(item_id)}|{n}|{q}"><option value="">-</option>'
                                       + ''.join(f'<option>{v}</option>' for v in range(1, 6)) + '</select></label>'
                                       for q in ("human", "fits", "clear"))}</div></div>''' for n in sorted(k["clips"]))
        cards.append(f'''<section><h2>{i + 1}. {html.escape(k["kind"])} <small>{engine}</small></h2>
        <p class="ctx"><b>They said:</b> “{html.escape(k["user"])}”<br><b>Jarvis says:</b> “{html.escape(k["reply"])}”</p>
        <div class="pair{' n4' if len(k['clips']) > 2 else ''}">{clips}</div>
        <div class="pref">Which one would you rather hear here?
          {''.join(f'<label><input type="radio" name="p{i}" value="{v}" data-k="{html.escape(item_id)}|pref"> {t}</label>'
                   for v, t in [(n, f"Version {n}") for n in sorted(k["clips"])] + [("same", "No real difference")])}</div>
        <input class="note" placeholder="Anything off? (optional)" data-k="{html.escape(item_id)}|note"></section>''')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voice Audition</title><style>
:root{{--bg:#fafaf8;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e4e2dc;--accent:#2f5d50}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ecebe6;--muted:#9a9890;--card:#1e1e1c;--line:#33322e;--accent:#8cc5b2}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px 80px}}
main{{max-width:880px;margin:0 auto}} h1{{font-size:22px;margin:0 0 4px}} .sub{{color:var(--muted);margin:0 0 20px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:0 0 14px}}
h2{{font-size:16px;margin:0 0 6px}} h2 small{{color:var(--muted);font-weight:400;margin-left:6px}}
.ctx{{color:var(--muted);margin:0 0 10px}} .pair{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.pair.n4{{grid-template-columns:1fr 1fr}}
@media (max-width:640px){{.pair,.pair.n4{{grid-template-columns:1fr}}}}
.clip{{border:1px solid var(--line);border-radius:8px;padding:10px}} .lab{{font-weight:600;margin-bottom:6px}}
audio{{width:100%}} .rate{{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px;font-size:13px}} .rate select{{margin-left:4px}}
.pref{{margin-top:10px;display:flex;gap:14px;flex-wrap:wrap;align-items:center}} .note{{width:100%;margin-top:8px;padding:6px;box-sizing:border-box;
background:transparent;color:var(--fg);border:1px solid var(--line);border-radius:6px}}
.bar{{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:10px 16px;display:flex;
gap:12px;align-items:center;justify-content:center}} button{{background:var(--accent);color:var(--bg);border:0;border-radius:6px;padding:8px 16px;
font-weight:600;cursor:pointer}} #st{{color:var(--muted)}}</style></head><body><main>
<h1>Voice audition</h1><p class="sub">Run {run_id}. Each item is the same sentence in the same moment, delivered in different ways, in random order.
Rate 1-5: <b>human</b> (sounds like a person talking), <b>fits</b> (right for what they said), <b>clear</b> (every word easy to catch).
Headphones help. Nothing is revealed until you score.</p>{''.join(cards)}</main>
<div class="bar"><button id="save">Save ratings</button><span id="st"></span></div>
<script>
const KEY="audition-{run_id}";
function collect(){{const r={{}};document.querySelectorAll("[data-k]").forEach(e=>{{if(e.type==="radio"){{if(e.checked)r[e.dataset.k]=e.value}}
else if(e.value)r[e.dataset.k]=e.value}});return r}}
function restore(){{let r={{}};try{{r=JSON.parse(localStorage.getItem(KEY)||"{{}}")}}catch(e){{}}
document.querySelectorAll("[data-k]").forEach(e=>{{const v=r[e.dataset.k];if(v===undefined)return;if(e.type==="radio")e.checked=e.value===v;else e.value=v}})}}
document.addEventListener("change",()=>{{try{{localStorage.setItem(KEY,JSON.stringify(collect()))}}catch(e){{}}}});
document.getElementById("save").onclick=async()=>{{const st=document.getElementById("st");try{{const res=await fetch("ratings",{{method:"POST",
headers:{{"Content-Type":"application/json"}},body:JSON.stringify(collect())}});st.textContent=res.ok?"Saved. Run: python -m tests.voice_audition score":"Couldn't save ("+res.status+")"}}
catch(e){{st.textContent="Couldn't reach the audition server"}}}};
restore();</script></body></html>'''


def serve():
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    run_id = (OUT / "latest.txt").read_text(encoding="utf-8").strip()
    folder = OUT / run_id

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(folder), **k)

        def do_GET(self):
            if self.path.split("?")[0].endswith((".json", ".txt")):  # (the answer key stays on disk)
                self.send_error(404)
                return
            super().do_GET()

        def do_POST(self):
            if self.path != "/ratings":
                self.send_error(404)
                return
            body = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 1_000_000))
            json.loads(body)  # (valid JSON only)
            (folder / "ratings.json").write_bytes(body)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    print(f"Blind listening for run {run_id}: http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


def score():
    from math import comb

    run_id = (OUT / "latest.txt").read_text(encoding="utf-8").strip()
    folder = OUT / run_id
    key = json.loads((folder / "key.json").read_text(encoding="utf-8"))
    ratings = json.loads((folder / "ratings.json").read_text(encoding="utf-8"))
    out = {}
    for engine in sorted({k["engine"] for k in key.values()}):
        conds = sorted({k[n] for k in key.values() if k["engine"] == engine for n in k["clips"]})
        agg = {c: {q: [] for q in ("human", "fits", "clear")} for c in conds}
        pref = {**{c: 0 for c in conds}, "same": 0}
        by_kind = []
        for item_id, k in key.items():
            if k["engine"] != engine:
                continue
            for n in k["clips"]:
                for q in ("human", "fits", "clear"):
                    v = ratings.get(f"{item_id}|{n}|{q}")
                    if v:
                        agg[k[n]][q].append(int(v))
            p = ratings.get(f"{item_id}|pref")
            if p:
                pref["same" if p == "same" else k[p]] += 1
                by_kind.append((k["kind"], "same" if p == "same" else k[p], ratings.get(f"{item_id}|note", "")))
        out[engine] = {"mean": {c: {q: round(float(np.mean(v)), 2) if v else None for q, v in d.items()} for c, d in agg.items()},
                       "preferred": pref, "items": by_kind}
        names = {"A": "previous", "B": "new"} if conds == ["A", "B"] else {c: c for c in conds}
        if conds == ["A", "B"]:
            n = pref["A"] + pref["B"]
            p_value = sum(comb(n, i) for i in range(max(pref["A"], pref["B"]), n + 1)) / 2 ** n * 2 if n else 1.0
            out[engine]["sign_test_p"] = round(min(1.0, p_value), 3)
        print(f"\n{engine}:" + ("  (A = previous, B = new)" if conds == ["A", "B"] else ""))
        for q in ("human", "fits", "clear"):
            print(f"  {q:6} " + "  ".join(f"{names[c]} {out[engine]['mean'][c][q]}" for c in conds))
        print("  preferred: " + ", ".join(f"{names[c]} {pref[c]}" for c in conds) + f", no difference {pref['same']}"
              + (f"  (sign test p = {out[engine]['sign_test_p']})" if "sign_test_p" in out[engine] else ""))
        for kind, which, note in by_kind:
            print(f"    {kind:36} -> {names.get(which, which)}  {note}")
    (folder / "report.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------------------------------- four-way comparison
HTTP_BASE = "https://api.elevenlabs.io"


def _conditions():
    from room_agent.config import EL_MODEL

    base = os.getenv("AUDITION_BASELINE_MODEL", "").strip() or EL_MODEL
    return [("baseline", base, "previous"), ("v4-turbo-plain", "eleven_v4_turbo", "plain"),
            ("v4-turbo-directed", "eleven_v4_turbo", "directed"), ("v4-directed", "eleven_v4", "directed")]


def _body(item, model, mode):
    """The request body for one sentence in one condition (the voice and seed are the same for all of them)."""
    from room_agent import runtime as rt
    from room_agent import speech
    from room_agent.audio import speaker, styles
    from room_agent.social.delivery import elevenlabs_speed
    from room_agent.speech import director, elevenlabs as el, normalize

    if mode == "directed":
        return speaker._payload(item, model)
    if mode == "previous":  # (what Jarvis sent before the SpeechDirector: the social layer's style tag, speed only)
        tag = styles.STYLE_TAGS.get(item.style or "", "") if el.caps(model)["tags"] else ""
        body = {"text": f"{tag} {item}" if tag else str(item), "model_id": model}
        speed = elevenlabs_speed(rt.speech_rate, item.delivery)
        if speed != 1.0 and "speed" in el.caps(model)["settings"]:
            body["voice_settings"] = {"speed": speed}
        return body
    words = speech.clean_for_speech(str(item))  # plain: the same words, no direction at all
    level = el.caps(model)["normalize"]
    text = normalize.speech_text(words, "full", acronyms=()) if level == "numbers" else normalize.speech_text(words, "light")
    return el.request_body(text, director.SpeechPerformance(words), model)


def _synthesize(session, body, model, voice, key, sr):
    """-> (pcm, first audio s, total s, transport). HTTP stream first; the dialogue WebSocket if HTTP refuses the model."""
    from room_agent.speech import dialogue_ws, elevenlabs as el

    t0 = time.time()
    r = session.post(f"{HTTP_BASE}/v1/text-to-speech/{voice}/stream?output_format=pcm_{sr}", headers={"xi-api-key": key},
                     json=body, stream=True, timeout=(5, 60))
    if r.status_code == 200:
        first, pcm = None, b""
        for chunk in r.iter_content(4096):
            if chunk and first is None:
                first = time.time() - t0
            pcm += chunk
        return pcm[: len(pcm) // 2 * 2], first, time.time() - t0, "http"
    if r.status_code in (400, 404, 422) and el.caps(model).get("dialogue"):
        got = []
        t0 = time.time()
        _, first = dialogue_ws.speak(body["text"], key, voice, model, sr, got.append, lambda: False,
                                     reply_id=f"audition-{time.time()}")
        pcm = b"".join(got)
        return pcm[: len(pcm) // 2 * 2], first, time.time() - t0, "dialogue websocket"
    raise RuntimeError(f"ElevenLabs {model}: status {r.status_code}: {r.text[:200]}")


def compare(argv=()):
    global OUT
    selftest = "--selftest" in argv
    if selftest:  # (a throwaway folder: nothing written into the project)
        import tempfile

        OUT = Path(tempfile.mkdtemp(prefix="audition-selftest-"))
    env = {"SOCIAL_MEANING": "1", "SETTINGS_FILE": str(OUT / "settings.json")}
    if selftest:
        env.update(ELEVENLABS_API_KEY="fake-key", ELEVENLABS_MODEL="eleven_flash_v2_5", SOCIAL_MEANING="0")
    setup_env(**env)
    import requests

    from room_agent import runtime as rt
    from room_agent.audio import voices
    from room_agent.config import EL_KEY, OUT_SR
    from room_agent.speech import dialogue_ws

    global HTTP_BASE  # noqa: PLW0603
    conds = _conditions()
    scenarios = SCENARIOS[:3] if selftest else SCENARIOS
    chars = sum(len(" ".join(reply)) for _, _, _, _, reply, _ in scenarios) * len(conds)
    print(f"Four-way comparison: {len(scenarios)} scenarios x {len(conds)} conditions, about {chars} characters of your "
          f"ElevenLabs quota.\nConditions: " + "; ".join(f"{n} = {m} ({mode})" for n, m, mode in conds))
    if selftest:
        sys.path.insert(0, ROOT)
        from tests import tts_bench

        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        tts_bench.fake_elevenlabs(port)
        HTTP_BASE, dialogue_ws.BASE = f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}"
    elif "--yes" not in argv:
        print("Nothing generated. Run again with --yes to spend those characters.")
        return
    if not EL_KEY or EL_KEY in ("...",) or EL_KEY.startswith("el-test"):
        print("No ElevenLabs key (ELEVENLABS_API_KEY): nothing to compare.")
        return
    if not selftest:
        from room_agent.social import meaning

        meaning.load()
    rt.tts_enabled = True
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-compare"
    folder = OUT / run_id
    (folder / "clips").mkdir(parents=True, exist_ok=True)
    rng = random.Random(f"{run_id}-{SEED}")
    session = requests.Session()
    key, rows = {}, []
    for sid, kind, user, fails, reply, lang in scenarios:
        clips = {}
        for label, model, mode in conds:
            _moment(user, fails)
            items = _items(reply)
            voices.current.fallback = False
            pcm, sent, firsts, totals, how = b"", [], [], [], set()
            try:
                for item, so_far in items:
                    rt.turn_speech.clear()
                    rt.turn_speech.extend(so_far)
                    body = _body(item, model, mode)
                    body["seed"] = SEED
                    sent.append(body["text"])
                    part, first, total, transport = _synthesize(session, body, model, voices.current.eleven, EL_KEY, OUT_SR)
                    pcm += part
                    firsts.append(first)
                    totals.append(total)
                    how.add(transport)
            except Exception as e:
                print(f"  {sid} {label}: FAILED {e}")
                continue
            name = f"{rng.getrandbits(48):012x}.wav"
            _write_wav(folder / "clips" / name, pcm, OUT_SR)
            try:
                m = measures(pcm, OUT_SR, " ".join(reply), lang, sent)
            except ImportError:  # (no Whisper here: timing only, listening by ear)
                m = {"duration_s": round(len(pcm) / 2 / OUT_SR, 2), "heard": "", "wer": None, "tag_words_spoken": []}
            clips[label] = name
            rows.append({"scenario": sid, "kind": kind, "condition": label, "model": model, "mode": mode, "clip": name,
                         "sent": sent, "first_audio_s": [round(f, 3) if f is not None else None for f in firsts],
                         "sentence_total_s": [round(x, 3) for x in totals], "transport": sorted(how), **m})
            print(f"  {sid:12} {label:18} first audio {firsts[0] if firsts and firsts[0] is not None else float('nan'):.2f}s  "
                  f"{'/'.join(sorted(how))}  sent: {' | '.join(sent)[:80]}")
        if len(clips) >= 2:
            order = list(clips)
            rng.shuffle(order)
            numbered = {str(i + 1): c for i, c in enumerate(order)}
            key[f"{sid}|elevenlabs"] = {**numbered, "clips": {n: clips[c] for n, c in numbered.items()}, "kind": kind,
                                        "user": user, "reply": " ".join(reply), "engine": "elevenlabs"}
    (folder / "key.json").write_text(json.dumps(key, indent=1, ensure_ascii=False), encoding="utf-8")
    (folder / "measures.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    (folder / "index.html").write_text(page(key, run_id), encoding="utf-8")
    (OUT / "latest.txt").write_text(run_id, encoding="utf-8")
    print("\nTime to first audio per condition (median of first sentences / of all sentences):")
    for label, _, _ in conds:
        rs = [r for r in rows if r["condition"] == label]
        firsts = [r["first_audio_s"][0] for r in rs if r["first_audio_s"] and r["first_audio_s"][0] is not None]
        every = [f for r in rs for f in r["first_audio_s"] if f is not None]
        if rs:
            print(f"  {label:18} {np.median(firsts) if firsts else float('nan'):.3f}s / {np.median(every) if every else float('nan'):.3f}s"
                  f"  ({len(rs)} clips, {', '.join(sorted({t for r in rs for t in r['transport']}))})")
    dialogue_ws.close()
    print(f"\n{len(key)} blind items in {folder}\nListen: .venv\\Scripts\\python -m tests.voice_audition serve, then score")
    return key, rows


def _write_wav(path, pcm, sr):
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "generate"
    if cmd == "compare":
        compare(sys.argv[2:])
    else:
        {"generate": generate, "serve": serve, "score": score}[cmd]()
