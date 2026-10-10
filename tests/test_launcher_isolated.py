"""Real process control for the launcher. Uses spare local ports, never the live assistant."""

from __future__ import annotations

import os
import shutil
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import launcher.supervisor as supervisor_module
from launcher.platform import WindowsPlatform
from launcher.supervisor import Supervisor

PROJECT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"
RESERVED = {3000, 3100, 8765, 8770, 8771, 3010}

STAND_IN = r"""
import os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, fmt, *args):
        return

name = Path(__file__).name
port = int(os.environ["ZEND_ISOLATED_BACKEND"] if name == "main.py" else os.environ["ZEND_ISOLATED_DASHBOARD"])
server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
print("ready", flush=True)
try:
    while True:
        time.sleep(0.5)
except KeyboardInterrupt:
    Path(os.environ["ZEND_STOP_FLAG"]).write_text("graceful:" + name, encoding="utf-8")
    server.shutdown()
"""

DEV_MJS = r"""
import http from "node:http";
import fs from "node:fs";
const port = Number(process.env.ZEND_DEV_PORT);
const server = http.createServer((req, res) => { res.writeHead(200); res.end("ok"); });
server.listen(port, "127.0.0.1");
process.on("SIGINT", () => {
  fs.writeFileSync(process.env.ZEND_STOP_FLAG, "graceful:dev.mjs");
  server.close(() => process.exit(0));
});
"""


class IsolatedPlatform(WindowsPlatform):
    def python_exe(self, root):
        return PYTHON if PYTHON.is_file() else None


def _free_port() -> int:
    while True:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
        sock.close()
        if port not in RESERVED:
            return port


def _service_owners() -> dict[int, int]:
    """Listener pids for the live assistant ports. Isolated tests must leave these unchanged."""
    platform = WindowsPlatform()
    return {port: item.pid for port, item in platform.listeners().items() if port in (3000, 3100, 8765, 8770, 8771)}


def _listener(port: int) -> int | None:
    item = WindowsPlatform().listeners().get(port)
    return int(item.pid) if item else None


def _build_root(root: Path, backend: int, dashboard: int) -> None:
    (root / "UI").mkdir(parents=True)
    (root / "frontend" / "scripts").mkdir(parents=True)
    (root / "frontend" / "node_modules" / "next").mkdir(parents=True)
    (root / "frontend" / "out").mkdir(parents=True)
    (root / "main.py").write_text(STAND_IN, encoding="utf-8")
    (root / "UI" / "server.py").write_text(STAND_IN, encoding="utf-8")
    (root / "frontend" / "scripts" / "dev.mjs").write_text(DEV_MJS, encoding="utf-8")
    (root / "frontend" / "out" / "index.html").write_text("production-ok", encoding="utf-8")
    (root / ".env").write_text(f"CONTROL_PORT={backend}\n", encoding="utf-8")
    os.environ["ZEND_ISOLATED_BACKEND"] = str(backend)
    os.environ["ZEND_ISOLATED_DASHBOARD"] = str(dashboard)
    os.environ["ZEND_DEV_PORT"] = str(_free_port())
    os.environ["ZEND_STOP_FLAG"] = str(root / "stopped.txt")


def _supervisor(root: Path, mode: str) -> Supervisor:
    sup = Supervisor(root, IsolatedPlatform(), root / "settings.json", root / "state.json")
    sup.set_mode(mode)
    return sup


def _healthy(sup: Supervisor, name: str) -> None:
    row = sup.status[name]
    assert row["state"] == "running", row
    assert row["health"] == "healthy", row
    assert _listener(int(row["port"]))


def test_real_start_stop_restart_and_recovery(tmp_path: Path) -> None:
    if os.name != "nt":
        return
    before = _service_owners()
    backend_port = _free_port()
    dashboard_port = _free_port()
    saved = {
        "DASHBOARD_PORT": supervisor_module.DASHBOARD_PORT,
        "ZEND_DEV_PORT": os.environ.get("ZEND_DEV_PORT"),
    }
    supervisor_module.DASHBOARD_PORT = dashboard_port
    root = tmp_path / "isolated"
    sup = None
    nxt = None
    try:
        _build_root(root, backend_port, dashboard_port)
        sup = _supervisor(root, "classic")
        assert "ready" in sup.start_all()
        _healthy(sup, "backend")
        _healthy(sup, "dashboard")
        backend_pid = int(sup.status["backend"]["pid"])
        dashboard_pid = int(sup.status["dashboard"]["pid"])

        again = sup.start_all()
        assert "not started again" in again
        assert int(sup.status["backend"]["pid"]) == backend_pid
        assert int(sup.status["dashboard"]["pid"]) == dashboard_pid

        assert "ready" in sup.restart_backend()
        assert sup.status["backend"]["state"] == "running"
        assert sup.status["backend"]["health"] == "healthy"
        restarted_backend = int(sup.status["backend"]["pid"])
        assert restarted_backend != backend_pid
        assert int(sup.status["dashboard"]["pid"]) == dashboard_pid
        assert "graceful:main.py" in (root / "stopped.txt").read_text(encoding="utf-8")
        assert _listener(backend_port)

        assert "ready" in sup.restart_frontend()
        assert int(sup.status["backend"]["pid"]) == restarted_backend
        assert int(sup.status["dashboard"]["pid"]) != dashboard_pid
        assert sup.status["dashboard"]["health"] == "healthy"

        assert "ready" in sup.restart_all()
        _healthy(sup, "backend")
        _healthy(sup, "dashboard")
        assert int(sup.status["backend"]["pid"]) != restarted_backend

        reopened = _supervisor(root, "classic")
        reopened.refresh()
        assert reopened.status["backend"]["state"] == "running"
        assert reopened._owned.get("backend"), "ownership was not recovered"
        owned_before = set(reopened._owned)
        message = reopened.start_all()
        assert "not started again" in message
        assert set(reopened._owned) == owned_before
        assert _listener(backend_port)

        stopped = reopened.stop_all()
        assert "stopped" in stopped.lower()
        deadline = time.time() + 8
        while time.time() < deadline and (_listener(backend_port) or _listener(dashboard_port)):
            time.sleep(0.2)
        assert _listener(backend_port) is None
        assert _listener(dashboard_port) is None

        nxt = _supervisor(root, "next")
        assert "ready" in nxt.start_all()
        _healthy(nxt, "frontend")
        front_pid = int(nxt.status["frontend"]["pid"])
        back_pid = int(nxt.status["backend"]["pid"])
        assert "ready" in nxt.restart_frontend()
        assert int(nxt.status["backend"]["pid"]) == back_pid
        assert int(nxt.status["frontend"]["pid"]) != front_pid
        assert nxt.status["frontend"]["health"] == "healthy"
        nxt.stop_all()
    finally:
        for running in (nxt, sup):
            if running is None:
                continue
            try:
                running.stop_all()
            except Exception:
                pass
        supervisor_module.DASHBOARD_PORT = saved["DASHBOARD_PORT"]
        if saved["ZEND_DEV_PORT"] is None:
            os.environ.pop("ZEND_DEV_PORT", None)
        else:
            os.environ["ZEND_DEV_PORT"] = saved["ZEND_DEV_PORT"]
        for key in ("ZEND_ISOLATED_BACKEND", "ZEND_ISOLATED_DASHBOARD", "ZEND_STOP_FLAG"):
            os.environ.pop(key, None)
        shutil.rmtree(root, ignore_errors=True)
        after = _service_owners()
        assert after == before, f"live listeners changed: {before} -> {after}"


if __name__ == "__main__":
    import tempfile
    import traceback
    with tempfile.TemporaryDirectory() as folder:
        try:
            test_real_start_stop_restart_and_recovery(Path(folder))
        except Exception:
            traceback.print_exc()
            raise SystemExit(1)
    print("isolated launcher controls passed")
