"""Looking at the PC itself: what's listening on which port, which processes are running, how loaded the machine is.
Read-only: these only observe (they never start, stop or change anything), so "is my backend running?" or "why is my
game lagging?" can be answered by looking instead of guessing.
"""

import re
import subprocess
import time

from room_agent.abilities._kit import NO_ARGS, params, tool
from room_agent.actions.core import Group, register_group

register_group(Group(
    "system", re.compile(r"\b(running|port|server|backend|frontend|localhost|api|process(es)?|cpu|ram|memory usage|lag\w*|"
                         r"slow|hot|temperature|gpu|fans?|freez\w*|crash\w*|python|node|docker|database|program)\b", re.I),
    title="checking what's running on the PC", summary="listening ports and the process behind each, running "
    "processes, CPU / memory / GPU load (read-only: it never starts or stops anything)"))


def _psutil():
    import psutil

    return psutil


def _name(pid):
    try:
        return _psutil().Process(pid).name() if pid else "unknown process"
    except Exception:
        return "a process I can't see"


def inspect_ports(args):
    psutil = _psutil()
    want = args.get("port")
    try:
        conns = psutil.net_connections(kind="tcp")
    except Exception as e:
        return f"FAILED: couldn't read the network connections ({e.__class__.__name__})."
    listening = {}
    for c in conns:
        if c.status == psutil.CONN_LISTEN and c.laddr:
            listening.setdefault(c.laddr.port, c.pid)
    if want is not None:
        want = int(want)
        if want not in listening:
            return f"OK: nothing is listening on port {want}."
        return f"OK: port {want} is in use by {_name(listening[want])} (pid {listening[want]})."
    dev = sorted(p for p in listening if p in (3000, 3001, 4200, 5000, 5173, 5432, 6379, 8000, 8001, 8080, 8081, 8443, 8888,
                                               9000, 27017) or 3000 <= p < 10000)
    if not dev:
        return "OK: nothing is listening on the usual development ports (3000-9999)."
    return "OK: listening: " + "; ".join(f"{p} ({_name(listening[p])})" for p in dev[:15]) + "."


def find_processes(args):
    psutil = _psutil()
    want = str(args.get("name") or "").lower().removesuffix(".exe")
    hits = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        try:
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or []).lower()
        except (psutil.Error, TypeError):
            continue
        if want and (want in name or want in cmd):
            mb = (p.info["memory_info"].rss / 2 ** 20) if p.info["memory_info"] else 0
            hits.append((want not in name, f"{p.info['name']} (pid {p.info['pid']}, {mb:.0f} MB)"))
    hits = [h for _, h in sorted(hits)]  # (processes named like it first, then ones that only mention it)
    if not hits:
        return f"OK: no running process matches '{want}'."
    return f"OK: {len(hits)} running: " + "; ".join(hits[:8]) + ("..." if len(hits) > 8 else "") + "."


def system_load(args):
    psutil = _psutil()
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    procs = []
    for p in psutil.process_iter(["name", "cpu_percent"]):
        procs.append(p)
    time.sleep(0.4)  # (cpu_percent per process needs two looks)
    top = []
    for p in procs:
        try:
            top.append((p.cpu_percent(None) / max(psutil.cpu_count() or 1, 1), p.info["name"]))
        except psutil.Error:
            pass
    top = sorted((t for t in top if t[1] and t[1].lower() != "system idle process"), reverse=True)[:3]
    gpu = ""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip().splitlines()
        if out:
            u, used, total, temp = [x.strip() for x in out[0].split(",")]
            gpu = f"; GPU {u}% busy, {used}/{total} MB memory, {temp} C"
    except Exception:
        pass
    return (f"OK: CPU {cpu:.0f}% busy, memory {mem.percent:.0f}% used ({mem.available / 2 ** 30:.1f} GB free){gpu}; busiest: "
            + ", ".join(f"{n} {c:.0f}%" for c, n in top) + ".")


common = dict(group="system", changes_state=False, fresh_for=10)
tool("inspect_ports", "What's listening on network ports, and which process owns each (a dev server, a database...). Give "
     "a port to check just that one.", params({"port": {"type": "integer", "minimum": 1, "maximum": 65535}}),
     inspect_ports, **common)
tool("find_processes", "Running processes whose name or command line contains this ('python', 'node', 'aimchart').",
     params({"name": {"type": "string"}}, ["name"]), find_processes, **common)
tool("system_load", "How busy the PC is right now: CPU, memory, GPU (and its temperature) and the busiest programs.",
     NO_ARGS, system_load, **common)
