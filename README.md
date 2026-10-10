# Room Agent: Claude in your room

Say "hey jarvis" and talk, either in one go ("hey Jarvis, what's the weather?") or after it answers "Yeah?". After that it stays in the conversation: no wake word needed until you go quiet for a while or tell it to be quiet. You can talk over it to interrupt.

**What it can do:** chat, tell the time, give the weather, search the web and read the news, give a daily briefing ("good morning"), set timers, alarms and wake-up calls (they survive restarts), remember things about you across restarts, change its own voice, and control lights and plugs through Home Assistant.

## Parts (~$150 to $250)

| Part | Pick | Why |
|---|---|---|
| Brain box | Mini PC (Intel N100, 8-16GB) or Raspberry Pi 5 8GB + active cooler + 27W PSU | A mini PC is faster and can run local speech-to-text. A Pi is cheaper and quieter. |
| Mic + speaker | USB conference speakerphone (Jabra Speak 410/510, Anker PowerConf S330) | The most important part. It has built-in echo cancellation and picks you up across the room. A laptop mic will make it feel dumb. |
| Storage | 64GB+ microSD or the mini PC's SSD | |
| Optional | Smart plugs / bulbs + Home Assistant | So it can actually control the room |

## 100% free mode (default)
Everything runs on your own PC: Ollama runs the AI model on your GPU, Whisper does speech-to-text, and Piper does the voice. No API keys and no monthly cost. This is set in `.env` with `LLM_PROVIDER=ollama`, `STT_PROVIDER=whisper` and `TTS_PROVIDER=piper`.
1. Install Ollama from ollama.com.
2. `ollama pull qwen3:14b` (on an 8GB GPU use `qwen3:8b`, and set `OLLAMA_MODEL` to match).
3. `pip install -r requirements/local.txt`. The Piper voice downloads itself on first run.

The paid options are Claude for better answers and ElevenLabs for a more natural voice. You can switch any part over in `.env` later.

## Running cost (paid mode)
A few dollars a month at normal use: the AI (see below), Deepgram STT (free credit to start, or use local Whisper for $0), and ElevenLabs (the ~$5/mo Starter tier is enough for one person).

### Keeping the AI cheap
- **Two models.** Everyday turns go to a cheap OpenAI model (`OPENAI_MODEL=gpt-5-mini`). Claude (`LLM_SMART`) answers when you ask for it ("ask Claude...", "think hard..."), when a request is long or complex ("help me plan...", "should I..."), and whenever OpenAI fails. The choice is made in code, with no extra model call, and logged: `model: gpt-5-mini (default)` / `model: claude-haiku-4-5 (you asked)`. Both models get the same truth rules, tools and claim checks.
- **Prompt caching.** The fixed part of every request (rules + tools, ~5,000 tokens) comes first and is billed at about 10% after the first request; only what changes per request (time, state, relevant memories) costs full price. The log shows `... from cache`.
- **Less sent per request:** the last 16 messages (`MAX_HISTORY`), tight reply ceilings.
- **Memory without a call per sentence:** facts are only looked for when you say something personal ("I...", "my...") or answer a question, once per conversation, and only conversations with real content get a summary.
- **Daily budget.** `DAILY_BUDGET_USD` (default $1) covers all paid models together. Each turn logs `turn cost: 0.05c (gpt-5-mini), today: 1.9c (...)`; `--check` shows today's total. Past the limit Jarvis says so once and switches to the free local model (Ollama) if it's running, or stops calling paid models until tomorrow.
- Estimated cost for 100 exchanges (a heavy day): about $1.09 before these changes, about $0.15 with gpt-5-mini, about $0.06 with gpt-5-nano.

## Setup on Windows

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1   # makes .venv, installs, creates .env
.\.venv\Scripts\pip install -r requirements/local.txt   # free local speech + voice
notepad .env                                          # free mode needs no keys, paid mode does
.\.venv\Scripts\python main.py --list-devices    # find your mic and speaker
.\.venv\Scripts\python main.py --check           # every line should say OK
.\.venv\Scripts\python -m room_agent.calibrate                   # put the number into SPEECH_RMS
.\.venv\Scripts\python main.py --text            # try it by typing first
.\.venv\Scripts\python main.py                   # the real thing
```
You can also double-click `run.bat` once it's set up.

## Setup on Linux (Pi / mini PC, runs on boot)

```bash
cd ~/room-agent            # copy this folder here
bash deploy/install-service.sh    # first run creates .env and stops; fill in keys, then run it again
```
The script installs dependencies, checks your keys, and installs a systemd service.
- Logs: `journalctl -u room-agent -f`
- Restart after editing .env: `sudo systemctl restart room-agent`
- Calibrate: `sudo systemctl stop room-agent && .venv/bin/python -m room_agent.calibrate`

## Commands
| Command | What it does |
|---|---|
| `python main.py` | Voice mode |
| `python main.py --text` | Type instead of talk. It still speaks if ElevenLabs is set. Add `--mute` for silent mode. |
| `python main.py --check` | Tests every API key, Home Assistant, weather, and your audio devices |
| `python main.py --list-devices` | Lists audio devices for `MIC_DEVICE` / `SPEAKER_DEVICE` |
| `python main.py --memory` | Shows everything it remembers about you |
| `python main.py --capabilities` | What it can and can't do right now |
| `python main.py --connect google` | Connect Gmail + Calendar in your browser (see google.md); `--connections` shows status |
| `python UI/server.py` | Dashboard at http://127.0.0.1:8765: status, settings, Settings > Connections. It opens (and prints) a one-time link that signs this browser in; other programs on the PC can't use the dashboard without it |
| `python -m room_agent.calibrate` | Measures your room so it knows when you've stopped talking |

## Settings (.env)
Every setting is explained in `.env.example`. These are the ones you'll touch:
- `MIC_DEVICE` / `SPEAKER_DEVICE`: part of the device name, e.g. `Jabra`. Leave blank to use the system default.
- `STT_PROVIDER=whisper`: local, free, private speech-to-text. Run `pip install -r requirements/local.txt` first. Use `tiny.en` on a Pi and `base.en` or `small.en` on a mini PC.
- `WEATHER_LOCATION`: your city.
- `HA_URL` / `HA_TOKEN`: Home Assistant. To get a token: your HA profile → Security → Long-lived access tokens.

## Memory
Jarvis remembers you without being asked:
- **After every exchange,** a background step (no added delay) saves anything lasting you mentioned: where you live, people's names, job, routines, preferences and plans. Relative dates become real ones ("this weekend" → "the weekend of Oct 10-11"). If something changes, like a move, the old fact is replaced.
- **After every conversation,** it writes a one- or two-line summary, so it can answer "what were we talking about yesterday?"
- **After a restart,** it picks up the last conversation word for word if it was recent (`RECENT_HOURS`).
- **Weather** uses your remembered home city, so it doesn't have to ask.
- You can say **"remember that…"** or **"forget that…"**. "Forget everything" wipes it all.

See what it knows with `python main.py --memory`. It's stored in a local SQLite database (`memory.db`); "forget" deletes rows for real. Each exchange costs one extra small Claude call (about a fifth of a cent on Haiku).

## States: when Jarvis listens and speaks
Whether Jarvis may talk is decided by code, never by the AI reading the conversation:

| State | What it listens for | What it may say |
|---|---|---|
| `WAKE_WORD_ONLY` (asleep) | only "hey Jarvis" (no speech recognition, no AI calls) | nothing |
| `QUIET` (you asked) | only "hey Jarvis" | nothing at all: no check-ins, no goodbyes. Timers you set still ring (`QUIET_ALLOWS_TIMERS`) |
| `LISTENING` | everything you say | check-ins and goodbyes after long silences |
| `PROCESSING` / `SPEAKING` | you, to interrupt | the answer |

**Quiet mode:** "stay quiet until I call you", "don't talk till I say hey Jarvis", "go quiet", "shut up" or "stop listening" are recognized in code. Jarvis says one short "Got it." and then answers nothing until "hey Jarvis". Other phrasings ("give me some peace for a bit") are caught by the AI, which flips the same switch. The log shows every state change (`state: LISTENING -> QUIET`).

## Changing the voice
Say "change your voice", "use a British voice" or "switch to George". These are real tools (`list_voices`, `set_voice`), so Jarvis knows exactly which voices exist:
- ElevenLabs: the 16 standard voices every plan can use (Brian, George, Daniel, Alice, Charlie, Sarah...).
- Piper (free/local, also used when ElevenLabs is out of credits): 13 English voices, US and British, downloaded on first use.

The choice is saved in `settings.json`. To add voices, edit `ELEVEN_VOICES` / `PIPER_VOICES` in `room_agent/audio/voices.py`.

### How it sounds (emotion)
With `ELEVENLABS_MODEL=eleven_v4_turbo` (or `eleven_v3_conversational`) the agent can change its delivery:
- Say "talk softer", "be more serious", "more energy" or "talk normal again". The style is saved (`soft`, `whisper`, `warm`, `engaged`, `excited`, `serious`, `playful`, `normal`).
- It also picks a style for a reply by itself when the moment calls for it (good news sounds excited, bad news soft). The tag is never spoken or saved in the conversation.
- Other models (`eleven_flash_v2_5`, Piper) can't do this; they sound the same whatever the style. Code is in `room_agent/audio/styles.py`.

How a sentence is performed is decided separately from what it says (`room_agent/speech/`):
- What Jarvis says (the SEMANTIC text) is what's stored, remembered and shown. The PERFORMANCE script (`[quiet, warm] Yeah...
  what happened?`) exists only inside the request to ElevenLabs.
- `social/meaning.py` reads what you MEAN (tired, urgent, heavy news, good news, frustrated) with a small local model, so
  wording nobody wrote a rule for still lands; `speech/director.py` turns that plus the sentence's own purpose (an
  apology, a surprise, a warning...) into a few delivery words, a pace and at most one emphasized word. Most sentences get
  none: plain is the default.
- `speech/elevenlabs.py` renders it per model (tags only for v3/v4, never SSML there; plain words for other models) and
  validates it (same words, at most 2 tags, no laughing at bad news); anything doubtful goes out plain.
- If ElevenLabs times out, disconnects, returns no audio or refuses the model, that sentence is said by the local voice
  (or the fallback model): expressiveness can be lost, the sentence never is.
- Say "say AimChart like aim chart" to fix a pronunciation (spoken only; the spelling stays).
- `SPEECH_DEBUG=1` logs every sentence's semantic text, strategy, performance script and model.

## Presence: waking, check-ins, going to sleep
- **"Hey Jarvis" on its own** gets a quick reply ("Yeah?", "I'm here.", "What's up?"...), then Jarvis listens.
- **"Hey Jarvis, what's the weather?"** in one go gets answered directly, with no "yeah?" first.
- **While you're talking with it**, you don't need the wake word. Short pauses are fine.
- **If you go quiet for a while** (`CHECKIN_AFTER_S`), it may check in once ("You good?"). Randomness, a cooldown and `MAX_CHECKINS` stop this from getting annoying.
- **After a long silence** (`SLEEP_AFTER_S`, or `SLEEP_AFTER_CHECKIN_S` if you ignored a check-in), it says a goodbye ("I'll be around.") and goes back to waiting for "hey Jarvis". Talk over the goodbye, or right after it, and it stays awake.
- Every line comes from a varied pool and never repeats back to back. Edit the pools in `room_agent/presence.py`. They're recorded once and cached, so they play instantly.
- **Thinking sound:** `assets/sounds/loading.mp3` loops quietly while Jarvis thinks or looks something up, and cuts off the moment he starts talking.
- `assets/sounds/wake.mp3` plays once at startup (`WAKE_SOUND_ON`).
- If ElevenLabs runs out of credits, Jarvis switches to the free local Piper voice on the spot instead of going silent.

All timing knobs are listed together in `.env.example` and in `room_agent/config.py`.

## Echo cancellation and interrupting
The mic stays on while the agent talks, and it never hears itself:
- **Echo cancellation (WebRTC, the same engine Chrome and Google Meet use):** everything sent to the speaker is handed to the echo canceller, which subtracts it from the mic.
- **Echo delay is measured continuously.** With a Bluetooth speaker the agent's voice comes back about 0.4 s late; the log prints `echo delay: 390 ms`. The canceller's reference is pre-aligned by that delay, because WebRTC fails beyond ~0.5 s. The agent also keeps ignoring its own echo for that long after it stops talking.
- **Noise suppression and a high-pass filter** remove fans, hum and hiss before anything else sees the audio.
- **Interrupting needs strong evidence**, because with a loud speaker the agent's own echo can be louder than you:
  1. Silero voice detection must hear a confident human voice that survived echo cancellation...
  2. ...for about 240 ms within a short window, not a single spike (`BARGE_MIN_SPEECH_MS`, `BARGE_VAD`)...
  3. ...and Whisper must transcribe it as words the agent isn't saying itself (`BARGE_VERIFY`). Its own echo, "thank you"-style Whisper hallucinations, noise and breathing are rejected.

  Each attempt logs one line: `barge-in candidate`, then `barge-in confirmed: user speech ('wait stop')` + `TTS cancelled`, or `barge-in rejected: likely echo (...)`. Turn interrupting off with `BARGE_IN=0`.
- If `livekit` isn't installed, it falls back to ignoring the mic while it talks. That's still loop-safe, but you can't interrupt it.

Speaker placement matters: if the log keeps showing `rejected: likely echo` or warns that the agent's voice keeps leaking, move the speaker further from the mic or lower its volume.

The code is in `room_agent/audio/engine.py`.

## Speed (what's already tuned)
- If an answer is slow, a quiet thinking sound loops (`assets/sounds/loading.mp3`, `LOADING_DELAY`), and it says "one sec" when it has to look something up. Turn fillers off with `FILLERS=0`.
- Speech-to-text runs on an NVIDIA graphics card with `WHISPER_DEVICE=cuda` (`pip install -r requirements/gpu.txt`). That takes about 0.07s, against about 0.7s on the processor.
- The voice is `eleven_turbo_v2_5` at 24 kHz, a balance of speed and natural sound.
- The log prints `answer started X.XXs after you stopped talking` on every turn.

## Tuning
Watch the "turn took" log line. Speech should start under ~1.5s after you stop talking.
- Too slow? Lower `SILENCE_S` (0.8 → 0.6), keep Haiku, and keep `eleven_flash_v2_5`.
- Cuts you off mid-sentence? Raise `SILENCE_S` or lower `SPEECH_RMS`.
- Wakes up randomly? Raise `WAKE_THRESHOLD` to 0.6-0.7.
- Doesn't hear the wake word? Lower `WAKE_THRESHOLD` to 0.3-0.4 and check `MIC_DEVICE`.

## Project layout
```
main.py                  entry point (python main.py [--text|--check|...])
run.bat, setup.ps1       Windows: launch, first-time setup
UI/                      local dashboard: home, chat, memory, activity, status, connections, settings (Ctrl+K)
assets/                  sounds/ (wake + loading sounds), voices/ (Piper voices and cached clips)
requirements/            base.txt, local.txt (Whisper + Piper), gpu.txt (NVIDIA)
deploy/                  Linux: install-service.sh, room-agent.service
memory.db, settings.json what it remembers, chosen voice
room_agent/
  persona.md             who Jarvis is: edit it to tune the personality (no code); the style (PERSONALITY=street: confident
                         big-brother energy, or friend: the original easygoing tone) fills its slots (social/personality.py)
  config.py              every setting read from .env
  runtime.py             shared state: the current turn, the session, long-lived services
  prompt.py              assembles the system prompt (persona + rules + area rules) and the runtime context
  truth.py               what it can do (from the registry) + the claim check in front of the speaker
  abilities/             one file per area: info, timers, apps, windows, media, memory, voice, home, quiet, undo
  actions/               capability registry (core), executor + plans + undo, environment context, events
  learning/              learned preferences per user (learning.db), corrections, routines
  integrations/          Connections: OAuth, credential vault, Google (Gmail, Calendar)
  tools/                 the implementations the abilities call (apps, media, window_control, timers, weather...)
  audio/ llm/ memory/ conversation/   speech in and out, model backends, memory store, turn-taking
tests/                   python -m tests (see below); harness.py is the shared test kit
```

## Adding a feature
One file in `room_agent/abilities/` (copy a small one like `presence.py`), plus its name in `MODULES` in
`room_agent/actions/core.py`. In that file:
- `tool(name, description, params(...), run, group=...)` for each thing the model can ask for. `run(args)` returns text
  starting with `OK:` / `FAILED:` / ... Parameters marked required, `minimum`/`maximum`/`enum` are enforced before it runs.
- `register_group(Group(...))`: when its tools are worth offering, its line in "what I can do", its prompt `rules`.
- Optional: `observe` / `verify` / `undo` (checks and undo), `risk=Risk.CONFIRM/SENSITIVE`, `register_claim(...)` for
  what it can confirm, `register_context(...)` for live facts it adds to each request.
Nothing else (router, prompt, claim check) needs editing.
- Optional, for the cognition layer (`room_agent/cognition/`): `expect` (what `observe` must show afterwards: checked as
  expected vs observed), `skip_if_satisfied`, `fresh_for`, and `reflex` patterns (simple phrasings that run with no model
  call). Read-only capabilities (`changes_state=False`) are automatically offered as checks before acting.
- `python -m tests.cognition_live` runs goal scenarios with the real model against a simulated PC (nothing on the PC
  changes); every turn's level, model calls, tokens, actions and observations are kept in `experience.db`.

## Debugging and tests
- `TRACE=1` in `.env` logs one block per turn: `INPUT`, `AUDIO` (USER / AGENT_ECHO / NOISE / UNCERTAIN), `INTENT`, `CAPABILITY`, `PARAMS`, `MISSING`, `ACTION`, `RESULT`, `RESPONSE`.
- Every turn logs a `timing:` line: speech-to-text, the model's first words, tools, first sound, the whole turn.
- `python -m tests` runs every offline suite (fake model and services, no cost, doesn't touch the PC).
- `python -m tests --live` adds the PC tests (really opens/moves apps, changes the volume, plays music briefly, then puts
  everything back). `python -m tests apps media` runs only the suites with those words in the name.
- `python tests/scenarios.py` runs about 50 natural-language requests against the real model (costs a little).
- Voice: `python -m tests.voice_audition` makes A/B clips (previous vs new delivery; ElevenLabs too when a key is set),
  `... serve` opens a blind listening page on http://127.0.0.1:8773, `... score` unblinds your ratings.
  `python -m tests.tts_bench` measures ElevenLabs models, HTTP vs WebSocket, normalization and seeds (`--selftest`: no key).
  `python -m tests.speech_latency <label>` measures time to first sound with the real model (actions are simulated).
  None of these change anything on the PC.

## Upgrade path
1. Custom wake word ("hey adam"): train it with openWakeWord's Colab notebook, put the `.onnx` file next to `main.py`, and set `WAKE_WORD=hey_adam.onnx`.
2. More tools: trading alerts, Stairwise job lookup, Google Calendar.
3. Sesame's open-source CSM-1B voice, if you have a GPU.
