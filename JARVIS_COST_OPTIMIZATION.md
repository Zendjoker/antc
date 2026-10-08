# Jarvis cost optimization

## Actual spend (from `spend.json` and live logs)

- **A real evening session** (2026-10-07, 11 turns): 11.4 ¢ total, mostly gpt-5-mini.
- **Per turn:** 0.07–0.36 ¢.
- **Today, 2026-10-08:** $0.12 OpenAI + $0.02 Claude, including a test run of mine that made a few real calls by mistake (reported in `JARVIS_OVERNIGHT_REPORT.md`; the test is now isolated).
- **Daily budget:** `DAILY_BUDGET_USD`. Past it, no paid model is called. The Ollama fallback after that is not live-tested.

## What drives the cost (evidence)

1. **Input tokens per call:** 7,200–9,900 live with the old code; 3,400–5,700 offline with the current code. That's most of the bill: output is 14–55 tokens per reply.
2. **Two calls per tool turn:** every live tool turn (temperature, lights, location, voice style) made 2 calls.
3. **Low cache use:** 0–8,192 cached tokens per call; often 0.
4. **Background memory work:** one summary call (~570 in / 145 out) and one learning call (~1,600 in) per conversation.

## Already done

| Change | Effect | Evidence |
|---|---|---|
| Reflexes for simple commands | 0 model calls (lights, lists, music, settings, browser open/search, stop) | Live: "Turn the light off" took 0.58 s, no call. Offline: test_lists, test_music, test_pcsettings, test_browser |
| Verified simple action ends without a 2nd call | −1 call per tool turn | Offline: test_reliability_fixes |
| Request-relevant capability list, area rules only with their tools | −290 tokens per call, and new features no longer grow every request | Offline: test_fixes (3,519 → 3,230) |
| Stable prompt opening for caching | Shared prefix 1,317 → 1,633 tokens | Offline: Stage 2 |
| Cheap model by default, smart model only for complex requests | gpt-5-mini by default | Live: "model: gpt-5-mini (default)" |
| Research and search via DuckDuckGo, no paid search API | $0 search | — |
| Screen vision only when asked; none if a web page can be read instead | 0 vision calls by default | Offline: test_screen |

## Not worth doing (yet)

- **A local model for everyday chat:** gpt-5-mini costs fractions of a cent per turn. A local model would add VRAM pressure next to Whisper, slower answers and worse tool choice. Revisit only if the monthly bill becomes material.
- **A bigger cheaper-model routing system:** the existing two-tier router is enough until live data shows misrouting.

## Next

1. **Measure the current code live** with `DIAGNOSTICS=1` for a few days: real tokens, cache hits, calls per turn.
2. **If caching stays low,** check that the first ~1,600 prompt tokens are identical between calls (OpenAI caches 1,024+ token prefixes).
3. **Tool-choice test with real models** (~50 requests, about $0.20–0.50): **needs your approval.**
4. **Memory work:** if live data shows the learning call rarely stores anything, run it only for conversations of 3+ turns.
