"""
Room Agent: wake word -> speech-to-text -> Claude (with tools) -> streamed voice.

Pipeline:
  mic (16kHz) -> openWakeWord -> record until silence -> Deepgram or local Whisper STT
  -> Claude or a local Ollama model (streamed sentence by sentence)
  -> ElevenLabs or local Piper TTS -> speaker

Fully free setup: LLM_PROVIDER=ollama, STT_PROVIDER=whisper, TTS_PROVIDER=piper.

After each reply it listens ~4s for a follow-up, so you can keep talking
without repeating the wake word.

Usage:
  python main.py                 # normal voice mode
  python main.py --text          # type instead of talk (no mic needed); add --mute for no audio
  python main.py --check         # verify API keys and devices, then exit
  python main.py --list-devices  # show audio devices for MIC_DEVICE / SPEAKER_DEVICE
"""

from room_agent.cli import main

if __name__ == "__main__":
    main()
