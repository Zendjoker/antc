"""A simulated PC for cognition tests (offline and live-model): apps, three monitors, volume, listening ports, and a dev
server + a sensitive stop_process registered exactly like any future capability would be. Import it after setup_env()."""

import re
import time

from room_agent import runtime as rt
from room_agent.actions import core
from room_agent.actions.core import Capability, Group, Risk, register, register_group
from room_agent.tools import apps, media
from room_agent.tools import window_control as wc

# ---------------------------------------------------------------- a simulated PC
CACHE = {"built": time.time(), "exes": {}, "entries": [
    {"name": "Spotify", "id": "Spotify", "exe": "", "link": "", "procs": []},
    {"name": "Google Chrome", "id": "Chrome", "exe": "", "link": "", "procs": []},
    {"name": "Visual Studio Code", "id": "Code", "exe": "", "link": "", "procs": []}]}
apps.apps = lambda refresh=False: CACHE
RUNNING, OPENED = set(), []
apps.processes = lambda entry: [1] if entry["name"] in RUNNING else []


def fake_open(name):
    entry, _ = apps.find(rt.last_active_app["name"] if name.lower() in apps.PRONOUNS and rt.last_active_app else name)
    if entry is None:
        return f"FAILED: couldn't find an app called '{name}' on this PC."
    RUNNING.add(entry["name"])
    OPENED.append(entry["name"])
    WIN.setdefault(entry["name"], 1)
    apps._remember(entry, "opened")
    return f"OK: {entry['name']} is open; its window is up."


apps.open_app = fake_open
MONS = [{"num": n, "handle": n, "primary": n == 1, "rect": (1920 * (n - 1), 0, 1920 * n, 1080),
         "work": (1920 * (n - 1), 0, 1920 * n, 1040), "where": w} for n, w in ((1, "left"), (2, "middle"), (3, "right"))]
wc.monitors = lambda: MONS
WIN, BUG = {}, {"move_lands_on": None}


def _app_of(args):
    from room_agent.abilities.apps import app_arg, resolve_app_name

    entry, _ = apps.find(resolve_app_name(app_arg(args)) or "")
    return entry["name"] if entry else None


def fake_window_state(args, before=None):
    name = before["app"] if before else _app_of(args)
    if name not in WIN:
        return None
    n = WIN[name]
    return {"app": name, "hwnd": 1, "rect": (0, 0, 10, 10), "state": "normal", "monitor": {"num": n, "where": MONS[n - 1]["where"]}}


def fake_move(app, monitor):
    name = _app_of({"app": app})
    current = MONS[WIN[name] - 1]
    dest, problem = wc.pick_monitor(monitor, MONS, current, None)
    if problem:
        return problem
    WIN[name] = BUG["move_lands_on"] or dest["num"]  # (BUG: the window ends up elsewhere, but the tool says OK)
    apps._remember({"name": name}, "moved")
    return f"OK: {name} is on monitor {dest['num']} now."


wc.move_window_to_monitor = fake_move
core.ensure_loaded()
core.get("move_window_to_monitor").observe = fake_window_state


class Endpoint:
    def __init__(self):
        self.level, self.muted, self.sets = 0.4, False, 0

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, v, _):
        self.level, self.sets = v, self.sets + 1

    def GetMute(self):
        return self.muted

    def SetMute(self, m, _):
        self.muted = bool(m)


EP = Endpoint()
media._endpoint = lambda: EP

# ports + a dev server, registered exactly like any future capability (no special code anywhere for it)
PORTS = {}


def fake_ports(args):
    port = args.get("port")
    if port is not None:
        owner = PORTS.get(int(port))
        return f"OK: port {port} is in use by {owner}." if owner else f"OK: nothing is listening on port {port}."
    return "OK: listening: " + "; ".join(f"{p} ({o})" for p, o in sorted(PORTS.items())) if PORTS else "OK: nothing listening."


core.get("inspect_ports").execute = fake_ports


def fake_processes(args):
    want = str(args.get("name") or "").lower()
    hits = [o for o in PORTS.values() if want and want in o.lower()] + [n for n in RUNNING if want and want in n.lower()]
    return f"OK: {len(hits)} running: " + "; ".join(hits) + "." if hits else f"OK: no running process matches '{want}'."


core.get("find_processes").execute = fake_processes
core.get("list_running_apps").execute = lambda a: ("OK: open apps: " + ", ".join(sorted(RUNNING)) + ".") if RUNNING else \
    "OK: no app windows are open."  # (the simulated PC only: never this computer's real processes)
# what this simulated PC covers itself: harness.simulate_actions(keep=SIMULATED) stubs everything else that's real
SIMULATED = {"open_app", "move_window_to_monitor", "set_volume", "volume_up", "volume_down", "mute", "unmute"}
STOPPED = []


def start_server(args):
    owner = PORTS.get(8000)
    if owner and owner != "devserver":
        return "FAILED: port 8000 is already in use, so the dev server couldn't start."
    PORTS[8000] = "devserver"
    return "OK: dev server started on port 8000."


register_group(Group("devtest", re.compile(r"server|backend|port|dev|environment|workspace", re.I), title="dev server",
                     summary="start the project's dev server"))
register(Capability("start_dev_server", "Start the project's development server (port 8000).", {"type": "object",
                    "properties": {}}, start_server, group="devtest", observe=lambda a, b=None: {"port_8000": PORTS.get(8000)},
                    expect=lambda a, b: {"port_8000": "devserver"}, skip_if_satisfied=True, fresh_for=30))
register(Capability("stop_process", "Stop (kill) a running process by name.", {"type": "object", "properties": {
                    "name": {"type": "string"}}, "required": ["name"]}, lambda a: STOPPED.append(a["name"]) or "OK: stopped.",
                    group="devtest", risk=Risk.SENSITIVE))
