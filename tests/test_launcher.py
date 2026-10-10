"""Launcher decisions and isolated process control. These tests do not start or stop the assistant."""

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from launcher.model import (
    EXTERNAL,
    FAILED,
    RUNNING,
    UNKNOWN,
    adoption_ok,
    assess_port,
    close_action,
    command_matches,
    may_stop,
    start_order,
    stop_order,
    taskkill_args,
)
from launcher.platform import Child, Listener, LiveProcess, WindowsPlatform
from launcher.redact import redact
from launcher.shortcut import png_to_ico
from launcher.supervisor import Supervisor

PROJECT = Path(__file__).resolve().parents[1]


class Poll:
    def __init__(self, code=None):
        self._code = code
        self.returncode = code

    def poll(self):
        return self._code


class FakePlatform:
    def __init__(self):
        self.spawned = []
        self.stopped = []
        self.ports = {}
        self.processes = {}
        self.up = set()
        self.have_python = True
        self.have_node = True
        self.have_modules = True
        self.fail = set()
        self.seq = 100

    def python_exe(self, root):
        return root / ".venv" / "Scripts" / "python.exe" if self.have_python else None

    def node_exe(self):
        return "node.exe" if self.have_node else None

    def frontend_installed(self, root):
        return self.have_modules

    def control_port(self, root):
        return 8771

    def listeners(self):
        return dict(self.ports)

    def http_ok(self, url, timeout=2):
        return url in self.up

    def describe(self, pid):
        return self.processes.get(pid)

    def spawn(self, argv, cwd, env, log_path):
        script = argv[-1]
        self.spawned.append(list(argv))
        pid = self.seq
        self.seq += 1
        failed = any(script.lower().endswith(name) for name in self.fail)
        code = 3 if failed else None
        child = Child(pid, pid * 10, script, list(argv), Poll(code))
        if not failed:
            self.processes[pid] = LiveProcess(pid, child.create_time, " ".join(argv), Path(argv[0]).name)
            self.up.add(_url_for(script))
        return child

    def stop(self, record, graceful_s, launcher_pid):
        live = self.processes.get(int(record["pid"]))
        allowed, why = may_stop(
            record,
            live.command if live else None,
            live.create_time if live else None,
            live.pid if live else None,
            launcher_pid,
        )
        self.stopped.append(int(record["pid"]))
        if not allowed:
            return why
        self.processes.pop(int(record["pid"]), None)
        self.up.discard(_url_for(record["script"]))
        return "stopped"


def _url_for(script: str) -> str:
    folded = script.lower().replace("/", "\\")
    if folded.endswith("\\main.py"):
        return "http://127.0.0.1:8771/live"
    if folded.endswith("\\server.py"):
        return "http://127.0.0.1:8765/"
    return "http://127.0.0.1:3000/overview/"


def _supervisor(tmp: Path, platform=None, mode="classic") -> Supervisor:
    sup = Supervisor(tmp, platform or FakePlatform(), tmp / "settings.json", tmp / "state.json")
    sup.set_mode(mode)
    return sup


def test_orders_and_close_policy():
    assert start_order("classic") == ["backend", "dashboard"]
    assert start_order("next") == ["backend", "dashboard", "frontend"]
    assert stop_order(["backend", "frontend", "dashboard"]) == ["frontend", "dashboard", "backend"]
    assert close_action(True, True) == "hide"
    assert close_action(False, False) == "exit-leave-services"
    assert close_action(False, True) == "stop-owned-and-exit"


def test_taskkill_is_one_owned_pid():
    args = taskkill_args(4321, force=False)
    assert args == ["taskkill", "/PID", "4321", "/T"]
    assert "/IM" not in args
    assert "python.exe" not in args
    assert "node.exe" not in args
    forced = taskkill_args(4321, force=True)
    assert forced == ["taskkill", "/PID", "4321", "/T", "/F"]
    try:
        taskkill_args(0, force=False)
        raise AssertionError("pid 0 should be refused")
    except ValueError:
        pass


def test_refuses_unrelated_and_reused_pids():
    record = {"pid": 10, "create_time": 99, "script": r"C:\room-agent\main.py"}
    ok, _ = may_stop(record, r"C:\room-agent\main.py", 99, 10, launcher_pid=1)
    assert ok
    ok, why = may_stop(record, r"C:\Program Files\Claude\claude.exe", 99, 10, 1)
    assert not ok and "command" in why
    ok, why = may_stop(record, r"C:\room-agent\main.py", 100, 10, 1)
    assert not ok and "reused" in why
    ok, why = may_stop({"pid": os.getpid(), "create_time": 1, "script": "main.py"}, "main.py", 1, os.getpid(), os.getpid())
    assert not ok and "launcher" in why
    assert command_matches(r'"C:\room-agent\main.py"', r"C:\room-agent\main.py")
    assert adoption_ok(record, r"C:\room-agent\main.py", 99, 10)
    assert not adoption_ok(record, r"C:\room-agent\main.py", 100, 10)


def test_port_assessment_does_not_imply_a_kill():
    root = r"C:\room-agent"
    assert assess_port(listening=False, healthy=False, command="", root=root, role="backend", owned_alive=False) == "free"
    assert assess_port(
        listening=True, healthy=True, command=root + r"\main.py", root=root, role="backend", owned_alive=False,
    ) == EXTERNAL
    relative = rf'"{root}\.venv\Scripts\python.exe" main.py'
    assert assess_port(
        listening=True, healthy=True, command=relative, root=root, role="backend", owned_alive=False,
    ) == EXTERNAL
    assert assess_port(
        listening=True, healthy=True, command="node  scripts/dev.mjs", root=root, role="frontend",
        owned_alive=False, cwd=root + r"\frontend",
    ) == EXTERNAL
    assert assess_port(
        listening=True, healthy=True, command="node  scripts/dev.mjs", root=root, role="frontend",
        owned_alive=False, cwd=r"C:\other\frontend",
    ) == UNKNOWN
    assert assess_port(
        listening=True, healthy=False, command=r"C:\Tools\claude.exe", root=root, role="backend", owned_alive=False,
    ) == UNKNOWN


def test_redacts_secrets_and_keeps_ordinary_words():
    text = "using sk-testFAKEvalue1234 and token=abcDEF123 secret: hunter2 Authorization: Bearer abc.def.ghi"
    cleaned = redact(text)
    assert "sk-testFAKEvalue1234" not in cleaned
    assert "hunter2" not in cleaned
    assert "abcDEF123" not in cleaned
    assert "abc.def.ghi" not in cleaned
    assert "token count is 12" == redact("token count is 12")


def test_first_start_and_repeat_start(tmp_path):
    platform = FakePlatform()
    sup = _supervisor(tmp_path, platform)
    assert "ready" in sup.start_all()
    assert [Path(item[-1]).name for item in platform.spawned] == ["main.py", "server.py"]
    assert sup.status["backend"]["state"] == RUNNING
    assert sup.status["dashboard"]["state"] == RUNNING
    again = len(platform.spawned)
    assert "not started again" in sup.start_all()
    assert len(platform.spawned) == again


def test_restart_all_and_backend_only(tmp_path):
    platform = FakePlatform()
    sup = _supervisor(tmp_path, platform)
    sup.start_all()
    sup.restart_backend()
    assert len(platform.stopped) == 1
    assert Path(platform.spawned[-1][-1]).name == "main.py"
    assert sum(Path(item[-1]).name == "server.py" for item in platform.spawned) == 1
    before = len(platform.spawned)
    sup.restart_all()
    assert Path(platform.spawned[before][-1]).name == "main.py"
    assert Path(platform.spawned[before + 1][-1]).name == "server.py"
    assert sup.status["backend"]["state"] == RUNNING


def test_frontend_restart_leaves_backend(tmp_path):
    platform = FakePlatform()
    sup = _supervisor(tmp_path, platform, mode="next")
    sup.start_all()
    assert [Path(item[-1]).name for item in platform.spawned] == ["main.py", "server.py", "dev.mjs"]
    backend_pid = next(iter(pid for pid, live in platform.processes.items() if live.command.endswith("main.py")))
    sup.restart_frontend()
    assert backend_pid in platform.processes
    assert sup.status["frontend"]["state"] == RUNNING
    assert sup.status["backend"]["state"] == RUNNING
    assert Path(platform.spawned[-1][-1]).name == "dev.mjs"


def test_existing_service_and_port_conflict_are_not_killed(tmp_path):
    platform = FakePlatform()
    root = tmp_path
    platform.ports[8771] = Listener(50, "python.exe", str(root / "main.py"))
    platform.up.add("http://127.0.0.1:8771/live")
    platform.ports[8765] = Listener(9, "claude.exe", r"C:\Tools\claude.exe")
    sup = _supervisor(root, platform)
    message = sup.start_all()
    assert platform.stopped == []
    assert platform.spawned == []
    assert sup.status["backend"]["state"] == EXTERNAL
    assert sup.status["dashboard"]["state"] == UNKNOWN
    assert "Later services were not started." not in message
    assert "was not started" in message


def test_backend_failure_does_not_start_dashboard(tmp_path):
    platform = FakePlatform()
    platform.fail.add("main.py")
    sup = _supervisor(tmp_path, platform)
    message = sup.start_all()
    assert sup.status["backend"]["state"] == FAILED
    assert "code 3" in sup.status["backend"]["detail"]
    assert [Path(item[-1]).name for item in platform.spawned] == ["main.py"]
    assert "Later services were not started." in message


def test_frontend_failure_leaves_backend(tmp_path):
    platform = FakePlatform()
    platform.fail.add("dev.mjs")
    sup = _supervisor(tmp_path, platform, mode="next")
    sup.start_all()
    assert sup.status["backend"]["state"] == RUNNING
    assert sup.status["dashboard"]["state"] == RUNNING
    assert sup.status["frontend"]["state"] == FAILED


def test_missing_environment_does_not_spawn_or_install(tmp_path):
    platform = FakePlatform()
    platform.have_python = False
    sup = _supervisor(tmp_path, platform)
    message = sup.start_all()
    assert platform.spawned == []
    assert "setup.ps1" in message
    assert sup.status["backend"]["state"] == FAILED


def test_overlapping_requests_are_refused(tmp_path):
    sup = _supervisor(tmp_path)
    assert sup._gate.acquire(blocking=False)
    seen = []

    def other():
        seen.append(sup.start_all())

    thread = threading.Thread(target=other)
    thread.start()
    thread.join(2)
    sup._gate.release()
    assert seen and "already running" in seen[0]


def test_stop_all_leaves_external_processes(tmp_path):
    platform = FakePlatform()
    sup = _supervisor(tmp_path, platform)
    sup.start_all()
    platform.ports[3000] = Listener(77, "node.exe", r"C:\other\scripts\dev.mjs")
    sup.refresh()
    assert sup.status["frontend"]["state"] == UNKNOWN
    sup.stop_all()
    assert 77 not in platform.stopped
    assert sup.status["backend"]["state"] != RUNNING
    assert "Left alone" in sup.message


def test_ico_wraps_the_existing_logo(tmp_path):
    logo = PROJECT / "UI" / "brand" / "zend-favicon.png"
    assert logo.is_file()
    dest = tmp_path / "zend.ico"
    png_to_ico(logo, dest)
    data = dest.read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00"
    assert b"PNG" in data[:40] or data[22:26] == b"\x89PNG"


def test_windows_stop_refuses_this_process():
    if os.name != "nt":
        return
    platform = WindowsPlatform()
    record = {"pid": os.getpid(), "create_time": 1, "script": r"C:\not\main.py"}
    result = platform.stop(record, 1, os.getpid())
    assert result != "stopped"
    assert "launcher" in result or "reused" in result or "command" in result or "gone" in result


def test_graceful_stop_and_source_reload(tmp_path):
    if os.name != "nt":
        return
    platform = WindowsPlatform()
    flag = tmp_path / "stopped.txt"
    version = tmp_path / "version.txt"
    version.write_text("version-1", encoding="utf-8")
    script = tmp_path / "stand_in.py"
    script.write_text(
        "import pathlib, sys, time\n"
        "version, flag = sys.argv[1], sys.argv[2]\n"
        "print(pathlib.Path(version).read_text(encoding='utf-8'), flush=True)\n"
        "try:\n"
        "    time.sleep(60)\n"
        "except KeyboardInterrupt:\n"
        "    pathlib.Path(flag).write_text('graceful', encoding='utf-8')\n",
        encoding="utf-8",
    )
    log = tmp_path / "stand-in.log"
    python = PROJECT / ".venv" / "Scripts" / "python.exe"
    child = platform.spawn([str(python), "-u", str(script), str(version), str(flag)], tmp_path, os.environ.copy(), log)
    deadline = time.time() + 8
    while time.time() < deadline and "version-1" not in log.read_text(encoding="utf-8", errors="replace"):
        time.sleep(0.1)
    assert "version-1" in log.read_text(encoding="utf-8", errors="replace")
    version.write_text("version-2", encoding="utf-8")
    record = {"pid": child.pid, "create_time": child.create_time, "script": str(script)}
    result = platform.stop(record, 8, os.getpid())
    assert result == "stopped"
    assert flag.read_text(encoding="utf-8") == "graceful"
    child = platform.spawn([str(python), "-u", str(script), str(version), str(flag)], tmp_path, os.environ.copy(), log)
    deadline = time.time() + 8
    text = ""
    while time.time() < deadline:
        text = log.read_text(encoding="utf-8", errors="replace")
        if "version-2" in text:
            break
        time.sleep(0.1)
    record = {"pid": child.pid, "create_time": child.create_time, "script": str(script)}
    platform.stop(record, 8, os.getpid())
    assert "version-2" in text


def test_live_assistant_is_only_observed(tmp_path):
    """Read-only probe of the real checkout. It must not spawn or stop anything."""
    if os.name != "nt":
        return
    sup = Supervisor(PROJECT, WindowsPlatform(), tmp_path / "settings.json", tmp_path / "state.json")
    before = _listener_pids()
    sup.refresh()
    after = _listener_pids()
    assert before == after
    assert sup._owned == {}
    for name in ("backend", "dashboard", "frontend"):
        assert sup.status[name]["state"] in (EXTERNAL, UNKNOWN, "stopped", RUNNING)
        if sup.status[name]["state"] in (EXTERNAL, UNKNOWN):
            assert "not stop" in sup.status[name]["detail"].lower() or "did not stop" in sup.status[name]["detail"].lower()


def test_windows_cwd_reads_this_process():
    if os.name != "nt":
        return
    from launcher.platform import _process_cwd

    assert Path(_process_cwd(os.getpid())).resolve() == Path.cwd().resolve()


def test_windows_command_line_reads_this_process():
    if os.name != "nt":
        return
    from launcher.platform import _command_line

    text = _command_line(os.getpid()).lower()
    assert "python" in text
    assert "sk-" not in text


def test_tray_icon_registers_and_removes():
    if os.name != "nt":
        return
    from launcher.tray import TrayIcon

    icon = PROJECT / "launcher" / "zendagent.ico"
    if not icon.is_file():
        png_to_ico(PROJECT / "UI" / "brand" / "zend-favicon.png", icon)
    tray = TrayIcon(icon, lambda _action: None)
    assert tray.start()
    tray.stop()


def _listener_pids():
    return {port: item.pid for port, item in WindowsPlatform().listeners().items() if port in (8765, 8770, 8771, 3000)}


if __name__ == "__main__":
    import traceback
    failed = 0
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        try:
            hints = test.__code__.co_varnames[:test.__code__.co_argcount]
            if hints == ("tmp_path",):
                import tempfile
                with tempfile.TemporaryDirectory() as folder:
                    test(Path(folder))
            else:
                test()
            print("ok", test.__name__)
        except Exception:
            failed += 1
            print("FAIL", test.__name__)
            traceback.print_exc()
    if failed:
        raise SystemExit(f"{failed} failed")
    print(f"{len(tests)} passed")
