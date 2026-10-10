"""Windows process control for children this launcher itself started.

No image-wide taskkill. Every kill is a single pid that just passed may_stop().
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from launcher.model import command_matches, may_stop, taskkill_args
from launcher.redact import redact

CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
STILL_ACTIVE = 259


@dataclass
class LiveProcess:
    pid: int
    create_time: int
    command: str
    name: str


@dataclass
class Listener:
    pid: int
    name: str
    command: str


@dataclass
class Child:
    pid: int
    create_time: int
    script: str
    argv: list[str]
    popen: subprocess.Popen | None = None


def _no_window_flags() -> int:
    return CREATE_NO_WINDOW if os.name == "nt" else 0


class WindowsPlatform:
    def __init__(self):
        self._described: dict[int, tuple[float, LiveProcess | None]] = {}

    def python_exe(self, root: Path) -> Path | None:
        path = root / ".venv" / "Scripts" / "python.exe"
        return path if path.is_file() else None

    def node_exe(self) -> str | None:
        return _which("node.exe") or _which("node")

    def frontend_installed(self, root: Path) -> bool:
        return (root / "frontend" / "node_modules" / "next").exists()

    def control_port(self, root: Path) -> int:
        return _env_int(root / ".env", "CONTROL_PORT", 8771)

    def listeners(self) -> dict[int, Listener]:
        if os.name != "nt":
            return {}
        try:
            out = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True, text=True, timeout=8, creationflags=_no_window_flags(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        found: dict[int, Listener] = {}
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0].upper() not in ("TCP", "TCPV6"):
                continue
            if parts[3].upper() != "LISTENING":
                continue
            local, pid_s = parts[1], parts[4]
            if not pid_s.isdigit():
                continue
            host, _, port_s = local.rpartition(":")
            if not port_s.isdigit():
                continue
            host = host.strip("[]")
            if host not in ("127.0.0.1", "0.0.0.0", "::", "::1", ""):
                continue
            port = int(port_s)
            pid = int(pid_s)
            if port not in found:
                found[port] = Listener(pid, "", "")
        return found

    def describe(self, pid: int) -> LiveProcess | None:
        if os.name != "nt" or pid <= 0:
            return None
        cached = self._described.get(pid)
        if cached and time.time() - cached[0] < 5:
            return cached[1]
        created = _create_time(pid)
        if created is None or not _running(pid):
            self._described[pid] = (time.time(), None)
            return None
        command = _command_line(pid)
        live = LiveProcess(pid=pid, create_time=created, command=command, name=_image_name(command))
        self._described[pid] = (time.time(), live)
        return live

    def http_ok(self, url: str, timeout: float = 2.0) -> bool:
        import urllib.error
        import urllib.request

        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as response:
                response.read(64)
                return 200 <= response.status < 300
        except (OSError, urllib.error.URLError, TimeoutError, ValueError):
            return False

    def spawn(self, argv: list[str], cwd: Path, env: dict[str, str], log_path: Path) -> Child:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        creation = 0
        startup = None
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = subprocess.SW_HIDE
            creation = CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            startupinfo=startup,
            creationflags=creation,
        )
        threading.Thread(target=_pump, args=(proc, log_path), name=f"log-{proc.pid}", daemon=True).start()
        created = _create_time(proc.pid) or 0
        script = str(argv[-1])
        return Child(pid=proc.pid, create_time=created, script=script, argv=list(argv), popen=proc)

    def stop(self, record: dict, graceful_s: float, launcher_pid: int) -> str:
        pid = int(record.get("pid") or 0)
        live = self.describe(pid)
        allowed, why = may_stop(
            record,
            live.command if live else None,
            live.create_time if live else None,
            live.pid if live else None,
            launcher_pid,
        )
        if not allowed:
            return why
        _signal_break(pid)
        if _wait_dead(pid, graceful_s):
            return "stopped"
        live = self.describe(pid)
        allowed, why = may_stop(
            record,
            live.command if live else None,
            live.create_time if live else None,
            live.pid if live else None,
            launcher_pid,
        )
        if not allowed:
            return why
        if not command_matches(live.command if live else "", str(record.get("script") or "")):
            return "command changed before stop"
        _run_taskkill(taskkill_args(pid, force=False))
        if _wait_dead(pid, 5):
            return "stopped"
        live = self.describe(pid)
        allowed, why = may_stop(
            record,
            live.command if live else None,
            live.create_time if live else None,
            live.pid if live else None,
            launcher_pid,
        )
        if not allowed:
            return why
        _run_taskkill(taskkill_args(pid, force=True))
        if _wait_dead(pid, 5):
            return "stopped"
        return "failed to stop"


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)


def _env_int(path: Path, key: str, default: int) -> int:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or not stripped.startswith(key + "="):
                continue
            raw = stripped.split("=", 1)[1].split("#", 1)[0].strip().strip('"').strip("'")
            return int(raw)
    except (OSError, ValueError):
        return default
    return default


def _image_name(command: str) -> str:
    token = (command or "").strip().split(" ", 1)[0].strip('"')
    return Path(token).name if token else ""


def _command_line(pid: int) -> str:
    if os.name != "nt":
        return ""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
            capture_output=True, text=True, timeout=8, creationflags=_no_window_flags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return redact((out.stdout or "").strip())


def _kernel():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    return kernel


def _open_process(pid: int):
    if os.name != "nt" or pid <= 0:
        return None
    import ctypes
    from ctypes import wintypes

    handle = _kernel().OpenProcess(0x1000, False, int(pid))
    return handle or None


def _create_time(pid: int) -> int | None:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    handle = _open_process(pid)
    if not handle:
        return None
    try:
        created = wintypes.FILETIME()
        exit_t = wintypes.FILETIME()
        kernel_t = wintypes.FILETIME()
        user_t = wintypes.FILETIME()
        if not _kernel().GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exit_t),
                                          ctypes.byref(kernel_t), ctypes.byref(user_t)):
            return None
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        _kernel().CloseHandle(handle)


def _running(pid: int) -> bool:
    """A terminated process can still be opened. Exit code 259 means it is actually running."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    handle = _open_process(pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not _kernel().GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        _kernel().CloseHandle(handle)


def _wait_dead(pid: int, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not _running(pid):
            return True
        time.sleep(0.2)
    return not _running(pid)


def _signal_break(pid: int) -> None:
    if os.name != "nt":
        return
    import signal

    try:
        os.kill(pid, signal.CTRL_BREAK_EVENT)
    except OSError:
        return


def _run_taskkill(args: list[str]) -> None:
    if "/IM" in args:
        raise RuntimeError("refusing an image-wide kill")
    try:
        subprocess.run(args, capture_output=True, text=True, timeout=15, creationflags=_no_window_flags())
    except (OSError, subprocess.TimeoutExpired):
        return


def _pump(proc: subprocess.Popen, path: Path) -> None:
    stream = proc.stdout
    if stream is None:
        return
    try:
        with path.open("a", encoding="utf-8", errors="replace") as handle:
            while True:
                chunk = stream.readline()
                if not chunk:
                    break
                if isinstance(chunk, bytes):
                    text = chunk.decode("utf-8", "replace")
                else:
                    text = chunk
                if len(text) > 2000:
                    text = text[:2000] + "…\n"
                handle.write(redact(text))
                handle.flush()
                _trim(path)
    except OSError:
        return


def _trim(path: Path, limit: int = 1_000_000, keep: int = 400_000) -> None:
    try:
        if path.stat().st_size < limit:
            return
        data = path.read_bytes()[-keep:]
        path.write_bytes(data[data.find(b"\n") + 1:] if b"\n" in data else data)
    except OSError:
        return


# Silence an unused import warning if a type checker looks at STILL_ACTIVE.
_ = STILL_ACTIVE
