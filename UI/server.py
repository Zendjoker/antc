"""Local web dashboard for the room agent: live status + an editable .env settings page.

Run:  python UI/server.py        then open http://127.0.0.1:8765
Binds to localhost only — this page can read and write API keys, never expose the port to a network.

Settings are written straight to .env. Most of them are only read once at startup
(room_agent/config.py), so the running agent needs a restart to pick up changes.
"""

import json
import re
import sys
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from room_agent import config  # noqa: E402
from room_agent.audio.voices import ELEVEN_VOICES, PIPER_VOICES  # noqa: E402

ENV_FILE = ROOT / ".env"

# Keys whose value is "1"/"0" and means true/false (matched against `os.getenv(X, ...) == "1"` in config.py).
BOOL_KEYS = {
    "ECHO_CANCELLATION", "NOISE_SUPPRESSION", "BARGE_IN", "QUIET_ALLOWS_TIMERS", "TRACE",
    "BARGE_VERIFY", "BARGE_DUCK", "SPEAKER_VERIFY", "FILLERS", "PROMPT_CACHE", "MEMORY_BATCH",
}
# Keys that hold a secret and should never be sent to the browser in full.
SECRET_HINTS = ("KEY", "TOKEN", "SECRET")

OPTIONS = {
    "LLM_PROVIDER": ["claude", "ollama"],
    "LLM_DEFAULT": ["openai", "claude"],
    "LLM_SMART": ["claude", "openai"],
    "STT_PROVIDER": ["deepgram", "whisper"],
    "TTS_PROVIDER": ["elevenlabs", "piper"],
    "WHISPER_DEVICE": ["cpu", "cuda"],
    "WAKE_SOUND_ON": ["startup", "wake", "off"],
    "UNITS": ["imperial", "metric"],
    "BUDGET_FALLBACK": ["ollama", "stop"],
    "ELEVENLABS_VOICE_ID": [{"value": v, "label": n} for n, (v, _d) in ELEVEN_VOICES.items()],
    "PIPER_VOICE": [{"value": v, "label": n} for n, (v, _d) in PIPER_VOICES.items()],
}

SECTION_RE = re.compile(r"^#\s*-{2,}\s*(.+?)\s*-{2,}\s*$")
VAR_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def is_secret(key):
    return any(h in key for h in SECRET_HINTS)


def mask(value):
    if not value:
        return ""
    if len(value) <= 8:
        return "••••••••"
    return f"{value[:4]}…{value[-4:]}"


def split_value_comment(rest):
    """"value   # comment" -> (value, "   # comment")."""
    m = re.search(r"\s+#", rest)
    return (rest.strip(), "") if not m else (rest[: m.start()].strip(), rest[m.start():])


def field_type(key, value):
    if key in BOOL_KEYS:
        return "bool"
    if key in OPTIONS:
        return "select"
    try:
        float(value)
        return "number"
    except (TypeError, ValueError):
        return "text"


def read_env():
    sections = [{"title": "General", "fields": []}]
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        sm = SECTION_RE.match(raw.strip())
        if sm:
            sections.append({"title": sm.group(1), "fields": []})
            continue
        vm = VAR_RE.match(raw)
        if not vm:
            continue
        key, rest = vm.group(1), vm.group(2)
        value, comment = split_value_comment(rest)
        secret = is_secret(key)
        sections[-1]["fields"].append({
            "key": key,
            "value": mask(value) if secret else value,
            "secret": secret,
            "has_value": bool(value),
            "comment": comment.lstrip("# ").strip(),
            "type": field_type(key, value),
            "options": OPTIONS.get(key),
        })
    return [s for s in sections if s["fields"]]


def write_env(changes):
    """Rewrite only the lines for keys in `changes`. A blank value for a secret means "leave it alone"."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        vm = VAR_RE.match(line)
        if not vm or vm.group(1) not in changes:
            out.append(line)
            continue
        key = vm.group(1)
        new_value = changes[key]
        if is_secret(key) and new_value == "":
            out.append(line)
            continue
        _, comment = split_value_comment(vm.group(2))
        out.append(f"{key}={new_value}{comment}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


app = Flask(__name__, static_folder=str(Path(__file__).parent), static_url_path="")


@app.after_request
def no_stale_pages(resp):
    resp.headers["Cache-Control"] = "no-cache"  # always the current page and scripts after an update (it's all local)
    return resp


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/env")
def api_get_env():
    return jsonify(sections=read_env())


@app.post("/api/env")
def api_post_env():
    if (bad := _guard()):
        return bad
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify(error="expected a JSON object of KEY: value"), 400
    changes = {}
    for key, value in body.items():
        if not KEY_RE.match(key):
            continue
        changes[key] = str(value).replace("\n", " ").replace("\r", " ").strip()
    if not changes:
        return jsonify(error="no valid settings in request"), 400
    write_env(changes)
    return jsonify(ok=True, changed=list(changes), restart_required=True)


JARVIS = f"http://127.0.0.1:{config.CONTROL_PORT}"


@app.get("/api/live")
def api_live():
    """What Jarvis is doing right now (from the running Jarvis; offline if it isn't running)."""
    import requests

    try:
        return jsonify(requests.get(f"{JARVIS}/live", timeout=2).json())
    except Exception:
        return jsonify(online=False)


@app.post("/api/command")
def api_command():
    import requests

    if (bad := _guard()):
        return bad
    try:
        r = requests.post(f"{JARVIS}/command", json=request.get_json(silent=True) or {}, timeout=90,
                          headers={"X-Jarvis": "1"})
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify(error="Jarvis isn't running. Start it with: python main.py"), 503


@app.get("/api/knowledge")
def api_knowledge():
    """What Jarvis remembers about you and what it has learned (from the running Jarvis)."""
    import requests

    try:
        return jsonify(requests.get(f"{JARVIS}/knowledge", timeout=4).json())
    except Exception:
        return jsonify(error="Jarvis isn't running. Start it with: python main.py"), 503


@app.post("/api/action")
def api_action():
    """A dashboard button (media, volume, timers, undo, quiet mode, forget, call me): run by Jarvis."""
    import requests

    if (bad := _guard()):
        return bad
    try:
        r = requests.post(f"{JARVIS}/action", json=request.get_json(silent=True) or {}, timeout=30,
                          headers={"X-Jarvis": "1"})
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify(ok=False, message="Jarvis isn't running. Start it with: python main.py"), 503


@app.get("/api/status")
def api_status():
    spend, saved = {}, {}
    try:
        spend = json.loads(config.SPEND_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        pass
    try:
        saved = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError, OSError):
        pass
    return jsonify(
        llm_provider=config.LLM_PROVIDER,
        llm_default=config.LLM_DEFAULT,
        model=config.MODEL,
        openai_model=config.OPENAI_MODEL,
        ollama_model=config.OLLAMA_MODEL,
        stt_provider=config.STT_PROVIDER,
        whisper_model=config.WHISPER_MODEL,
        tts_provider=config.TTS_PROVIDER,
        wake_word=config.WAKE_WORD,
        mic_device=config.MIC_DEVICE or "(system default)",
        speaker_device=config.SPEAKER_DEVICE or "(system default)",
        daily_budget=config.DAILY_BUDGET_USD,
        speaker_verify=config.SPEAKER_VERIFY,
        voiceprint_enrolled=config.VOICEPRINT_FILE.exists(),
        spend=spend,
        voice=saved,
    )


# ---------------------------------------------------------------- Settings -> Connections
# Provider status and connect / reconnect / disconnect. Sign-in happens in the system browser (Google's own page);
# this page never sees a password or a token. Write endpoints need the X-Jarvis header (a cross-site page can't send
# it without permission) and a same-origin Origin, so another website can't trigger them.
import threading  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402

from room_agent.integrations import all_providers, provider  # noqa: E402
from room_agent.integrations.base import IntegrationError  # noqa: E402

FLOWS = {}  # flow id -> {"state": pending|done|canceled|error, "message", "at"}
SETUP_HINT = {"google": "Create a Google Cloud OAuth client of type Desktop app, then put GOOGLE_CLIENT_ID and "
                        "GOOGLE_CLIENT_SECRET in .env (or save the downloaded JSON as google_client.json) and restart this page."}


@app.before_request
def _only_local():
    """Every request must name this PC as its Host: a website that re-points its own name to 127.0.0.1 (DNS
    rebinding) would otherwise be able to read the live view and settings."""
    from room_agent.control import local_host

    if not local_host(request.headers.get("Host", "")):
        return jsonify(error="refused: not a local request"), 403
    return None


def _guard():
    origin = request.headers.get("Origin", "")
    if request.headers.get("X-Jarvis") != "1" or (origin and not origin.startswith(("http://127.0.0.1:", "http://localhost:"))):
        return jsonify(error="refused: requests must come from this page"), 403
    return None


def _provider_view(p):
    view = {"id": p.id, "name": p.display_name, "icon": p.icon, "configured": p.configured(),
            "setup": SETUP_HINT.get(p.id, ""), "accounts": []}
    from room_agent.integrations import state as cstate
    from room_agent.integrations.google.provider import SCOPE_LABELS

    info = cstate.provider(p.id)
    for account, a in info["accounts"].items():
        granted = set(a.get("scopes", []))
        disabled = set(a.get("disabled", []))
        view["accounts"].append({
            "account": account, "active": account == info.get("active"), "status": p.connection_status(account),
            "last_success": a.get("last_success"), "last_error": a.get("last_error", ""),
            "permissions": sorted({SCOPE_LABELS.get(s, s) for s in granted}),
            "services": [{"id": svc.id, "name": svc.name, "levels": [
                {"id": l.id, "label": l.label, "granted": set(l.scopes) <= granted,
                 "enabled": set(l.scopes) <= granted and f"{svc.id}.{l.id}" not in disabled,
                 "consequential": l.consequential} for l in svc.levels]} for svc in p.services]})
    return view


@app.get("/api/connections")
def api_connections():
    return jsonify(providers=[_provider_view(p) for p in all_providers()])


def _start(fn):
    fid = uuid.uuid4().hex
    FLOWS[fid] = {"state": "pending", "message": "Waiting for you to sign in in your browser...", "at": time.time()}

    def run():
        try:
            fn()
            FLOWS[fid].update(state="done", message="Connected.")
        except IntegrationError as e:
            FLOWS[fid].update(state="canceled" if e.code == "canceled" else "error", message=e.say())
        except Exception as e:  # (never the exception text: it could include request details)
            FLOWS[fid].update(state="error", message=f"Something went wrong ({e.__class__.__name__}).")
    threading.Thread(target=run, daemon=True).start()
    return jsonify(flow=fid)


@app.get("/api/connections/flows/<fid>")
def api_flow(fid):
    return jsonify(FLOWS.get(fid) or {"state": "error", "message": "unknown sign-in"})


@app.post("/api/connections/<pid>/connect")
def api_connect(pid):
    if (bad := _guard()):
        return bad
    body = request.get_json(silent=True) or {}
    levels = body.get("levels") or {"gmail": ["read"], "calendar": ["read"]}
    return _start(lambda: provider(pid).connect(levels))


@app.post("/api/connections/<pid>/reconnect")
def api_reconnect(pid):
    if (bad := _guard()):
        return bad
    account = (request.get_json(silent=True) or {}).get("account", "")
    return _start(lambda: provider(pid).reconnect(account))


@app.post("/api/connections/<pid>/access")
def api_access(pid):
    if (bad := _guard()):
        return bad
    b = request.get_json(silent=True) or {}
    p = provider(pid)
    if not b.get("on"):
        p.enable(b["account"], b["service"], b["level"], on=False)
        return jsonify(ok=True)
    return _start(lambda: p.enable(b["account"], b["service"], b["level"], on=True))


@app.post("/api/connections/<pid>/active")
def api_active(pid):
    if (bad := _guard()):
        return bad
    provider(pid).set_active((request.get_json(silent=True) or {}).get("account", ""))
    return jsonify(ok=True)


@app.post("/api/connections/<pid>/disconnect")
def api_disconnect(pid):
    if (bad := _guard()):
        return bad
    account = (request.get_json(silent=True) or {}).get("account", "")
    revoked = provider(pid).disconnect(account)
    return jsonify(ok=True, revoked=revoked)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8765, debug=False)
