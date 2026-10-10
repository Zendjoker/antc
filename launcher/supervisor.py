"""Start, stop, and restart only the services this launcher owns."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from launcher.model import (
    EXTERNAL,
    RUNNING,
    STARTING,
    STOPPED,
    STOPPING,
    UNKNOWN,
    FAILED,
    adoption_ok,
    assess_port,
    cache_targets,
    identity_ok,
    looks_like_ours,
    start_order,
    stop_order,
)
from launcher.platform import WindowsPlatform, control_token

DASHBOARD_PORT = 8765
NEXT_PORT = 3000
STATIC_PORT = 3010
BACKEND_READY_S = 180
DASHBOARD_READY_S = 30
FRONTEND_READY_S = 90
BACKEND_STOP_S = 20
OTHER_STOP_S = 8

ROLES = {
    "backend": "AI backend",
    "dashboard": "Classic dashboard",
    "frontend": "Next.js dev",
}


class Supervisor:
    def __init__(self, root: Path, platform=None, settings_path: Path | None = None, state_path: Path | None = None):
        self.root = Path(root)
        self.platform = platform or WindowsPlatform()
        self.logs = self.root / "logs"
        self.settings_path = settings_path or (self.logs / "launcher-settings.json")
        self.state_path = state_path or (self.logs / "launcher-state.json")
        self.owner = uuid.uuid4().hex
        self.launcher_pid = os.getpid()
        self.mode = "classic"
        self.close_to_tray = True
        self.stop_on_exit = False
        self._owned: dict[str, dict] = {}
        self.status: dict[str, dict] = {}
        self.message = "Checking what is already running."
        self.probed = False
        self.port_note = ""
        self.busy = False
        self._gate = threading.Lock()
        self._listeners: dict = {}
        for name in ("backend", "dashboard", "frontend"):
            self.status[name] = {"state": STOPPED, "detail": "Checking…"}
        self.load_settings()
        self._load_owned()

    def load_settings(self) -> None:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            data = {}
        mode = data.get("frontend", "classic")
        self.mode = mode if mode in ("classic", "next", "production") else "classic"
        self.close_to_tray = bool(data.get("close_to_tray", True))
        self.stop_on_exit = bool(data.get("stop_on_exit", False))

    def save_settings(self) -> None:
        self.logs.mkdir(parents=True, exist_ok=True)
        payload = {
            "frontend": self.mode,
            "close_to_tray": self.close_to_tray,
            "stop_on_exit": self.stop_on_exit,
        }
        self.settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def set_mode(self, mode: str) -> None:
        if mode not in ("classic", "next", "production"):
            return
        self.mode = mode
        self.save_settings()

    def role_label(self, name: str) -> str:
        if name == "frontend" and self.mode == "production":
            return "Production UI"
        return ROLES[name]

    def urls(self) -> dict[str, str]:
        return {
            "classic": f"http://127.0.0.1:{DASHBOARD_PORT}/",
            "next": f"http://127.0.0.1:{self._next_port()}/overview/",
            "production": f"http://127.0.0.1:{STATIC_PORT}/",
        }

    def sign_in_url(self) -> str:
        """What "Open" opens: the Classic dashboard through its sign-in link (it sets the session cookie, then shows the
        page; a bare address shows "Dashboard locked" in a browser that hasn't signed in). The secret is read now, never
        stored."""
        url = self.active_url()
        secret = control_token()
        if secret and url == f"http://127.0.0.1:{DASHBOARD_PORT}/":
            import urllib.parse

            return url + "?key=" + urllib.parse.quote(secret)
        if secret and url == self.urls()["next"]:
            import urllib.parse

            # the Next.js dev entry point signs in through the same backend link, then lands on the overview
            return f"http://127.0.0.1:{self._next_port()}/signin?key=" + urllib.parse.quote(secret)
        if secret and url == self.urls()["production"]:
            import urllib.parse

            return f"http://127.0.0.1:{STATIC_PORT}/signin?key=" + urllib.parse.quote(secret)
        return url

    def classic_sign_in_url(self) -> str:
        import urllib.parse

        secret = control_token()
        url = self.urls()["classic"]
        if not secret:
            return url
        return url + "?key=" + urllib.parse.quote(secret)

    def active_url(self) -> str:
        urls = self.urls()
        front = self.status.get("frontend", {})
        dash = self.status.get("dashboard", {})
        if self.mode == "production" and front.get("state") in (RUNNING, EXTERNAL, STARTING):
            return urls["production"]
        if self.mode == "next" and front.get("state") in (RUNNING, EXTERNAL, STARTING):
            return urls["next"]
        if dash.get("state") in (RUNNING, EXTERNAL, STARTING):
            return urls["classic"]
        return urls["next"] if self.mode == "next" else urls["classic"]

    def active_label(self) -> str:
        front = self.status.get("frontend", {}).get("state")
        dash = self.status.get("dashboard", {}).get("state")
        live = []
        if dash in (RUNNING, EXTERNAL):
            live.append("Classic")
        if front in (RUNNING, EXTERNAL):
            live.append("Production UI" if self.mode == "production" else "Next.js dev")
        selected = {"next": "Next.js dev", "production": "Production UI"}.get(self.mode, "Classic")
        if not live:
            return f"Selected: {selected}"
        return f"Running: {' and '.join(live)}. Selected for the next start: {selected}."

    def refresh(self) -> None:
        """Read-only status. Does not start or stop anything."""
        try:
            self._listeners = self.platform.listeners()
            phone = self._listeners.get(8770)
            if phone:
                described = self.platform.describe(phone.pid)
                if described:
                    phone.command = described.command
                    phone.name = described.name
                name = phone.name or "process"
                self.port_note = f"Port 8770 is in use by pid {phone.pid} ({name}). The launcher does not control that port."
            else:
                self.port_note = ""
            self._refresh_role("backend")
            self._refresh_role("dashboard")
            self._refresh_role("frontend")
            if self.message.startswith("Checking"):
                self.message = "Status updated. Nothing was started or stopped."
        finally:
            self.probed = True

    def start_all(self) -> str:
        if not self._gate.acquire(blocking=False):
            return "A start, stop, or restart is already running."
        self.busy = True
        try:
            notes = []
            for name in start_order(self.mode):
                notes.append(self._start_one(name))
                if self.status[name]["state"] == FAILED:
                    notes.append("Later services were not started.")
                    break
            self.message = " ".join(notes)
            return self.message
        finally:
            self.busy = False
            self._gate.release()

    def stop_all(self) -> str:
        if not self._gate.acquire(blocking=False):
            return "A start, stop, or restart is already running."
        self.busy = True
        try:
            notes = [self._stop_one(name) for name in stop_order(set(self._owned))]
            external = [
                ROLES[name] for name, row in self.status.items()
                if row.get("state") in (EXTERNAL, UNKNOWN)
            ]
            if external:
                notes.append("Left alone: " + ", ".join(external) + ".")
            self.message = " ".join(note for note in notes if note) or "Nothing owned by this launcher was running."
            return self.message
        finally:
            self.busy = False
            self._gate.release()

    def restart_all(self) -> str:
        if not self._gate.acquire(blocking=False):
            return "A start, stop, or restart is already running."
        self.busy = True
        try:
            notes = []
            for name in stop_order(set(self._owned)):
                notes.append(self._stop_one(name))
                self._wait_port_free(self._port(name))
            notes.extend(self._start_one(name) for name in start_order(self.mode))
            self.message = " ".join(note for note in notes if note)
            return self.message
        finally:
            self.busy = False
            self._gate.release()

    def restart_backend(self) -> str:
        return self._restart_named("backend")

    def restart_frontend(self) -> str:
        """Classic restarts the dashboard. Next.js restarts only the dev server."""
        return self._restart_named("frontend" if self.mode in ("next", "production") else "dashboard")

    def _restart_named(self, name: str) -> str:
        if not self._gate.acquire(blocking=False):
            return "A start, stop, or restart is already running."
        self.busy = True
        try:
            notes = [self._stop_one(name)]
            self._wait_port_free(self._port(name))
            notes.append(self._start_one(name))
            self.message = " ".join(note for note in notes if note)
            return self.message
        finally:
            self.busy = False
            self._gate.release()

    def _start_one(self, name: str) -> str:
        self.refresh()
        row = self.status[name]
        if row["state"] in (RUNNING, EXTERNAL):
            return f"{ROLES[name]} is already {row['state'].replace('_', ' ')} and was not started again."
        if row["state"] == UNKNOWN:
            return f"{ROLES[name]} was not started. {row['detail']}"
        if name in self._owned:
            return f"{ROLES[name]} is already managed by this launcher. Use Restart to replace it."
        if name in ("backend", "dashboard") and self.platform.python_exe(self.root) is None:
            self._set(name, FAILED, "Python environment not found at .venv\\Scripts\\python.exe. "
                      "The launcher will not run setup.ps1 or install dependencies.")
            return self.status[name]["detail"]
        spec = self._spec(name)
        if spec is None:
            return self.status[name]["detail"]
        self._set(name, STARTING, "Starting…")
        try:
            child = self.platform.spawn(spec["argv"], spec["cwd"], spec["env"], spec["log"])
        except OSError as exc:
            self._set(name, FAILED, f"Could not start ({exc.__class__.__name__}).")
            return self.status[name]["detail"]
        record = {
            "pid": child.pid,
            "create_time": child.create_time,
            "script": spec.get("script") or child.script,
            "argv": child.argv,
            "exe": spec["argv"][0],
            "port": spec["port"],
            "root": str(self.root),
            "role": name,
            "cwd": str(spec["cwd"]),
        }
        self._owned[name] = record
        self._save_owned()
        self._set(name, STARTING, f"Process {child.pid} started. Waiting until it answers.", pid=child.pid, health="starting")
        ok, why = self._wait_ready(child, spec["url"], spec["timeout"], spec["port"])
        if ok:
            self._listeners = self.platform.listeners()
            owner = self._port_owner(spec["port"]) or child.pid
            self._owned[name] = record
            self._save_owned()
            self._set(name, RUNNING, f"Ready on port {spec['port']} (pid {owner}).",
                      pid=owner, health="healthy", url=spec["url"])
            return f"{self.role_label(name)} is ready."
        exited = child.popen is not None and child.popen.poll() is not None
        if not exited:
            self.platform.stop(record, OTHER_STOP_S, self.launcher_pid)
        self._owned.pop(name, None)
        self._save_owned()
        if exited:
            self._set(name, FAILED, f"Exited with code {child.popen.returncode} before it was ready.", health="down",
                      error=f"Exited with code {child.popen.returncode}")
        else:
            self._set(name, FAILED, f"Not ready: {why}", health="down", error=why)
        return f"{self.role_label(name)} failed. {self.status[name]['detail']}"

    def _stop_one(self, name: str) -> str:
        record = self._owned.get(name)
        if not record:
            state = self.status.get(name, {}).get("state")
            if state in (EXTERNAL, UNKNOWN):
                return f"{ROLES[name]} is not owned by this launcher and was left running."
            return ""
        self._set(name, STOPPING, "Stopping…")
        timeout = BACKEND_STOP_S if name == "backend" else OTHER_STOP_S
        result = self.platform.stop(record, timeout, self.launcher_pid)
        if result in ("stopped", "process is already gone", "no owned pid"):
            self._owned.pop(name, None)
            self._save_owned()
            self._set(name, STOPPED, "Stopped.")
            return f"{ROLES[name]} stopped."
        self._set(name, FAILED, result)
        return f"{ROLES[name]}: {result}"

    def _wait_port_free(self, port: int, timeout: float = 10) -> bool:
        """Restart waits until the service port is no longer listening."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if port not in self.platform.listeners():
                return True
            time.sleep(0.2)
        return port not in self.platform.listeners()

    def _wait_ready(self, child, url: str, timeout: float, port: int) -> tuple[bool, str]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if child.popen is not None and child.popen.poll() is not None:
                return False, f"exited with code {child.popen.returncode}"
            answered = self.platform.http_ok(url, timeout=2)
            owns = self.platform.owns_port(child.pid, port)
            if answered and owns:
                return True, "ready"
            if answered and not owns:
                return False, f"port {port} answered from a different process, so this start was stopped"
            time.sleep(0.4)
        return False, f"no answer from port {port} after {int(timeout)}s"

    def _spec(self, name: str) -> dict | None:
        logs = self.logs
        logs.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["ZEND_LAUNCHER_OWNER"] = self.owner
        if name == "backend":
            python = self.platform.python_exe(self.root)
            script = str((self.root / "main.py").resolve())
            port = self.platform.control_port(self.root)
            return {
                "argv": [str(python), "-u", script],
                "cwd": self.root,
                "env": env,
                "log": logs / "launcher-backend.log",
                "url": f"http://127.0.0.1:{port}/live",
                "port": port,
                "script": script,
                "timeout": BACKEND_READY_S,
            }
        if name == "dashboard":
            python = self.platform.python_exe(self.root)
            script = str((self.root / "UI" / "server.py").resolve())
            return {
                "argv": [str(python), "-u", script],
                "cwd": self.root,
                "env": env,
                "log": logs / "launcher-dashboard.log",
                "url": f"http://127.0.0.1:{DASHBOARD_PORT}/",
                "port": DASHBOARD_PORT,
                "script": script,
                "timeout": DASHBOARD_READY_S,
            }
        if self.mode == "production":
            page = self.root / "frontend" / "out" / "index.html"
            if not page.is_file():
                self._set(name, FAILED, "Production UI is not built at frontend/out. The launcher will not run npm build.",
                          health="down", error="frontend/out is missing")
                return None
            python = self.platform.python_exe(self.root)
            if python is None:
                self._set(name, FAILED, "Python environment not found at .venv\\Scripts\\python.exe.", health="down")
                return None
            env["ZEND_STATIC_ROOT"] = str((self.root / "frontend" / "out").resolve())
            env["ZEND_STATIC_PORT"] = str(STATIC_PORT)
            env["ZEND_BACKEND_URL"] = f"http://127.0.0.1:{DASHBOARD_PORT}"
            return {
                "argv": [str(python), "-m", "launcher.staticfront"],
                "cwd": self.root,
                "env": env,
                "log": logs / "launcher-frontend.log",
                "url": f"http://127.0.0.1:{STATIC_PORT}/",
                "port": STATIC_PORT,
                "script": "launcher.staticfront",
                "timeout": DASHBOARD_READY_S,
            }
        node = self.platform.node_exe()
        if not node:
            self._set(name, FAILED, "Node.js was not found on PATH. The launcher will not install it.")
            return None
        if not self.platform.frontend_installed(self.root):
            self._set(name, FAILED, "frontend/node_modules is missing. Run npm install in frontend/ yourself. "
                      "The launcher will not install dependencies.")
            return None
        script = str((self.root / "frontend" / "scripts" / "dev.mjs").resolve())
        port = self._next_port()
        env["ZEND_BACKEND_URL"] = f"http://127.0.0.1:{DASHBOARD_PORT}"
        env["ZEND_DEV_PORT"] = str(port)
        return {
            "argv": [node, script],
            "cwd": self.root / "frontend",
            "env": env,
            "log": logs / "launcher-frontend.log",
            "url": f"http://127.0.0.1:{port}/overview/",
            "port": port,
            "script": script,
            "timeout": FRONTEND_READY_S,
        }

    def _refresh_role(self, name: str) -> None:
        port = self._port(name)
        url = self._ready_url(name)
        listener = self._listeners.get(port)
        cwd = ""
        if listener:
            described = self.platform.describe(listener.pid)
            if described:
                listener.command = described.command or listener.command
                listener.name = described.name or listener.name
                cwd = described.cwd
        healthy = self.platform.http_ok(url, timeout=1.5) if url else False
        health = "healthy" if healthy else ("down" if listener is None else "unhealthy")
        record = self._owned.get(name)
        live = self.platform.describe(record["pid"]) if record else None
        if record and live and int(live.pid) == int(record.get("pid") or 0) and int(live.create_time) == int(record.get("create_time") or 0):
            same = (not live.command) or adoption_ok(record, live.command, live.create_time, live.pid)
            owns = (not listener) or self.platform.is_descendant(int(record["pid"]), int(listener.pid))
            if same and owns:
                shown = listener.pid if listener else live.pid
                if healthy:
                    self._set(name, RUNNING, f"Ready on port {port} (pid {shown}).", pid=shown, health="healthy")
                elif self.status[name]["state"] != STARTING:
                    self._set(name, STARTING, f"Process {shown} is up. Waiting for it to answer.", pid=shown, health="starting")
                return
        if record:
            self._owned.pop(name, None)
            self._save_owned()
        command = listener.command if listener else ""
        described = self.platform.describe(listener.pid) if listener else None
        kind = assess_port(
            listening=listener is not None,
            healthy=healthy,
            command=command,
            root=str(self.root),
            role=name,
            owned_alive=False,
            cwd=cwd,
        )
        if kind == "free":
            if self.status[name]["state"] not in (FAILED, STARTING, STOPPING):
                self._set(name, STOPPED, "Not running.", health="down")
            return
        if kind == EXTERNAL and self._recover(name, described, port, url):
            return
        if kind == EXTERNAL:
            pid = listener.pid if listener else "?"
            self._set(name, EXTERNAL, f"Already running outside this launcher (pid {pid}). Not stopped.",
                      pid=pid, health=health)
            return
        who = f"pid {listener.pid} ({listener.name})" if listener else "an unknown process"
        self._set(name, UNKNOWN, f"Port {port} is in use by {who}. This launcher did not stop it.",
                  pid=listener.pid if listener else "", health=health, error=f"Port {port} is not a free ZendAgent port.")

    def _port(self, name: str) -> int:
        if name == "backend":
            return self.platform.control_port(self.root)
        if name == "dashboard":
            return DASHBOARD_PORT
        if self.mode == "production":
            return STATIC_PORT
        return self._next_port()

    def _port_owner(self, port: int) -> int | None:
        listener = self._listeners.get(port)
        return int(listener.pid) if listener else None

    def _recover(self, name: str, described, port: int, url: str) -> bool:
        """Adopt a dead launcher's process when its environment marker and identity both match."""
        if described is None or not described.launcher_owner:
            return False
        if not identity_ok(described.command, described.exe, str(self.root), name, described.cwd):
            return False
        self._owned[name] = {
            "pid": described.pid,
            "create_time": described.create_time,
            "script": described.command,
            "exe": described.exe,
            "port": port,
            "root": str(self.root),
            "role": name,
            "cwd": described.cwd,
            "recovered": True,
        }
        self._save_owned()
        self._set(name, RUNNING, f"Recovered a launcher process on port {port} (pid {described.pid}).",
                  pid=described.pid, health="healthy", url=url)
        return True

    def _next_port(self) -> int:
        try:
            return int(os.environ.get("ZEND_DEV_PORT", str(NEXT_PORT)))
        except ValueError:
            return NEXT_PORT

    def _ready_url(self, name: str) -> str:
        if name == "backend":
            return f"http://127.0.0.1:{self._port(name)}/live"
        if name == "dashboard":
            return f"http://127.0.0.1:{DASHBOARD_PORT}/"
        if self.mode == "production":
            return f"http://127.0.0.1:{STATIC_PORT}/"
        return f"http://127.0.0.1:{self._next_port()}/overview/"

    def clean_frontend_cache(self) -> str:
        """Delete regenerable Next caches. Refuses while a frontend process is up, and never touches runtime files."""
        front = self.status.get("frontend", {}).get("state")
        if front in (RUNNING, EXTERNAL, STARTING, STOPPING):
            return "Frontend cache was not cleaned because a frontend process is still running."
        removed = []
        for path in cache_targets(self.root):
            if not path.is_dir():
                continue
            try:
                import shutil
                shutil.rmtree(path)
            except OSError as exc:
                return f"Could not remove {path.name} ({exc.__class__.__name__})."
            removed.append(str(path.relative_to(self.root)))
        if not removed:
            return "No frontend cache to clean."
        return "Removed " + ", ".join(removed) + "."

    def _set(self, name: str, state: str, detail: str, **extra) -> None:
        self.status[name] = {
            "state": state,
            "detail": detail,
            "pid": extra.get("pid", ""),
            "port": extra.get("port", self._port(name)),
            "health": extra.get("health", ""),
            "url": extra.get("url", self._ready_url(name)),
            "error": extra.get("error", detail if state == FAILED else ""),
        }

    def _load_owned(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return
        owned = data.get("components") or {}
        if isinstance(owned, dict):
            self._owned = {key: value for key, value in owned.items() if isinstance(value, dict)}

    def _save_owned(self) -> None:
        try:
            self.logs.mkdir(parents=True, exist_ok=True)
            payload = {"owner": self.owner, "components": self._owned}
            self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            return
