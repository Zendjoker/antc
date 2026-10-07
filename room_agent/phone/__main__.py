"""Jarvis on the phone only (no room mic or speaker):  python -m room_agent.phone

Same brain, memory, email, calendar and tools; it just doesn't listen in the room. Useful when the room Jarvis isn't
running, or to test calls. (Don't run it at the same time as `python main.py` with PHONE_MODE=1: same port.)
"""

import logging
import sys
import time

from room_agent import config
from room_agent import runtime as rt


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if not config.PHONE_MODE:
        sys.exit("PHONE_MODE=1 isn't set in .env (see phone.md)")
    from room_agent.cli import USER_NAME, MemoryWriter, memory_call_text, memory_call_tool
    from room_agent.phone import server
    from room_agent.tools import apps, location

    rt.tts_enabled = False  # (no room audio: replies go to the phone)
    rt.writer = MemoryWriter(rt.memory, USER_NAME, memory_call_tool, memory_call_text, defer=True)
    apps.warm()
    location.warm()
    missing = server.start()
    print(f"Jarvis is on the phone line (port {config.PHONE_PORT})." + (f" Still needed: {', '.join(missing)}" if missing else ""))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("Bye.")
    finally:
        rt.writer.conversation_ended(rt.writer.last_summarized)
        rt.writer.flush(timeout=15)


if __name__ == "__main__":
    main()
