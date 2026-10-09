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
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")  # gpt-5-nano is ~5x cheaper still, but follows the rules less reliably
OPENAI_MEMORY_MODEL = os.getenv("OPENAI_MEMORY_MODEL", "") or OPENAI_MODEL  # background memory work
OPENAI_REASONING = os.getenv("OPENAI_REASONING", "minimal")  # GPT-5 thinking effort: minimal is fastest/cheapest (blank = model default)
# Natural conversation, open questions, ambiguous requests and project discussions get a stronger OpenAI model; clear
# commands and status checks stay on OPENAI_MODEL (router.conversational). Blank = always OPENAI_MODEL. "gpt-5" at
# minimal thinking answered in ~1s in a measured test (Oct 9 2026); "low" thinks more but takes ~3.5-4s to first words.
OPENAI_CONVERSATION_MODEL = os.getenv("OPENAI_CONVERSATION_MODEL", "gpt-5").strip()
OPENAI_CONVERSATION_REASONING = os.getenv("OPENAI_CONVERSATION_REASONING", "minimal").strip()
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
# V2 general intelligence (cognition/understand.py, actions/planning.py, actions/supervisor.py, computer/coding.py)
GOAL_UNDERSTANDING = os.getenv("GOAL_UNDERSTANDING", "1") == "1"  # explicit limits in their words are enforced on every goal
GOAL_VERIFIER = os.getenv("GOAL_VERIFIER", "0") == "1"  # a cheap model re-reads AMBIGUOUS requests (paid: off by default)
TASK_CONTRACTS = os.getenv("TASK_CONTRACTS", "1") == "1"  # run_task plans: contracts, validation, inferred dependencies
TASK_RECOVERY = os.getenv("TASK_RECOVERY", "1") == "1"  # run_task: bounded, safe alternatives after a failure
TASK_SUPERVISOR = os.getenv("TASK_SUPERVISOR", "1") == "1"  # run_task: deterministic progress monitoring
TASK_MAX_RECOVERIES = int(os.getenv("TASK_MAX_RECOVERIES", "") or "3")  # alternative attempts per task
ROUTER_ESCALATION = os.getenv("ROUTER_ESCALATION", "0") == "1"  # multi-domain / coding / repeated failures -> the smart model (costs more: off by default)
TASK_MAX_USD = float(os.getenv("TASK_MAX_USD", "") or "0.50")  # a task that has cost more than this stops (supervisor)
TASK_EXPERIENCE = os.getenv("TASK_EXPERIENCE", "1") == "1"  # verified procedures / failure patterns in experience.db
EXPERIENCE_TTL_DAYS = int(os.getenv("EXPERIENCE_TTL_DAYS", "") or "90")  # learned procedures / patterns expire
CODING = os.getenv("CODING", "1") == "1"  # run_tests (free, sandboxed copy); fix_code needs CODING_FIXER + budget
CODING_FIXER = os.getenv("CODING_FIXER", "none").strip().lower()  # none (default) | anthropic: a paid model proposes fixes
CODING_MAX_USD = float(os.getenv("CODING_MAX_USD", "") or "0.50")  # most one fix_code call may reserve (paid fixer only)
CODING_ROOTS = [p for p in os.getenv("CODING_ROOTS", "").split(";") if p.strip()]  # extra project roots (default: home)

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
# Lists, event reminders and nudges (tools/lists.py, triggers.py)
TASKS_FILE = Path(os.getenv("TASKS_FILE", HERE / "tasks.json"))  # every request's task record + checkpoints (actions/tasks.py)
LISTS_FILE = Path(os.getenv("LISTS_FILE", HERE / "lists.json"))  # to-do / shopping / any list, and notes
EVENT_REMINDERS_FILE = Path(os.getenv("EVENT_REMINDERS_FILE", HERE / "event_reminders.json"))  # "when I get home"...
DESK_AWAY_MIN = float(os.getenv("DESK_AWAY_MIN", "10"))  # this long without keyboard/mouse = away from the PC
BREAK_REMINDER_MIN = float(os.getenv("BREAK_REMINDER_MIN", "180"))  # nonstop PC use before suggesting a break (0 = off)
RAIN_ALERT = os.getenv("RAIN_ALERT", "1") == "1"  # mention rain when you leave, if it's likely today
RAIN_ALERT_PCT = int(os.getenv("RAIN_ALERT_PCT", "60"))
MEETING_ALERT_MIN = float(os.getenv("MEETING_ALERT_MIN", "10"))  # heads-up before calendar events (0 = off)
PLAN_FOLLOWUPS = os.getenv("PLAN_FOLLOWUPS", "1") == "1"  # mention a remembered plan the morning it's due

# Computer interaction (room_agent/computer/): browsers, page reading, clicking / typing, screen vision, web research
BROWSER_CONTROL = os.getenv("BROWSER_CONTROL", "1") == "1"  # open sites / search / click / type / read pages in your browsers
SCREEN_VISION = os.getenv("SCREEN_VISION", "ask").lower()  # off | ask (asks once before the first screenshot) | allow
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "auto").lower()  # auto | claude | openai | off: who reads screenshots
VISION_MODEL = os.getenv("VISION_MODEL", "").strip()  # blank = CLAUDE_MODEL / OPENAI_MODEL
RESEARCH_FILE = Path(os.getenv("RESEARCH_FILE", HERE / "research.json"))  # the last research reports, for the dashboard

# Missions (room_agent/missions/): long background jobs such as "find 20 restaurants without a good website and build demos"
MISSIONS_DIR = Path(os.getenv("MISSIONS_DIR", "").strip() or Path.home() / "Documents" / "Jarvis Missions")  # reports, demo sites, exports
MISSIONS_DB = Path(os.getenv("MISSIONS_DB", "").strip() or HERE / "missions.db")  # missions, steps, leads, sources, drafts, approvals
MISSION_DEFAULT_BUDGET_USD = float(os.getenv("MISSION_DEFAULT_BUDGET_USD", "") or "1.00")  # per mission, unless you say otherwise
MISSION_MAX_BUDGET_USD = float(os.getenv("MISSION_MAX_BUDGET_USD", "") or "10.00")  # hard ceiling: a mission can't be given more
MISSION_LIGHT_MODEL = os.getenv("MISSION_LIGHT_MODEL", "") or OPENAI_MODEL  # wording (email drafts, site copy): cheap model
MISSION_STRONG_MODEL = (os.getenv("MISSION_STRONG_MODEL", "").strip() or "claude-sonnet-5-5")  # demo-site code edits (CODER_BACKEND=anthropic)
MISSION_LLM_COPY = os.getenv("MISSION_LLM_COPY", "0") == "1"  # 1 = a model polishes outreach wording (costs a little); 0 = templates
# goal-driven missions (missions/goals.py): contract verification, bounded replanning, parallel independent steps
MISSION_VERIFY = os.getenv("MISSION_VERIFY", "1") == "1"  # a step is done only when its contract's code check passes
MISSION_REPLAN = os.getenv("MISSION_REPLAN", "1") == "1"  # unmet success criterion -> the workflow may add free steps
MISSION_MAX_REPLANS = int(os.getenv("MISSION_MAX_REPLANS", "") or "2")  # at most this many plan changes per mission
MISSION_CONCURRENCY = max(1, int(os.getenv("MISSION_CONCURRENCY", "") or "1"))  # steps run at once (1 = one at a time)
MISSION_ESCALATE_AFTER = int(os.getenv("MISSION_ESCALATE_AFTER", "") or "0")  # light-model outputs that failed their
#   checks before wording moves to the strong model (0 = never escalate: no surprise cost)
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()  # optional: better business discovery (paid per request)
PLACES_COST_PER_REQUEST = float(os.getenv("PLACES_COST_PER_REQUEST", "") or "0.035")  # USD estimate per Places Text Search call
PLACES_DETAILS_COST_PER_REQUEST = float(os.getenv("PLACES_DETAILS_COST_PER_REQUEST", "") or "0.025")  # Place Details re-fetch
PLACES_MEMORY_TTL_S = int(os.getenv("PLACES_MEMORY_TTL_S", "") or "21600")  # Google content is kept in memory only, this long
CODER_MAX_USD = float(os.getenv("CODER_MAX_USD", "") or "1.00")  # budget reserved for one Claude Code edit (its real cost is unknown up front)
MISSION_PREVIEW_PORT = int(os.getenv("MISSION_PREVIEW_PORT", "") or "8790")  # demo-site previews, on 127.0.0.1 only
CODER_BACKEND = (os.getenv("CODER_BACKEND", "").strip() or "auto").lower()  # auto | claude_cli | anthropic | none: who edits demo sites
CODER_CLI = (os.getenv("CODER_CLI", "").strip() or "claude")  # the Claude Code command, if CODER_BACKEND uses it
CODER_MODEL = os.getenv("CODER_MODEL", "").strip()  # for claude_cli: blank = its default model
CODER_TIMEOUT_S = int(os.getenv("CODER_TIMEOUT_S", "") or "600")
# You, as the sender of outreach drafts (shown in the drafts; required by anti-spam law for commercial email)
MISSION_SENDER_NAME = os.getenv("MISSION_SENDER_NAME", "").strip()
MISSION_SENDER_BUSINESS = os.getenv("MISSION_SENDER_BUSINESS", "").strip()
MISSION_SENDER_EMAIL = os.getenv("MISSION_SENDER_EMAIL", "").strip()
MISSION_SENDER_PHONE = os.getenv("MISSION_SENDER_PHONE", "").strip()
MISSION_SENDER_ADDRESS = os.getenv("MISSION_SENDER_ADDRESS", "").strip()  # a postal address (CAN-SPAM)
# Under the automated tests (tests/__init__.py sets it): the primitives that touch the real desktop refuse to run
# (keyboard / mouse input, UI Automation, screenshots, launching browsers / files / links, theme / brightness / radio
# writes, lock / sleep / shutdown, the Recycle Bin). A test fakes what it needs; one that forgets fails instead of
# touching the PC. Hardware tests opt out explicitly (JARVIS_TEST=0).
TEST_MODE = os.getenv("JARVIS_TEST", "0") == "1"


class BlockedInTests(RuntimeError):
    pass


def real_desktop(what):
    """Call before touching the real desktop: raises BlockedInTests under the automated tests."""
    if TEST_MODE:
        raise BlockedInTests(f"{what} is blocked under the automated tests (fake it in the test)")


EMERGENCY_HOTKEY = os.getenv("EMERGENCY_HOTKEY", "ctrl+alt+j").strip()  # stops everything Jarvis is doing (emergency.py)
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
ERRAND_TTS_PROVIDER = os.getenv("ERRAND_TTS_PROVIDER", "ElevenLabs").strip()  # errand calls' voice (through Twilio)
# ElevenLabs "Jessica" (young American woman, conversational): VOICEID-model-speed_stability_similarity; a lower stability
# sounds less flat. Alternatives: Sarah EXAVITQu4vr4xnSDxMaL, Rachel 21m00Tcm4TlvDq8ikWAM; or Google en-US-Chirp3-HD-Aoede
ERRAND_VOICE = os.getenv("ERRAND_VOICE", "cgSgspJ2msm6clMCkdW9-flash_v2_5-1.0_0.45_0.8").strip()
ERRAND_SPEECH_MODEL = os.getenv("ERRAND_SPEECH_MODEL", "nova-3-general").strip()  # Deepgram speech recognition
# The model that runs an errand call's conversation (code only checks the hard limits). gpt-5 at "minimal" thinking:
# first words in ~1s, much better judgment than gpt-5-mini (~2-5c a call). claude-* works too (Sonnet: ~2-3s, slower).
ERRAND_MODEL = os.getenv("ERRAND_MODEL", "gpt-5").strip()
ERRAND_REASONING = os.getenv("ERRAND_REASONING", "minimal").strip()  # OpenAI models only (blank = model default)
ERRANDS_FILE = Path(os.getenv("ERRANDS_FILE", HERE / "errands.json"))  # errand calls (phone/errand.py): outcomes
VIP_SENDERS = [s.strip().lower() for s in os.getenv("VIP_SENDERS", "").split(",") if s.strip()]  # emails worth a call
SMS_DAILY_LIMIT = int(os.getenv("SMS_DAILY_LIMIT", "20"))  # texts to your phone per day (each one costs a little)
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
