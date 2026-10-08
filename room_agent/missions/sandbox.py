"""Restrictions applied to the coding worker BEFORE it runs (not just checks afterwards).

    workspace   a fresh throwaway folder outside Jarvis and outside the missions folder, holding a COPY of the one site's
                own files and nothing else. The worker never sees or touches the real site, the repo, .env, other sites,
                the user's documents or Claude's own settings. Jarvis copies validated results back itself
                (sitegen.swap_in), so a misbehaving worker can at worst ruin its own copy.
    environment an allowlist, not a denylist: no API keys / tokens (except ANTHROPIC_API_KEY when the CLI must bill
                it), no proxies, no user paths. HOME / USERPROFILE / APPDATA / LOCALAPPDATA / TEMP point INTO the
                sandbox, so the CLI can't load the user's settings, memory files, MCP servers or credentials.
    process     (Windows) a Job Object: at most JOB_MAX_PROCESSES processes, JOB_MEMORY_MB of memory, no breakaway,
                no clipboard / desktop / display-settings / shutdown access (UI restrictions), and the whole process
                tree is killed when Jarvis cancels it or exits (KILL_ON_JOB_CLOSE). If the job can't be applied, the
                worker is not run (fail closed).
    tools       (Claude Code) file tools only; no shell, no web, no MCP servers (--strict-mcp-config with an empty set).
Not done (see MISSIONS.md): network isolation for the CLI process (it needs the Anthropic API; Windows has no per-process
firewall rule without admin rights), a low-integrity / AppContainer token.
"""

import ctypes
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from room_agent import config
from room_agent.missions import runctx

log = logging.getLogger("room-agent")
JOB_MAX_PROCESSES = 8
JOB_MEMORY_MB = 2048
ENV_KEEP = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
            "OS", "LANG", "LC_ALL")


class SandboxError(Exception):
    pass


def root():
    r = Path(os.getenv("CODER_SANDBOX_DIR", "").strip() or Path(tempfile.gettempdir()) / "jarvis-coder")
    r.mkdir(parents=True, exist_ok=True)
    return r


def _is_plain_file(p):
    """A regular file, not a link / junction / reparse point (a worker could plant one to reach outside)."""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    return not (getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def make(files):
    """A fresh sandbox holding exactly `files` ({name: bytes}). -> Path"""
    d = root() / f"w-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    (d / "site").mkdir(parents=True)
    for sub in ("home", "appdata", "localappdata", "tmp"):
        (d / sub).mkdir()
    for name, data in files.items():
        (d / "site" / name).write_bytes(data)
    return d


def collect(d, allowed_ext, max_bytes):
    """Read back the sandbox's site folder. -> ({name: bytes}, [problems]). Anything but flat, plain, allowed files is
    a problem (and nothing is taken)."""
    site = Path(d) / "site"
    out, problems = {}, []
    for p in site.iterdir():
        if not _is_plain_file(p):
            problems.append(f"'{p.name}' isn't a plain file (folder / link): not allowed")
            continue
        if p.suffix.lower() not in allowed_ext or p.name.startswith("."):
            problems.append(f"'{p.name}': that kind of file isn't allowed in a demo site")
            continue
        if p.stat().st_size > max_bytes:
            problems.append(f"'{p.name}' is too big")
            continue
        out[p.name] = p.read_bytes()
    return out, problems


def remove(d):
    try:
        shutil.rmtree(d, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def env(sandbox, api_key=""):
    e = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    sysroot = os.environ.get("SYSTEMROOT", r"C:\Windows")
    tool_dirs = []
    exe = shutil.which(config.CODER_CLI)
    if exe:
        tool_dirs.append(str(Path(exe).parent))
    node = shutil.which("node")
    if node:
        tool_dirs.append(str(Path(node).parent))
    e["PATH"] = os.pathsep.join(dict.fromkeys(tool_dirs + [str(Path(sysroot) / "System32"), sysroot]))
    home = str(Path(sandbox) / "home")
    e.update(HOME=home, USERPROFILE=home, APPDATA=str(Path(sandbox) / "appdata"),
             LOCALAPPDATA=str(Path(sandbox) / "localappdata"), TEMP=str(Path(sandbox) / "tmp"),
             TMP=str(Path(sandbox) / "tmp"), DISABLE_AUTOUPDATER="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
             DISABLE_TELEMETRY="1")
    if api_key:
        e["ANTHROPIC_API_KEY"] = api_key
    return e


# ---------------------------------------------------------------- Windows Job Object
class _IO(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]


class _Basic(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class _Extended(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _UI(ctypes.Structure):
    _fields_ = [("UIRestrictionsClass", ctypes.c_uint32)]


LIMIT_ACTIVE_PROCESS, LIMIT_JOB_MEMORY = 0x8, 0x200
LIMIT_DIE_ON_UNHANDLED_EXCEPTION, LIMIT_KILL_ON_JOB_CLOSE = 0x400, 0x2000
UI_ALL = 0x1 | 0x2 | 0x4 | 0x8 | 0x10 | 0x20 | 0x40 | 0x80  # handles, clipboard r/w, system params, display, atoms, desktop, exit
CREATE_SUSPENDED, CREATE_NO_WINDOW, CREATE_NEW_PROCESS_GROUP = 0x4, 0x08000000, 0x200


class Job:
    """A Job Object with the limits above. Windows only; raises SandboxError if it can't be set up."""

    def __init__(self):
        if sys.platform != "win32":
            raise SandboxError("the coding-worker sandbox needs Windows Job Objects")
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.k32 = k32
        self.h = k32.CreateJobObjectW(None, None)
        if not self.h:
            raise SandboxError(f"CreateJobObject failed ({ctypes.get_last_error()})")
        info = _Extended()
        info.BasicLimitInformation.LimitFlags = (LIMIT_ACTIVE_PROCESS | LIMIT_JOB_MEMORY | LIMIT_DIE_ON_UNHANDLED_EXCEPTION
                                                 | LIMIT_KILL_ON_JOB_CLOSE)
        info.BasicLimitInformation.ActiveProcessLimit = JOB_MAX_PROCESSES
        info.JobMemoryLimit = JOB_MEMORY_MB * 1024 * 1024
        if not k32.SetInformationJobObject(self.h, 9, ctypes.byref(info), ctypes.sizeof(info)):  # (9: extended limits)
            self.close()
            raise SandboxError(f"job limits couldn't be set ({ctypes.get_last_error()})")
        ui = _UI(UI_ALL)
        if not k32.SetInformationJobObject(self.h, 4, ctypes.byref(ui), ctypes.sizeof(ui)):  # (4: UI restrictions)
            self.close()
            raise SandboxError(f"job UI restrictions couldn't be set ({ctypes.get_last_error()})")

    def assign(self, process_handle):
        if not self.k32.AssignProcessToJobObject(self.h, ctypes.c_void_p(int(process_handle))):
            raise SandboxError(f"the worker couldn't be put in its job ({ctypes.get_last_error()})")

    def kill(self):
        if self.h:
            self.k32.TerminateJobObject(self.h, 1)

    def close(self):
        if self.h:
            self.k32.CloseHandle(self.h)  # (KILL_ON_JOB_CLOSE: anything still running in it ends here)
            self.h = None


def _resume(process_handle):
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
    status = ntdll.NtResumeProcess(ctypes.c_void_p(int(process_handle)))
    if status != 0:
        raise SandboxError(f"the worker couldn't be started (NtResumeProcess {status:#x})")


def run(cmd, sandbox, env_vars, timeout_s):
    """Run `cmd` inside the job, in sandbox/site, cancellable (runctx) and timed. -> (returncode, stdout, stderr)"""
    config.real_desktop("running the coding worker")
    runctx.check()
    job = Job()
    try:
        p = subprocess.Popen(cmd, cwd=str(Path(sandbox) / "site"), env=env_vars, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                             errors="replace", creationflags=CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
    except OSError as e:
        job.close()
        raise SandboxError(f"the worker couldn't be started ({e.__class__.__name__})")
    try:
        handle = p._handle  # (CPython's process handle on Windows)
        try:
            job.assign(handle)
        except SandboxError:
            p.kill()
            raise
        _resume(handle)
        deadline = time.time() + timeout_s
        out, err = "", ""
        while True:
            try:
                out, err = p.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if time.time() > deadline:
                    job.kill()
                    p.communicate(timeout=10)
                    raise TimeoutError(f"the coding worker took longer than {timeout_s}s and was stopped")
                if runctx.cancelled():
                    job.kill()
                    p.communicate(timeout=10)
                    raise runctx.Cancelled("the coding worker was stopped")
        return p.returncode, out or "", err or ""
    finally:
        job.close()
