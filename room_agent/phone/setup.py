"""python main.py --setup-phone  /  --test-call : set phone mode up, and check it works."""

import re
import secrets

import requests

from room_agent import config
from room_agent.config import HERE

ENV = HERE / ".env"


def set_env(key, value):
    """Write KEY=value into .env (replacing the line if it's there)."""
    text = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    line = f"{key}={value}"
    if re.search(rf"^{key}=", text, re.M):
        text = re.sub(rf"^{key}=.*$", line, text, count=1, flags=re.M)
    else:
        text = text.rstrip("\n") + ("\n\n# --- Phone mode ---\n" if "# --- Phone mode ---" not in text else "\n") + line + "\n"
    tmp = ENV.with_name(".env.setup.tmp")  # (swapped in whole: a crash never leaves .env half-written)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(ENV)
    setattr(config, key, value)


def setup():
    from room_agent.phone.server import ready, twilio

    print("Phone mode setup\n")
    if not config.PHONE_TOKEN:
        set_env("PHONE_TOKEN", secrets.token_urlsafe(24))
        print("  made a secret token for your iPhone (saved as PHONE_TOKEN in .env)")
    if not config.PHONE_MODE:
        set_env("PHONE_MODE", "1")
        config.PHONE_MODE = True
        print("  turned phone mode on (PHONE_MODE=1)")
    missing = ready()
    if missing:
        print("\n  Still needed in .env (see phone.md): " + ", ".join(missing))
    url = config.PUBLIC_URL
    if url:
        try:
            ok = requests.get(f"{url}/phone/health", timeout=8).status_code == 200
        except requests.RequestException:
            ok = False
        print(f"\n  {url} reaches Jarvis: " + ("yes" if ok else "NO (is Jarvis running with PHONE_MODE=1, and is the "
                                                               "tunnel on? see phone.md step 2)"))
    if not missing:
        try:
            twilio().set_voice_webhook(config.TWILIO_NUMBER, f"{url}/twilio/voice")
            print(f"  Twilio: calls to {config.TWILIO_NUMBER} now go to Jarvis")
        except Exception as e:
            print(f"  Twilio: couldn't point your number at Jarvis ({e}). Check TWILIO_* in .env.")
    if url and config.PHONE_TOKEN:
        print(f"""
  iPhone Shortcuts (Shortcuts app > Automation > New Automation), two of them:
    1. "CarPlay" > Connects  (or "Driving" Focus > Turns On) > Run Immediately
       Action: Get Contents of URL
         URL:     {url}/phone/driving
         Method:  POST
         Headers: Authorization = Bearer {config.PHONE_TOKEN}
         Body:    JSON, driving = true (Boolean)
    2. Same, but CarPlay > Disconnects (or the Focus turns off), and driving = false.
  Optional, for your location while you drive: in automation 1, add "Get Current Location" first, then a second
  "Get Contents of URL" to {url}/phone/location with JSON lat / lon (Latitude / Longitude of that location).""")
    return not missing


def test_call():
    from room_agent.phone.server import place_call, ready

    missing = ready()
    if missing:
        print("Not ready, still needed in .env: " + ", ".join(missing))
        return False
    print(f"Calling {config.MY_PHONE} (Jarvis must be running with PHONE_MODE=1 to answer the conversation)...")
    place_call("test", "Hey, it's Jarvis, just testing the line. Can you hear me okay?")
    print("Ringing. Answer and talk to it.")
    return True
