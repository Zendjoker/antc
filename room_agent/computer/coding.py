"""General coding and testing on the user's own projects, sandboxed and permission-gated.

    run_tests(project)        copy the project into a throwaway sandbox folder and run an ALLOWLISTED test command there
                              (python -m unittest / python -m pytest - nothing else, no shell). Free; the live project is
                              never touched.
    propose_fix(project, ...) a coding model proposes changed files (FIXER; CODING_FIXER=anthropic is paid and OFF by
                              default). The proposal is written into a sandbox copy and the tests are run there; at most
                              MAX_ATTEMPTS proposals, each told the remaining failures. Test files are protected (a fix that
                              edits the tests to pass isn't a fix) unless they asked to change the tests. Only a fix whose
                              tests PASS in the sandbox is kept, as a pending fix - nothing live changes.
    apply_fix(project)        (always asks first: SENSITIVE) the pending fix goes live only if the live files are still exactly what
                              the fix was made from (no hand edits since); the files it changes are backed up first; the
                              tests are run again on a fresh copy of the live project; if they fail, the backup is put back.

Boundaries: a project is a folder inside their own folders (computer/files.allowed, or CODING_ROOTS), never Jarvis's own
code, at most MAX_FILES files / MAX_BYTES. Processes run in a Windows Job Object (missions/sandbox.Job: process count,
memory, UI restrictions, killed with the job), with a minimal environment (no API keys, HOME / TEMP inside the sandbox),
a timeout, and cancellation. Not done: network isolation of the test process (Windows offers none per process without
admin rights) - test code runs with the user's network access, as it would if they ran it themselves.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from room_agent import config

log = logging.getLogger("room-agent")
MAX_FILES = 2000
MAX_BYTES = 50 * 1024 * 1024
MAX_PROMPT_BYTES = 200_000
MAX_ATTEMPTS = 2
TEST_TIMEOUT_S = 120
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache",
             ".tox", "dist", "build", ".idea", ".vscode"}
TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".md", ".txt", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".html",
            ".css", ".sql", ".sh", ".ps1", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".cs"}
TEST_FILE = re.compile(r"(^|/)(test_[^/]*|[^/]*_test\.\w+|tests?/.*|conftest\.py)$", re.I)
FIXER = None   # (project, instruction, files, failures) -> {relative path: new content}; tests set a fake; None = configured
_pending = {}  # project path -> {"files": {rel: text}, "base": {rel: sha256}, "summary": str, "at": time}


class CodingError(Exception):
    pass


def resolve(project):
    """A project folder they may let Jarvis work on. -> Path, or raises CodingError."""
    from room_agent.computer import files

    raw = os.path.expanduser(str(project or "").strip())
    if not raw:
        raise CodingError("which project folder?")
    p = Path(raw)
    if not p.is_absolute():
        p = files.HOME / raw
    try:
        p = p.resolve()
    except OSError:
        raise CodingError("that path isn't valid")
    roots = [Path(r).resolve() for r in config.CODING_ROOTS]
    inside_root = any(p == r or r in p.parents for r in roots)
    ok, why = files.allowed(p / "x.txt")
    if not (ok or inside_root):
        raise CodingError(f"not a folder Jarvis may work on ({why})")
    here = Path(config.HERE).resolve()
    if p == here or here in p.parents or p in here.parents:
        raise CodingError("Jarvis doesn't edit or run its own code")
    if not p.is_dir():
        raise CodingError(f"there's no project folder {p.name}")
    return p


def _plain(path):
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and not (getattr(st, "st_file_attributes", 0) & 0x400)


def _files(root):
    """{relative path (posix): absolute Path} of the project's plain files, within the limits."""
    out, total = {}, 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            if not _plain(p):
                continue
            total += p.stat().st_size
            out[p.relative_to(root).as_posix()] = p
            if len(out) > MAX_FILES or total > MAX_BYTES:
                raise CodingError(f"the project is too big to copy into a sandbox (over {MAX_FILES} files or "
                                  f"{MAX_BYTES // (1024 * 1024)} MB)")
    return out


def _sandbox():
    d = Path(os.getenv("CODER_SANDBOX_DIR", "").strip() or Path(tempfile.gettempdir()) / "jarvis-coder") / \
        f"code-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    for sub in ("work", "home", "tmp"):
        (d / sub).mkdir(parents=True)
    return d


def copy_to_sandbox(root, overrides=None):
    sb = _sandbox()
    for rel, src in _files(root).items():
        dest = sb / "work" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    for rel, text in (overrides or {}).items():
        dest = sb / "work" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    return sb


def _env(sb):
    keep = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
            "OS", "LANG", "LC_ALL")
    e = {k: os.environ[k] for k in keep if k in os.environ}
    e.update(HOME=str(sb / "home"), USERPROFILE=str(sb / "home"), TEMP=str(sb / "tmp"), TMP=str(sb / "tmp"),
             PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PATH=str(Path(sys.executable).parent))
    return e


def test_command(work):
    """The allowlisted command for this project: pytest if it uses pytest and pytest is installed, else unittest."""
    uses_pytest = any((work / n).exists() for n in ("pytest.ini", "conftest.py")) or (
        (work / "pyproject.toml").exists() and "pytest" in (work / "pyproject.toml").read_text(encoding="utf-8", errors="ignore"))
    if uses_pytest:
        try:
            import importlib.util

            if importlib.util.find_spec("pytest"):
                return [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]
        except Exception:  # noqa: BLE001
            pass
    return [sys.executable, "-m", "unittest", "discover", "-q"]


def _run(cmd, sb, timeout_s=None):
    """Run an allowlisted command in the sandbox copy, inside a Job Object. -> (returncode, output)."""
    from room_agent import cancel
    from room_agent.missions import sandbox

    if cmd[0] != sys.executable or cmd[1:3] not in (["-m", "unittest"], ["-m", "pytest"]):
        raise CodingError("only the allowlisted test commands run here")
    sandbox.check_argv(cmd)
    if sys.platform != "win32" and not config.CODING_ALLOW_UNSANDBOXED:
        raise CodingError("there's no process sandbox on this system (it needs Windows), so a project's code isn't run "
                          "(set CODING_ALLOW_UNSANDBOXED=1 to allow it anyway)")
    job = sandbox.Job() if sys.platform == "win32" else None
    flags = (sandbox.CREATE_SUSPENDED | sandbox.CREATE_NO_WINDOW | sandbox.CREATE_NEW_PROCESS_GROUP) if job else 0
    try:
        p = subprocess.Popen(cmd, cwd=str(sb / "work"), env=_env(sb), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=flags)
    except OSError as e:
        if job:
            job.close()
        raise CodingError(f"the tests couldn't be started ({e.__class__.__name__})")
    try:
        if job:
            try:
                job.assign(p._handle)
            except sandbox.SandboxError:
                p.kill()
                raise CodingError("the test process couldn't be put in its sandbox job, so it wasn't run")
            sandbox._resume(p._handle)
        deadline = time.time() + (timeout_s or TEST_TIMEOUT_S)
        while True:
            try:
                out, _ = p.communicate(timeout=0.5)
                return p.returncode, out or ""
            except subprocess.TimeoutExpired:
                if time.time() > deadline or cancel.requested():
                    (job.kill() if job else p.kill())
                    p.communicate(timeout=10)
                    raise CodingError("the tests were stopped" + (" (timed out)" if time.time() > deadline else ""))
    finally:
        if job:
            job.close()


def _summary(code, out):
    """Parse unittest / pytest output into (passed: bool, counts line, failing test names)."""
    failing = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\S+)", out, re.M) + re.findall(r"^FAILED ([^\s(]\S*)", out, re.M)))
    ran = re.search(r"Ran (\d+) tests?", out) or re.search(r"(\d+) (?:passed|failed)", out)
    return code == 0, (f"{ran.group(1)} test(s) ran" if ran else "tests ran"), failing


def run_tests(project, overrides=None):
    """-> dict(passed, line, failing, output tail). The live project is only read (copied)."""
    root = resolve(project)
    sb = copy_to_sandbox(root, overrides)
    try:
        code, out = _run(test_command(sb / "work"), sb)
    finally:
        shutil.rmtree(sb, ignore_errors=True)
    passed, line, failing = _summary(code, out)
    if not re.search(r"Ran [1-9]\d* test|\d+ passed|\d+ failed", out):
        passed = False
        line = "no tests were found"
    return {"passed": passed, "line": line, "failing": failing, "tail": out[-1500:], "project": str(root)}


def _sha(text):
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _readable(root):
    out, size = {}, 0
    for rel, p in _files(root).items():
        if p.suffix.lower() in TEXT_EXT and p.stat().st_size < 100_000:
            text = p.read_text(encoding="utf-8", errors="replace")
            size += len(text)
            if size > MAX_PROMPT_BYTES:
                break
            out[rel] = text
    return out


def propose_fix(project, instruction, allow_tests=False, fixer=None):
    """-> dict(ok, message, changed, attempts). On success a pending fix is kept for apply_fix; nothing live changes."""
    root = resolve(project)
    fixer = fixer or FIXER or _configured_fixer()
    if fixer is None:
        return {"ok": False, "unavailable": True, "message": "no coding model is set up (CODING_FIXER); paid code fixes "
                                                             "are off by default"}
    first = run_tests(root)
    if first["passed"] and not instruction:
        return {"ok": True, "message": f"the tests already pass ({first['line']}); nothing to fix", "changed": [],
                "attempts": 0}
    files = _readable(root)
    base = {rel: _sha(text) for rel, text in files.items()}
    changes, failures, attempts = {}, first["tail"], 0
    for attempts in range(1, MAX_ATTEMPTS + 1):
        proposal = fixer(str(root), instruction, {**files, **changes}, failures) or {}
        bad = [rel for rel in proposal if rel not in files or (TEST_FILE.search(rel) and not allow_tests)]
        if bad:
            return {"ok": False, "message": "the proposed fix touched files it may not (" + ", ".join(bad[:3])
                                            + "): test files and files outside the project are protected; nothing kept",
                    "attempts": attempts}
        changes.update({rel: str(text) for rel, text in proposal.items()})
        result = run_tests(root, changes)
        if result["passed"]:
            changed = sorted(rel for rel in changes if changes[rel] != files.get(rel))
            _pending[str(root)] = {"files": {r: changes[r] for r in changed}, "base": {r: base[r] for r in changed},
                                   "summary": f"{len(changed)} file(s): {', '.join(changed)}", "at": time.time(),
                                   "line": result["line"]}
            return {"ok": True, "message": f"a fix that makes the tests pass in a sandbox copy ({result['line']}): "
                                           f"{', '.join(changed)} - NOT applied yet", "changed": changed, "attempts": attempts}
        failures = result["tail"]
    return {"ok": False, "message": f"no fix made the tests pass after {attempts} attempt(s); still failing: "
                                    + (", ".join(result["failing"][:4]) or result["line"]), "attempts": attempts}


def pending(project):
    try:
        return _pending.get(str(resolve(project)))
    except CodingError:
        return None


def apply_fix(project):
    """-> dict(ok, message, backup). Only a verified pending fix, only over unchanged live files; verified again live."""
    root = resolve(project)
    fix = _pending.get(str(root))
    if fix is None:
        return {"ok": False, "message": "there's no verified fix waiting for this project (run fix_code first)"}
    for rel, h in fix["base"].items():
        live = root / rel
        if not live.exists() or _sha(live.read_text(encoding="utf-8", errors="replace")) != h:
            return {"ok": False, "message": f"{rel} changed since the fix was made (hand edits?); nothing applied - make "
                                            "the fix again"}
    backup = Path(os.getenv("CODER_SANDBOX_DIR", "").strip() or Path(tempfile.gettempdir()) / "jarvis-coder") / \
        "backups" / f"{root.name}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    backup.mkdir(parents=True)
    for rel in fix["files"]:
        dest = backup / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, dest)
    (backup / "jarvis-backup.json").write_text(json.dumps({"project": str(root), "files": sorted(fix["files"])}),
                                               encoding="utf-8")
    for rel, text in fix["files"].items():
        tmp = (root / rel).with_name((root / rel).name + ".jarvis-tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(root / rel)
    check = run_tests(root)
    if not check["passed"]:
        restore(root, backup)
        return {"ok": False, "message": f"applied, but the tests failed on the live copy ({check['line']}); the backup was "
                                        "put back, so nothing changed", "backup": str(backup)}
    _pending.pop(str(root), None)
    return {"ok": True, "message": f"applied {fix['summary']}; tests pass on the live project ({check['line']}); backup "
                                   f"of the old files: {backup}", "backup": str(backup), "files": sorted(fix["files"])}


def restore(root, backup):
    meta = json.loads((Path(backup) / "jarvis-backup.json").read_text(encoding="utf-8"))
    for rel in meta["files"]:
        shutil.copy2(Path(backup) / rel, Path(root) / rel)
    return meta["files"]


def _configured_fixer():
    """The paid fixer (CODING_FIXER=anthropic): off by default. Budget-checked before every call."""
    if config.CODING_FIXER != "anthropic" or not config.ANTHROPIC_KEY:
        return None

    def fix(project, instruction, files, failures):
        from room_agent.llm.budget import budget
        from room_agent.llm.client import client

        if budget.exceeded() or budget.total() + config.CODING_MAX_USD > budget.limit:
            raise CodingError("today's model budget doesn't allow a code fix right now")
        listing = "\n\n".join(f"### {rel}\n{text}" for rel, text in files.items())
        prompt = (f"Task: {instruction or 'make the failing tests pass'}.\nFailing test output:\n{failures[-3000:]}\n\n"
                  f"Project files:\n{listing}\n\nReturn ONLY JSON: {{\"files\": {{\"relative/path\": \"full new content\"}}}} "
                  "with just the files you change. Never change test files.")
        msg = client().with_options(max_retries=0, timeout=120).messages.create(
            model=config.MISSION_STRONG_MODEL, max_tokens=4000, messages=[{"role": "user", "content": prompt}])
        budget.record("claude", config.MISSION_STRONG_MODEL, fresh_in=msg.usage.input_tokens, out=msg.usage.output_tokens)
        text = "".join(getattr(b, "text", "") for b in msg.content)
        m = re.search(r"\{.*\}", text, re.S)
        return (json.loads(m.group(0)).get("files") or {}) if m else {}

    return fix
