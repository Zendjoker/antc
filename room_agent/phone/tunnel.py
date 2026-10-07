"""A public HTTPS address for the phone line, started by Jarvis itself: a Cloudflare quick tunnel (no account needed).

The address changes every time Jarvis starts, so Jarvis points your Twilio number at the new one on each start. (The
iPhone driving Shortcuts need a fixed address: for those, use Tailscale Funnel and set PUBLIC_URL instead.)
"""

import atexit
import logging
import re
import subprocess
import threading
import time

from room_agent import config
from room_agent.config import HERE

log = logging.getLogger("room-agent")
URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_proc = None


def binary():
    for p in (HERE / "bin" / "cloudflared.exe", HERE / "bin" / "cloudflared"):
        if p.exists():
            return str(p)
    return "cloudflared"  # (installed on the PATH)


def start(port, timeout=40, popen=subprocess.Popen):
    """Start the tunnel to 127.0.0.1:port. -> its https address, or None."""
    global _proc
    try:
        _proc = popen([binary(), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        log.warning("phone: couldn't start the tunnel (%s): put cloudflared in bin/ (see phone.md)", e.__class__.__name__)
        return None
    atexit.register(stop)
    found = {}

    def read():
        for line in _proc.stderr:
            m = URL.search(line)
            if m and "url" not in found:
                found["url"] = m[0]
    threading.Thread(target=read, daemon=True).start()
    deadline = time.time() + timeout
    while time.time() < deadline and "url" not in found:
        if _proc.poll() is not None:
            break
        time.sleep(0.2)
    if "url" not in found:
        log.warning("phone: the tunnel didn't give an address")
        stop()
        return None
    return found["url"]


def stop():
    global _proc
    if _proc and _proc.poll() is None:
        _proc.terminate()
    _proc = None
