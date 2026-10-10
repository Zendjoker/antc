"""Decisions the launcher can make without touching a live process."""

from __future__ import annotations

STOPPED = "stopped"
STARTING = "starting"
RUNNING = "running"
STOPPING = "stopping"
FAILED = "failed"
EXTERNAL = "external"  # a healthy ZendAgent process this launcher did not start
UNKNOWN = "unknown"    # a port is taken by something this launcher will not touch

LABELS = {
    STOPPED: "Stopped",
    STARTING: "Starting",
    RUNNING: "Running",
    STOPPING: "Stopping",
    FAILED: "Failed",
    EXTERNAL: "Externally managed",
    UNKNOWN: "Unknown",
}

# Image-wide kills are never constructed. Callers pass a single pid.
_REFUSED_IMAGES = ("python.exe", "pythonw.exe", "node.exe", "claude.exe", "cursor.exe")


def start_order(mode: str) -> list[str]:
    names = ["backend", "dashboard"]
    if mode == "next":
        names.append("frontend")
    return names


def stop_order(names: set[str] | list[str]) -> list[str]:
    wanted = set(names)
    return [name for name in ("frontend", "dashboard", "backend") if name in wanted]


def close_action(close_to_tray: bool, stop_on_exit: bool) -> str:
    """What the window close button does. Exit never stops services unless stop_on_exit is set."""
    if close_to_tray:
        return "hide"
    if stop_on_exit:
        return "stop-owned-and-exit"
    return "exit-leave-services"


def taskkill_args(pid: int, *, force: bool) -> list[str]:
    """Tree-kill one confirmed pid. Refuses an image-wide kill."""
    pid = int(pid)
    if pid <= 0:
        raise ValueError("pid must be a real process id")
    args = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        args.append("/F")
    if "/IM" in args or any(image in args for image in _REFUSED_IMAGES):
        raise RuntimeError("refusing an image-wide kill")
    return args


def command_matches(command: str, script: str) -> bool:
    """True when the live command line is the absolute script this launcher started."""
    if not command or not script:
        return False
    return script.lower().replace("/", "\\") in command.lower().replace("/", "\\")


def looks_like_ours(command: str, root: str, role: str) -> bool:
    """A process that belongs to this checkout, whether or not the launcher started it."""
    if not command:
        return False
    folded = command.lower().replace("/", "\\")
    root_f = str(root).lower().replace("/", "\\").rstrip("\\")
    if role == "backend":
        return f"{root_f}\\main.py" in folded
    if role == "dashboard":
        return f"{root_f}\\ui\\server.py" in folded
    if role == "frontend":
        return f"{root_f}\\frontend\\scripts\\dev.mjs" in folded
    return False


def may_stop(record: dict, live_command: str | None, live_created: int | None, live_pid: int | None,
             launcher_pid: int) -> tuple[bool, str]:
    """Stop only a process this launcher started, still the same process, still the same script."""
    pid = int(record.get("pid") or 0)
    if pid <= 0:
        return False, "no owned pid"
    if pid == int(launcher_pid):
        return False, "refusing to stop the launcher"
    if live_pid is None:
        return False, "process is already gone"
    if int(live_pid) != pid:
        return False, "pid does not match"
    created = int(record.get("create_time") or 0)
    if not created or live_created is None or int(live_created) != created:
        return False, "pid was reused or the process changed"
    script = str(record.get("script") or "")
    if not command_matches(live_command or "", script):
        return False, "command line is not the script this launcher started"
    lowered = (live_command or "").lower()
    if any(name in lowered for name in ("claude", "cursor", "copilot")) and not command_matches(live_command or "", script):
        return False, "unrelated developer process"
    return True, "owned"


def assess_port(*, listening: bool, healthy: bool, command: str, root: str, role: str,
                 owned_alive: bool) -> str:
    """What a port means before the launcher decides to start anything."""
    if owned_alive:
        return RUNNING if healthy else STARTING
    if not listening and not healthy:
        return "free"
    if healthy and looks_like_ours(command, root, role):
        return EXTERNAL
    if listening or healthy:
        return UNKNOWN
    return "free"


def adoption_ok(record: dict, live_command: str | None, live_created: int | None, live_pid: int | None) -> bool:
    """After the launcher itself restarts, keep ownership of children that are still the same process."""
    if not record or live_pid is None or live_created is None:
        return False
    if int(live_pid) != int(record.get("pid") or 0):
        return False
    if int(live_created) != int(record.get("create_time") or 0):
        return False
    return command_matches(live_command or "", str(record.get("script") or ""))
