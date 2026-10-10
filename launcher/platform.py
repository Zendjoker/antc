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
    cwd: str = ""


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
        live = LiveProcess(
            pid=pid, create_time=created, command=command,
            name=_image_name(command), cwd=_process_cwd(pid),
        )
        self._described[pid] = (time.time(), live)
        return live

    def http_ok(self, url: str, timeout: float = 2.0) -> bool:
        import urllib.error
        import urllib.parse
        import urllib.request

        headers = {}
        if urllib.parse.urlsplit(url).path == "/live":  # (Jarvis's control link answers 403 without the local secret)
            secret = control_token()
            if secret:
                headers["X-Jarvis-Token"] = secret
        try:
            req = urllib.request.Request(url, method="GET", headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                response.read(64)
                return 200 <= response.status < 300
        except urllib.error.HTTPError as e:  # (the dashboard says "locked" until a browser has signed in: it's up)
            try:
                return e.code == 401 and b"Dashboard locked" in e.read(2048)
            except OSError:
                return False
        except (OSError, urllib.error.URLError, TimeoutError, ValueError):
            return False

    def spawn(self, argv: list[str], cwd: Path, env: dict[str, str], log_path: Path) -> Child:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        creation = 0
        if os.name == "nt":
            creation = CREATE_NEW_CONSOLE
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=creation,
        )
        threading.Thread(target=_pump, args=(proc, log_path), name=f"log-{proc.pid}", daemon=True).start()
        threading.Thread(target=_hide_console, args=(proc.pid,), name=f"hide-{proc.pid}", daemon=True).start()
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
    text = (command or "").strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        token = text[1:end] if end > 1 else ""
    else:
        token = text.split(" ", 1)[0]
    return Path(token).name if token else ""


def _command_line(pid: int) -> str:
    if os.name != "nt":
        return ""
    native = _command_line_native(pid)
    if native:
        return redact(native)
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
            capture_output=True, text=True, timeout=8, creationflags=_no_window_flags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return redact((out.stdout or "").strip())


def _command_line_native(pid: int) -> str:
    """Read one process command line. Empty when access is denied; the caller can fall back."""
    import ctypes

    kernel = _kernel()
    handle = kernel.OpenProcess(0x0410, False, int(pid))  # QUERY_INFORMATION | VM_READ
    if not handle:
        return ""
    try:
        class UnicodeString(ctypes.Structure):
            _fields_ = [
                ("Length", ctypes.c_ushort),
                ("MaximumLength", ctypes.c_ushort),
                ("Buffer", ctypes.c_void_p),
            ]

        ntdll = ctypes.windll.ntdll
        ntdll.NtQueryInformationProcess.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        ntdll.NtQueryInformationProcess.restype = ctypes.c_long
        needed = ctypes.c_ulong(0)
        ntdll.NtQueryInformationProcess(handle, 60, None, 0, ctypes.byref(needed))
        size = max(int(needed.value or 0), ctypes.sizeof(UnicodeString) + 2)
        if size > 1_000_000:
            return ""
        buf = ctypes.create_string_buffer(size)
        status = ntdll.NtQueryInformationProcess(handle, 60, buf, size, ctypes.byref(needed))
        if status != 0:
            return ""
        info = UnicodeString.from_buffer(buf)
        if not info.Buffer or not info.Length:
            return ""
        return ctypes.wstring_at(info.Buffer, info.Length // 2).strip()
    except (OSError, ValueError):
        return ""
    finally:
        kernel.CloseHandle(handle)


def _process_cwd(pid: int) -> str:
    """Working directory of a 64-bit process. Empty when it cannot be read."""
    if os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes

    kernel = _kernel()
    handle = kernel.OpenProcess(0x0410, False, int(pid))
    if not handle:
        return ""
    try:
        class Basic(ctypes.Structure):
            _fields_ = [
                ("Reserved1", ctypes.c_void_p),
                ("PebBaseAddress", ctypes.c_void_p),
                ("Reserved2", ctypes.c_void_p * 2),
                ("UniqueProcessId", ctypes.c_void_p),
                ("Reserved3", ctypes.c_void_p),
            ]

        class UnicodeString(ctypes.Structure):
            _fields_ = [
                ("Length", ctypes.c_ushort),
                ("MaximumLength", ctypes.c_ushort),
                ("Buffer", ctypes.c_void_p),
            ]

        ntdll = ctypes.windll.ntdll
        ntdll.NtQueryInformationProcess.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        ntdll.NtQueryInformationProcess.restype = ctypes.c_long
        kernel.ReadProcessMemory.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        kernel.ReadProcessMemory.restype = wintypes.BOOL
        info = Basic()
        needed = ctypes.c_ulong()
        if ntdll.NtQueryInformationProcess(handle, 0, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(needed)) != 0:
            return ""
        if not info.PebBaseAddress:
            return ""

        def read_at(address: int, size: int):
            buf = ctypes.create_string_buffer(size)
            got = ctypes.c_size_t()
            if not address or not kernel.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, size, ctypes.byref(got)):
                return None
            return buf.raw[:got.value]

        params_raw = read_at(info.PebBaseAddress + 0x20, 8)
        if not params_raw or len(params_raw) < 8:
            return ""
        params = int.from_bytes(params_raw[:8], "little")
        us_raw = read_at(params + 0x38, 16)
        if not us_raw or len(us_raw) < 16:
            return ""
        info_us = UnicodeString.from_buffer_copy(us_raw)
        if not info_us.Buffer or not info_us.Length or info_us.Length > 32768:
            return ""
        text = read_at(info_us.Buffer, info_us.Length)
        if not text:
            return ""
        return text.decode("utf-16-le", "replace").rstrip("\x00").strip()
    except (OSError, ValueError):
        return ""
    finally:
        kernel.CloseHandle(handle)


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


_console_lock = threading.Lock()


def _with_child_console(pid: int, action) -> bool:
    """Attach to a child console, run action, then restore the launcher's own console."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    with _console_lock:
        kernel.FreeConsole()
        if not kernel.AttachConsole(int(pid)):
            kernel.AttachConsole(0xFFFFFFFF)
            return False
        try:
            return bool(action(kernel, wintypes))
        finally:
            kernel.FreeConsole()
            kernel.AttachConsole(0xFFFFFFFF)


def _hide_console(pid: int) -> None:
    """A new console is required so Ctrl+C can reach Python. Hide it as soon as it exists."""
    user32 = None
    if os.name == "nt":
        import ctypes
        user32 = ctypes.windll.user32

    def hide(kernel, _wintypes):
        hwnd = kernel.GetConsoleWindow()
        if hwnd and user32 is not None:
            user32.ShowWindow(hwnd, 0)
        return True

    for _ in range(25):
        if not _running(pid):
            return
        if _with_child_console(pid, hide):
            return
        time.sleep(0.2)


def _signal_break(pid: int) -> None:
    """Ctrl+C on the child console. Python turns that into KeyboardInterrupt and runs its cleanup."""
    def send(kernel, wintypes):
        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        handler = handler_type(lambda _code: True)
        kernel.SetConsoleCtrlHandler(handler, True)
        ok = kernel.GenerateConsoleCtrlEvent(0, 0)  # CTRL_C_EVENT
        time.sleep(0.4)
        _signal_break.handler = handler
        return bool(ok)

    import ctypes
    _with_child_console(pid, send)


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


def control_token() -> str:
    """Jarvis's local secret (room_agent/localauth.py), read when needed and never stored or logged by the launcher."""
    path = os.getenv("CONTROL_TOKEN_FILE", "").strip() or str(Path(__file__).resolve().parents[1] / "control.token")
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
