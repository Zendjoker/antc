"""Tests never touch real data, real devices or paid APIs. This runs before every suite (python -m tests.<name>), so it
covers suites that build their own environment too. A suite that sets its own temp paths still wins (it sets them after
this). .env never overrides what's set here (load_dotenv doesn't replace existing variables).

  - every file Jarvis persists (memory, reminders, action history, spend, settings, events, ...) -> a throwaway folder
  - credentials -> an in-memory vault; API keys -> fake values, so a stray real call fails instead of costing money
  - Zigbee, Home Assistant, the phone tunnel, location lookup, tracing and audio debug dumps -> off

Real keys only for an approved live-API run: python -m tests --live-api (sets JARVIS_LIVE_API=1) or a suite run
directly with --live-api. Hardware suites (--hardware) still get fake keys: they test the PC, not the model.
"""

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="jarvis-test-")
for _name, _file in {
    "MEMORY_DB": "memory.db", "MEMORY_FILE": "memory.json", "RECENT_FILE": "conversation.json",
    "REMINDERS_FILE": "reminders.json", "SPEND_FILE": "spend.json", "SETTINGS_FILE": "settings.json",
    "ACTIONS_JOURNAL_FILE": "actions_journal.json", "ZIGBEE_EVENTS_FILE": "zigbee_events.json",
    "PROACTIVE_STATE_FILE": "proactive_state.json", "CONNECTIONS_FILE": "connections.json",
    "LEARNING_DB": "learning.db", "EXPERIENCE_DB": "experience.db", "APPS_CACHE": "apps_cache.json",
    "RESEARCH_FILE": "research.json", "TASKS_FILE": "tasks.json", "LISTS_FILE": "lists.json", "EVENT_REMINDERS_FILE": "event_reminders.json",
    "MISSIONS_DB": "missions.db", "MISSIONS_DIR": "missions", "ERRANDS_FILE": "errands.json",
}.items():
    os.environ[_name] = os.path.join(_TMP, _file)
os.environ.setdefault("JARVIS_TEST", "1")  # (config.TEST_MODE: the real desktop is off-limits; hardware tests set 0)
os.environ.update(JARVIS_VAULT="memory", ZIGBEE="0", HA_URL="", HA_TOKEN="", PHONE_TUNNEL="", PHONE_MODE="0",
                  LOCATION_SOURCE="off", TRACE="0", AUDIO_DEBUG="0", DIAGNOSTICS="0",
                  DIAGNOSTICS_DIR=os.path.join(_TMP, "logs"), AUDIO_DEBUG_DIR=os.path.join(_TMP, "debug"))

LIVE_API = os.environ.get("JARVIS_LIVE_API") == "1" or "--live-api" in sys.argv
if not LIVE_API:
    os.environ.update(ANTHROPIC_API_KEY="sk-ant-test-not-real", OPENAI_API_KEY="sk-test-not-real",
                      DEEPGRAM_API_KEY="test-not-real", ELEVENLABS_API_KEY="test-not-real",
                      TWILIO_ACCOUNT_SID="", TWILIO_AUTH_TOKEN="", GOOGLE_CLIENT_SECRET="",
                      GOOGLE_PLACES_API_KEY="", CODER_BACKEND="none",
                      CODER_SANDBOX_DIR=os.path.join(_TMP, "coder-sandbox"))
