# Jarvis performance report

**Date:** 2026-10-08. Every number says where it came from. "Live" means your real PC; "offline" means automated tests on fakes.

## 1. Time to answer (live, old code, 11 voice turns, 2026-10-07 23:51–23:54)

| Stage | Measured |
|---|---|
| Endpointing (waiting to be sure you stopped) | 0.88 s, every turn (a fixed setting) |
| Speech recognition (Whisper large-v3-turbo, CUDA) | 0.11–0.19 s |
| Model's first words | 1.09–3.08 s |
| Voice's first audio (ElevenLabs) | 0.18–0.33 s |
| **First sound after you stopped** | **1.58–3.64 s, median ~2.0 s** |
| Simple command via reflex ("Turn the light off") | **0.58 s**, no AI call |
| Whole turn including speech | 3.9–12.6 s |

**Where the time goes:**

- **The model is 50–75% of the wait.**
- **Endpointing is a fixed 0.9 s on every turn.**
- **A tool turn costs two model calls:** tool, then reply. That added ~1.5 s ("tools 0.02 s" but "model's first words 2.5–3.1 s" on tool turns).

## 2. Offline (current code)

| What | Before | Now |
|---|---|---|
| Input tokens, simple chat | 7,185 (old measure) / 7,179–9,885 live | 3,352 |
| Input tokens, timer via AI | 9,676 over 2 calls | 4,528, 1 call |
| Input tokens, worst common case | — | 5,695 (18 tools offered) |
| Capability list per call | grew with every feature | details only for relevant areas (−290 tokens/call) |
| Piper first audio, long sentence | 1,773 ms | 1,184 ms (first-clause chunking) |

**Not measured yet: real latency or token counts for the current code.** The live process runs older code (see the audit, §0).

## 3. Resources (live process, measured now)

| What | Value |
|---|---|
| Memory | 1,295 MB, mostly the Whisper model's CUDA host memory |
| Idle CPU | 8.0% of one core, measured over 10 s while waiting for the wake word |
| Threads | 164 after 10 h |
| Startup to "Listening" | 15 s (log 23:50:15 → 23:50:30); Whisper load ~3 s |

**Threads:** 60 simulated turns added no threads beyond one per running timer (each timer ends when it fires or is cancelled). So it's not a Python-level leak. The 164 are most likely the native worker pools of the ONNX wake-word / VAD / meaning models and CTranslate2 (Whisper). Unconfirmed: a per-thread breakdown needs a native profiler.

**VRAM:** Windows doesn't report it per process (WDDM).

## 4. Tool timings (live, read-only on your PC)

| Operation | Time |
|---|---|
| Browser address bar read (accessibility) | 125 ms |
| Page text read | 36 ms |
| 11 tabs listed | 44 ms |
| Windows Search, 5 PDFs | 0.24 s |
| Spotify buttons read (274 elements) | 0.4 s |
| Zigbee light command → verified state | 0.02–0.10 s (live log) |

## 5. Improvements made

- **Startup:** Whisper now loads in the background as soon as Jarvis starts, in parallel with devices, voices and the wake-word model.
  - Expected: ~3 s less to "Listening".
  - Not measured live.
- **Simple commands skip the model entirely (reflexes):** lists, music choice, dark mode, brightness, Bluetooth, lock, browser open/search/back/scroll, emergency stop. Measured live for lights: 0.58 s vs ~2–3 s.
- **Fewer second calls:** a verified simple action ends without a second model call (offline-tested).
- **Smaller requests:** the capability list is request-relevant; area rules are only sent with their tools.

## 6. Next, with evidence needed first

1. **Measure the current code live** with `DIAGNOSTICS=1`: the `turn` and `tool` events give every number above per turn.
2. **Endpointing 0.88 s:** test 0.6 s on real speech. Risk: cutting you off mid-sentence (the unfinished-sentence logic would then matter more).
3. **Idle CPU 8%:** limit ONNX intra-op threads for the small models. Measure before/after; must not slow wake-word detection.
4. **Prompt caching:** the live log shows 0–20% of input tokens cached. The stable prompt opening (Stage 2) should raise this; verify with the `openai usage ... from cache` lines.
