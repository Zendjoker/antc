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

from launcher.model import command_matches, is_tunnel, kill_plan, may_stop
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
    exe: str = ""
    launcher_owner: str = ""


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

    def owns_port(self, pid: int, port: int) -> bool:
        listener = self.listeners().get(int(port))
        if listener is None:
            return False
        return self.is_descendant(int(pid), int(listener.pid))

    def is_descendant(self, ancestor: int, pid: int) -> bool:
        if int(ancestor) == int(pid):
            return True
        current = int(pid)
        for _ in range(12):
            parent = _parent_pid(current)
            if not parent or parent == current:
                return False
            if parent == int(ancestor):
                return True
            current = parent
        return False

    def descendants(self, pid: int) -> list[int]:
        children = _child_map()
        found = []
        pending = list(children.get(int(pid), []))
        while pending:
            child = pending.pop()
            if child in found:
                continue
            found.append(child)
            pending.extend(children.get(child, []))
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
            exe=_exe_path(pid), launcher_owner=_env_value(pid, "ZEND_LAUNCHER_OWNER"),
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
        allowed, why = self._may_stop(pid, record, launcher_pid)
        if not allowed:
            return why
        _signal_break(pid)
        if _wait_dead(pid, graceful_s):
            return "stopped"
        targets = self._stop_targets(pid, record, launcher_pid)
        if targets is None:
            return "process identity changed before stop"
        for args in kill_plan(targets, self._target_commands(targets), force=False):
            _run_taskkill(args)
        if all(not _running(item) for item in targets):
            return "stopped"
        targets = self._stop_targets(pid, record, launcher_pid)
        if targets is None:
            return "process identity changed before stop"
        for args in kill_plan(targets, self._target_commands(targets), force=True):
            _run_taskkill(args)
        if all(not _running(item) for item in targets):
            return "stopped"
        return "failed to stop"

    def _may_stop(self, pid: int, record: dict, launcher_pid: int) -> tuple[bool, str]:
        live = self.describe(pid)
        listener = self.listeners().get(int(record.get("port") or 0))
        ancestor_ok = True
        if listener is not None:
            ancestor_ok = self.is_descendant(pid, listener.pid)
        return may_stop(
            record,
            live.command if live else None,
            live.create_time if live else None,
            live.pid if live else None,
            launcher_pid,
            live_exe=live.exe if live else "",
            listener_pid=listener.pid if listener else None,
            ancestor_ok=ancestor_ok,
        )

    def _stop_targets(self, pid: int, record: dict, launcher_pid: int) -> list[int] | None:
        allowed, _why = self._may_stop(pid, record, launcher_pid)
        if not allowed:
            return None
        targets = [pid]
        for child in self.descendants(pid):
            described = self.describe(child)
            text = f"{described.command if described else ''} {described.exe if described else ''}"
            if is_tunnel(text):
                continue
            if described and command_matches(described.command, str(record.get("script") or "")):
                targets.append(child)
        return targets

    def _target_commands(self, pids: list[int]) -> dict[int, str]:
        commands = {}
        for pid in pids:
            described = self.describe(pid)
            commands[pid] = f"{described.command if described else ''} {described.exe if described else ''}"
        return commands


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


def _exe_path(pid: int) -> str:
    if os.name != "nt" or pid <= 0:
        return ""
    import ctypes
    from ctypes import wintypes

    kernel = _kernel()
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return ""
    try:
        kernel.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value
    except OSError:
        return ""
    finally:
        kernel.CloseHandle(handle)


def _env_value(pid: int, key: str) -> str:
    """One environment value from a process. Other variables are not returned."""
    if os.name != "nt" or pid <= 0 or not key:
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
        header = read_at(params + 0x80, 8)
        if not header or len(header) < 8:
            return ""
        address = int.from_bytes(header[:8], "little")
        if not address:
            return ""
        chunks = []
        for offset in range(0, 65536, 4096):
            piece = read_at(address + offset, 4096)
            if not piece:
                break
            chunks.append(piece)
            if b"\x00\x00\x00\x00" in piece[-4:]:
                break
        raw = b"".join(chunks)
        if not raw:
            return ""
        text = raw.decode("utf-16-le", "replace")
        prefix = (key + "=").lower()
        for item in text.split("\x00"):
            if item.lower().startswith(prefix):
                return item.split("=", 1)[1]
        return ""
    except (OSError, ValueError):
        return ""
    finally:
        kernel.CloseHandle(handle)


def _process_snapshot() -> tuple[dict[int, int], dict[int, list[int]]]:
    """pid -> parent, and parent -> children. Empty when the snapshot cannot be read."""
    parents: dict[int, int] = {}
    children: dict[int, list[int]] = {}
    if os.name != "nt":
        return parents, children
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel = _kernel()
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(Entry)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(Entry)]
    kernel.Process32NextW.restype = wintypes.BOOL
    snap = kernel.CreateToolhelp32Snapshot(2, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return parents, children
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        if not kernel.Process32FirstW(snap, ctypes.byref(entry)):
            return parents, children
        while True:
            pid = int(entry.th32ProcessID)
            parent = int(entry.th32ParentProcessID)
            parents[pid] = parent
            children.setdefault(parent, []).append(pid)
            if not kernel.Process32NextW(snap, ctypes.byref(entry)):
                break
    finally:
        kernel.CloseHandle(snap)
    return parents, children


def _parent_pid(pid: int) -> int:
    parents, _children = _process_snapshot()
    return int(parents.get(int(pid), 0))


def _child_map() -> dict[int, list[int]]:
    _parents, children = _process_snapshot()
    return children


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
