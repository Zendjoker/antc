"""Every setting, read once from .env (or the environment). Nothing in here changes while the agent runs."""

import os
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parents[1]  # project root: .env, assets/, memory.db live here
load_dotenv(HERE / ".env")
VOICES_DIR = HERE / "assets" / "voices"  # Piper voices and cached stock clips

# ---------- providers ----------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "claude").lower()  # claude | ollama
STT_PROVIDER = os.getenv("STT_PROVIDER", "deepgram").lower()  # deepgram | whisper
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "elevenlabs").lower()  # elevenlabs | piper

# ---------- language model ----------
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")  # fast. swap to claude-sonnet-5-5 for smarter
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:14b")
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")  # how long the model stays loaded on the GPU
MEMORY_MODEL = os.getenv("MEMORY_MODEL", "")  # model that decides what to remember; blank = same as CLAUDE_MODEL
USER_NAME = os.getenv("USER_NAME", "Adam")
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "16"))  # messages of conversation sent with each request (tool pairs never split)

# ---------- cost: which model answers, and how much it may spend ----------
# Most turns go to a cheap OpenAI model; Claude handles what matters (you ask for it, or it's long/complex),
# and takes over if OpenAI fails. Both get the same truth rules, tools and claim checks.
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")  # gpt-5-nano is ~5x cheaper still, but follows the rules less reliably
OPENAI_MEMORY_MODEL = os.getenv("OPENAI_MEMORY_MODEL", "") or OPENAI_MODEL  # background memory work
OPENAI_REASONING = os.getenv("OPENAI_REASONING", "minimal")  # GPT-5 thinking effort: minimal is fastest/cheapest (blank = model default)
LLM_DEFAULT = os.getenv("LLM_DEFAULT", "openai" if OPENAI_KEY else "claude").lower()  # openai | claude
LLM_SMART = os.getenv("LLM_SMART", "claude").lower()  # model for important requests
LLM_SMART_TRIGGERS = [t.strip().lower() for t in os.getenv(
    "LLM_SMART_TRIGGERS", "ask claude,use claude,smart model,think hard,think it through,think carefully,really think").split(",")
    if t.strip()]  # saying any of these sends the request to the smart model
LLM_COMPLEX_TOPICS = [t.strip().lower() for t in os.getenv(
    "LLM_COMPLEX_TOPICS", "help me plan,make a plan,help me decide,should i,pros and cons,compare,step by step,"
                          "explain why,in detail,advice on,give me advice,strategy").split(",") if t.strip()]
LLM_COMPLEX_WORDS = int(os.getenv("LLM_COMPLEX_WORDS", "35"))  # requests at least this long count as complex
MAX_TOKENS_REPLY = int(os.getenv("MAX_TOKENS_REPLY", "450"))  # spoken replies are short; this is only a ceiling
MAX_TOKENS_MEMORY = int(os.getenv("MAX_TOKENS_MEMORY", "400"))
MAX_TOKENS_SUMMARY = int(os.getenv("MAX_TOKENS_SUMMARY", "150"))
PROMPT_CACHE = os.getenv("PROMPT_CACHE", "1") == "1"  # Claude: fixed part of every request billed at 10% after the first
DAILY_BUDGET_USD = float(os.getenv("DAILY_BUDGET_USD", "1.00"))  # total for all paid models per day (0 = no limit)
BUDGET_FALLBACK = os.getenv("BUDGET_FALLBACK", "ollama").lower()  # past the budget: ollama (free local model) | stop
MEMORY_BATCH = os.getenv("MEMORY_BATCH", "1") == "1"  # learn facts once per conversation instead of after every exchange

# ---------- cognition (room_agent/cognition/): how much reasoning a request gets, and the limits of one request ----------
REFLEX = os.getenv("REFLEX", "1") == "1"  # simple fully specified commands ("open Spotify") run without a model call
LEVEL_DEEP = os.getenv("LEVEL_DEEP", "smart").lower()  # who handles DEEP requests: smart (LLM_SMART) | default
OLLAMA_MODEL_DEEP = os.getenv("OLLAMA_MODEL_DEEP", "").strip()  # local/Ollama setups: a stronger model for DEEP (blank = same)
COG_MAX_TOOL_ROUNDS = int(os.getenv("COG_MAX_TOOL_ROUNDS", "6"))  # tool rounds per request before it must stop and report
COG_MAX_TOOL_CALLS = int(os.getenv("COG_MAX_TOOL_CALLS", "14"))   # tool calls per request
COG_MAX_TURN_S = float(os.getenv("COG_MAX_TURN_S", "90"))         # seconds of tool work per request
COG_MAX_RETRIES = int(os.getenv("COG_MAX_RETRIES", "2"))          # the same failing action, at most this many times per goal

# ---------- audio devices and rates ----------
SR = 16000  # mic / wake word / STT rate
OUT_SR = int(os.getenv("OUTPUT_SAMPLE_RATE", "24000"))  # speaker rate. 16000 sounds like a phone call
FRAME = 1280  # 80 ms, what openWakeWord expects
MIC_DEVICE = os.getenv("MIC_DEVICE", "")  # name fragment or index, blank = system default
SPEAKER_DEVICE = os.getenv("SPEAKER_DEVICE", "")
FOLLOW_DEFAULT_DEVICE = os.getenv("FOLLOW_DEFAULT_DEVICE", "1") == "1"  # blank MIC/SPEAKER_DEVICE: follow Windows' default live
AEC = os.getenv("ECHO_CANCELLATION", "1") == "1"  # subtract the agent's own voice from the mic
NOISE_SUPPRESSION = os.getenv("NOISE_SUPPRESSION", "1") == "1"

# ---------- wake word and speech detection ----------
WAKE_WORD = os.getenv("WAKE_WORD", "hey_jarvis")
WAKE_THRESHOLD = float(os.getenv("WAKE_THRESHOLD", "0.5"))
WAKE_SOUND = os.getenv("WAKE_SOUND", "assets/sounds/wake.mp3")  # played when it wakes up; blank = short beep
WAKE_SOUND_ON = os.getenv("WAKE_SOUND_ON", "startup").lower()  # startup | wake (before "yeah?") | off
SPEECH_RMS = float(os.getenv("SPEECH_RMS", "500"))  # tune with calibrate.py output
SILENCE_S = float(os.getenv("SILENCE_S", "0.8"))  # how long a pause ends your turn
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.5"))  # how sure the voice detector must be that it's a human voice

# ---------- conversation presence: every timing knob lives here ----------
# SLEEPING (wake word only) -> "hey jarvis" -> ACTIVE conversation (no wake word needed)
#   short silence    : keep listening, say nothing
#   CHECKIN_AFTER_S  : maybe one natural check-in ("you good?"), with randomness + cooldown
#   SLEEP_AFTER_S    : say a goodbye line and go back to sleeping (sooner if a check-in got no answer)
WAKE_ACK_DELAY = float(os.getenv("WAKE_ACK_DELAY", "0.7"))  # after "hey jarvis", wait this long for a command before saying "yeah?"
WAKE_GUARD_S = float(os.getenv("WAKE_GUARD_S", "1.0"))  # ignore the wake word this long after the agent stops talking
CHECKIN_AFTER_S = float(os.getenv("CHECKIN_AFTER_S", "20"))
CHECKIN_CHANCE = float(os.getenv("CHECKIN_CHANCE", "0.6"))  # 0-1, so it doesn't happen like clockwork
CHECKIN_COOLDOWN_S = float(os.getenv("CHECKIN_COOLDOWN_S", "180"))  # min time between check-ins, across conversations
MAX_CHECKINS = int(os.getenv("MAX_CHECKINS", "1"))  # per conversation
SLEEP_AFTER_S = float(os.getenv("SLEEP_AFTER_S", "45"))
SLEEP_AFTER_CHECKIN_S = float(os.getenv("SLEEP_AFTER_CHECKIN_S", "12"))  # no answer to "you good?" -> sleep after this
SLEEP_GRACE_S = float(os.getenv("SLEEP_GRACE_S", "2"))  # start talking within this long after the goodbye and it stays awake
QUIET_ALLOWS_TIMERS = os.getenv("QUIET_ALLOWS_TIMERS", "1") == "1"  # timers you set still ring in quiet mode
ALARM_GAP_S = float(os.getenv("ALARM_GAP_S", "4"))  # ringing timers and alarms: pause between calls while it listens for you
ALARM_MAX_S = float(os.getenv("ALARM_MAX_S", "900"))  # ringing timers and alarms: give up after this long (0 = never)
ALARM_MISSED_GRACE_S = float(os.getenv("ALARM_MISSED_GRACE_S", "600"))  # after a restart, still ring what was missed by less than this
TIMER_MIN_S = 1  # shortest countdown timer (the tool schema, the capability list and the checks all read this)
TIMER_MAX_S = 30 * 86400  # longest countdown timer
LISTEN_WAIT_S = float(os.getenv("LISTEN_WAIT_S", "6"))  # after an unfinished sentence, how long to wait for the rest
MISSING_WAIT_S = float(os.getenv("MISSING_WAIT_S", "4"))  # after a request that lacks a required detail, how long before asking for it
DIAGNOSTICS = os.getenv("DIAGNOSTICS", "0") == "1"  # live diagnostic mode: logs/diagnostics-*.jsonl (livelog.py)
DIAGNOSTICS_DIR = Path(os.getenv("DIAGNOSTICS_DIR", HERE / "logs"))
TRACE = os.getenv("TRACE", "0") == "1"  # log one decision trace per turn (INPUT, AUDIO, INTENT, PARAMS, ACTION, RESULT...)
LISTEN_PATIENCE = {"normal": 1.0, "longer": 1.8, "longest": 3.0}  # x SILENCE_S before it treats you as finished
MIN_VOICE_MASS = 2.5  # summed voice probability a recording needs before Whisper hears it (a click or echo flicker is ~1, real words 4+)
EXPLAIN_PATIENCE = 2.5  # x SILENCE_S while you're explaining ("let me finish"): pauses of 1-2 s don't end the turn
EXPLAIN_WAIT_S = 20.0  # after "let me finish", how long to wait for you to start
EXPLAIN_DONE_S = 2.5  # extra quiet after a statement that ended with a full stop before it answers
SPEECH_RATES = {"much slower": 0.75, "slower": 0.88, "normal": 1.0, "faster": 1.12}  # ElevenLabs speed setting

# ---------- barge-in (talking over the agent) ----------
BARGE_IN = os.getenv("BARGE_IN", "1") == "1"  # talk over the agent to interrupt it
BARGE_MIN_SPEECH_MS = int(os.getenv("BARGE_MIN_SPEECH_MS", "240"))  # how much of your voice it takes to interrupt it
BARGE_VAD = float(os.getenv("BARGE_VAD", "0.6"))  # voice detector confidence needed to interrupt it
BARGE_VERIFY = os.getenv("BARGE_VERIFY", "1") == "1"  # double-check interruptions with Whisper (needs STT_PROVIDER=whisper)
BARGE_DUCK = os.getenv("BARGE_DUCK", "1") == "1"  # lower the agent's volume the moment you might be interrupting, so the echo canceller keeps your voice
BARGE_DUCK_GAIN = float(os.getenv("BARGE_DUCK_GAIN", "0.15"))  # its volume while ducked (1.0 = no change)

# ---------- speaker verification: only your voice may interrupt ----------
SPEAKER_VERIFY = os.getenv("SPEAKER_VERIFY", "1") == "1"  # needs a voiceprint: python main.py --enroll-voice. None enrolled = off
VOICEPRINT_FILE = Path(os.getenv("VOICEPRINT_FILE", HERE / "voiceprint.npz"))
SPEAKER_MODEL_FILE = Path(os.getenv("SPEAKER_MODEL_FILE", HERE / "models" / "speaker" / "wespeaker_en_voxceleb_resnet34_LM.onnx"))
SPEAKER_ACCEPT = float(os.getenv("SPEAKER_ACCEPT", "0.70"))  # similarity to your voiceprint (0-1) that counts as you, for 0.8-1.4 s of audio
SPEAKER_ACCEPT_FAST = float(os.getenv("SPEAKER_ACCEPT_FAST", "0.75"))  # same for the first, shorter (< 0.8 s) look: stricter
SPEAKER_ACCEPT_LONG = float(os.getenv("SPEAKER_ACCEPT_LONG", "0.62"))  # same for 1.4 s or more: a longer sample can be trusted more
SPEAKER_TRUST = float(os.getenv("SPEAKER_TRUST", "0.80"))  # at or above this the Whisper check is skipped (faster interrupt)
SPEAKER_REJECT = float(os.getenv("SPEAKER_REJECT", "0.30"))  # below this: clearly someone else, no second look
SPEAKER_BARGE_VAD = float(os.getenv("SPEAKER_BARGE_VAD", "0.45"))  # with the speaker check on, BARGE_VAD drops to this
SPEAKER_BARGE_RMS = float(os.getenv("SPEAKER_BARGE_RMS", "0.2"))  # ...and the loudness needed is this x SPEECH_RMS (was 0.4)
SPEAKER_BARGE_GAP = int(os.getenv("SPEAKER_BARGE_GAP", "5"))  # ...and a gap of this many 80 ms frames ends an attempt (was 3)

# ---------- fillers and loading sound ----------
FILLERS = os.getenv("FILLERS", "1") == "1"  # say "mm" / "one sec" when an answer is slow, so there's no dead air
FILLER_DELAY = float(os.getenv("FILLER_DELAY", "1.0"))  # only fill silence longer than this (seconds)
LOADING_SOUND = os.getenv("LOADING_SOUND", "assets/sounds/loading.mp3")  # looped while it thinks / looks things up; blank = off
LOADING_VOLUME = float(os.getenv("LOADING_VOLUME", "0.5"))  # 1.0 = the file's own volume
LOADING_DELAY = float(os.getenv("LOADING_DELAY", "0.6"))  # only start the loop if the answer takes longer than this

# ---------- speech-to-text ----------
DG_KEY = os.getenv("DEEPGRAM_API_KEY", "")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base.en")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu").lower()  # cpu | cuda (NVIDIA GPU, ~20x faster)

# ---------- text-to-speech ----------
EL_KEY = os.getenv("ELEVENLABS_API_KEY", "")
EL_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")
# Voices from .env. The voice in use right now (it can be changed by voice) is audio.voices.current.
EL_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "nPczCjzI2devNBz1zQrb")
PIPER_VOICE = os.getenv("PIPER_VOICE", "en_US-ryan-high")  # browse: huggingface.co/rhasspy/piper-voices
EL_FALLBACK_MODEL = os.getenv("ELEVENLABS_FALLBACK_MODEL", "eleven_flash_v2_5").strip()  # if the main model is refused
EL_TEXT_NORMALIZATION = os.getenv("ELEVENLABS_TEXT_NORMALIZATION", "auto").strip().lower()  # auto | on | off
EL_SEED = os.getenv("ELEVENLABS_SEED", "").strip()  # a fixed seed: repeatable audio for A/B tests (blank = natural variation)
SPEECH_DEBUG = os.getenv("SPEECH_DEBUG", "0") == "1"  # log SEMANTIC / STRATEGY / PERFORMANCE / MODEL per sentence

# ---------- integrations ----------
HA_URL = os.getenv("HA_URL", "").rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "")
# Zigbee sensors and lights through Zigbee2MQTT on this PC (tools/zigbee.py). Off if it isn't running.
ZIGBEE = os.getenv("ZIGBEE", "1") == "1"
ZIGBEE_MQTT_HOST = os.getenv("ZIGBEE_MQTT_HOST", "127.0.0.1")
ZIGBEE_MQTT_PORT = int(os.getenv("ZIGBEE_MQTT_PORT", "1883"))
ZIGBEE_TOPIC = os.getenv("ZIGBEE_TOPIC", "zigbee2mqtt")
ZIGBEE_ALIASES = os.getenv("ZIGBEE_ALIASES", "").strip()  # what you call your devices: "bed=Vibration sensor, desk light=LED strip"
ZIGBEE2MQTT_DIR = os.getenv("ZIGBEE2MQTT_DIR", "").strip()  # folder with start.bat: Jarvis starts it if it isn't running
ZIGBEE_EVENTS_FILE = Path(os.getenv("ZIGBEE_EVENTS_FILE", HERE / "zigbee_events.json"))  # door/bed events, kept 48 h
MEMORY_SENSITIVE = os.getenv("MEMORY_SENSITIVE", "explicit").strip().lower()  # explicit: health, money, IDs... only
                                                                             # when you ask; allow: learned like the rest
ACTIONS_JOURNAL_FILE = Path(os.getenv("ACTIONS_JOURNAL_FILE", HERE / "actions_journal.json"))  # action lifecycles
QUIET_HOURS = os.getenv("QUIET_HOURS", "").strip()  # e.g. 23-7: nothing proactive is spoken then (alarms still ring)
PROACTIVE_STATE_FILE = Path(os.getenv("PROACTIVE_STATE_FILE", HERE / "proactive_state.json"))  # cooldowns across restarts
# Greeting you when you come home (conversation/greet.py): the door opens after the room was quiet this long
GREET_ON_ARRIVAL = os.getenv("GREET_ON_ARRIVAL", "1") == "1"
GREET_AWAY_MIN = float(os.getenv("GREET_AWAY_MIN", "10"))       # minutes with nobody heard in the room = you were out
GREET_COOLDOWN_MIN = float(os.getenv("GREET_COOLDOWN_MIN", "30"))  # at most one greeting this often
WEATHER_LOCATION = os.getenv("WEATHER_LOCATION", "")
UNITS = os.getenv("UNITS", "imperial").lower()  # imperial | metric

# ---------- files ----------
SETTINGS_FILE = Path(os.getenv("SETTINGS_FILE", HERE / "settings.json"))  # e.g. chosen voice
MEMORY_DB = Path(os.getenv("MEMORY_DB", HERE / "memory.db"))  # persistent memory (SQLite): what it knows about you
MEMORY_FILE = Path(os.getenv("MEMORY_FILE", HERE / "memory.json"))  # older formats, imported into memory.db once
RECENT_FILE = Path(os.getenv("RECENT_FILE", HERE / "conversation.json"))
REMINDERS_FILE = Path(os.getenv("REMINDERS_FILE", HERE / "reminders.json"))  # timers and alarms, kept across restarts
SPEND_FILE = Path(os.getenv("SPEND_FILE", HERE / "spend.json"))  # today's API spend, kept across restarts
# Connections (Settings -> Connections). The Google app registration: a "Desktop app" OAuth client from Google Cloud.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_CLIENT_FILE = Path(os.getenv("GOOGLE_CLIENT_FILE", HERE / "google_client.json"))
CONNECTIONS_FILE = Path(os.getenv("CONNECTIONS_FILE", HERE / "connections.json"))  # status only; tokens are in the vault
BACKGROUND_SYNC = os.getenv("BACKGROUND_SYNC", "0") == "1"  # opt-in, for future "tell me when..." (nothing polls now)
# Phone mode (room_agent/phone/, see phone.md): Jarvis on your phone, calls you when it matters while you drive.
CONTROL_PORT = int(os.getenv("CONTROL_PORT", "8771"))  # the dashboard's live link to Jarvis (local only)
PHONE_MODE = os.getenv("PHONE_MODE", "0") == "1"
PHONE_PORT = int(os.getenv("PHONE_PORT", "8770"))  # local port the public URL forwards to
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")  # e.g. https://my-pc.tailnet-name.ts.net (Tailscale Funnel)
PHONE_TUNNEL = os.getenv("PHONE_TUNNEL", "").strip().lower()  # "cloudflared": Jarvis starts its own tunnel (no PUBLIC_URL)
PHONE_TOKEN = os.getenv("PHONE_TOKEN", "").strip()  # secret the iPhone Shortcuts send (made by --setup-phone)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER", "").strip()  # Jarvis's number, +1...
MY_PHONE = os.getenv("MY_PHONE", "").strip()  # your number, +1... (the only number Jarvis talks to)
PHONE_TTS_PROVIDER = os.getenv("PHONE_TTS_PROVIDER", "").strip()  # optional: Google / Amazon / ElevenLabs (Twilio's)
PHONE_VOICE = os.getenv("PHONE_VOICE", "").strip()  # optional voice id for that provider
VIP_SENDERS = [s.strip().lower() for s in os.getenv("VIP_SENDERS", "").split(",") if s.strip()]  # emails worth a call
CALL_COOLDOWN_MIN = float(os.getenv("CALL_COOLDOWN_MIN", "10"))  # at least this long between calls
DRIVE_CHECK_S = float(os.getenv("DRIVE_CHECK_S", "180"))  # while driving: how often to look for important things
MEETING_SOON_MIN = float(os.getenv("MEETING_SOON_MIN", "15"))  # a meeting this close is worth a call
LOCATION_SOURCE = os.getenv("LOCATION_SOURCE", "auto").strip().lower()  # auto (Windows, then internet) / windows / ip / off
PERSONA_FILE = Path(os.getenv("PERSONA_FILE", HERE / "room_agent" / "persona.md"))  # who Jarvis is (plain text)
LEARNING_DB = Path(os.getenv("LEARNING_DB", HERE / "learning.db"))  # learned preferences + interaction records (local only)
LEARNING_TELEMETRY = os.getenv("LEARNING_TELEMETRY", "1") != "0"  # 0: learn preferences but keep no interaction records
USER_PROFILE = os.getenv("USER_PROFILE", "").strip()  # whose preferences (default: USER_NAME)
APPS_CACHE = Path(os.getenv("APPS_CACHE", HERE / "apps_cache.json"))
EXPERIENCE_DB = Path(os.getenv("EXPERIENCE_DB", HERE / "experience.db"))  # goal outcomes + turn metrics (cognition/)  # installed Windows apps, found once and reused
RECENT_HOURS = float(os.getenv("RECENT_HOURS", "12"))  # after a restart, pick up conversations this recent
