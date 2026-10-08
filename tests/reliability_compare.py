"""Before / after: the same fault-injected multi-step scenarios run two ways, scored by the END STATE (files, the
simulated world), not by messages.

    before   steps run one after another through the existing per-request Plan (what happens when the model calls the
             tools itself): implicit same-subject blocking and duplicate guard only; no retries, timeouts, success
             checks, checkpoints or resume
    after    the same steps as one task (actions/tasks.py)

    .venv\\Scripts\\python -m tests.reliability_compare        prints the table and writes RELIABILITY_COMPARE.md

This measures the execution machinery only; how a real model plans or phrases things is not part of it.
"""

import tempfile
import threading
import time
from pathlib import Path

from tests.harness import setup_env

setup_env()

from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor, tasks  # noqa: E402
from room_agent.actions.core import Capability  # noqa: E402
from room_agent.computer import files  # noqa: E402

core.ensure_loaded()
tasks.BACKOFF_S = (0.05, 0.1)
home = Path(tempfile.mkdtemp())
files.HOME = home
(home / "Desktop").mkdir()
W = {}


def reset():
    W.clear()
    W.update(net_fails=0, hang=0.0, done=[], claims=[])


def reg(name, fn, **kw):
    core.register(Capability(name=name, description=f"scenario tool {name}", parameters={
        "type": "object", "properties": {"x": {"type": "string"}}}, execute=fn, verification="internal", verified_by="test", **kw))


def net(a):
    if W["net_fails"] > 0:
        W["net_fails"] -= 1
        return "FAILED: the site didn't answer in time."
    W["done"].append("read")
    return "OK: data"


def hang(a):
    time.sleep(W["hang"])
    W["done"].append("hang")
    return "OK: changed"


def step(a):
    W["done"].append(a.get("x"))
    return "OK: did " + str(a.get("x"))


def need_x(a):
    if a.get("x") == "missing":
        return "FAILED: there's nothing called that."
    W["done"].append("needx-" + str(a.get("x")))
    return "OK: done"


reg("s_net", net, changes_state=False)
reg("s_hang", hang)
reg("s_step", step)
reg("s_open", need_x, subject=lambda a: "thing")
reg("s_move", step, subject=lambda a: "thing")
reg("s_ambiguous", lambda a: (W["done"].append("amb"), "UNKNOWN: not confirmed: no answer from the device.")[1], event="email.sent")


def run_before(steps, said):
    """The model calling the tools one by one (one round): the existing Plan. A hung step is waited for in full."""
    rt.new_turn(said)
    rt.turn_no += 1
    plan = executor.Plan()
    rt.current_plan = plan
    out = []
    from room_agent import cancel

    for s in steps:
        if cancel.requested():  # (the model loop checks this before each tool call: llm/loop.py)
            break
        r = plan.run(s["tool"], s.get("args", {}))
        out.append(r.outcome)
        if r.outcome in ("waiting",):
            break
    return out


def run_after(steps, said):
    rt.new_turn(said)
    rt.turn_no += 1
    rt.current_plan = executor.Plan()
    return tasks.run(tasks.new(said, steps))


SCENARIOS = []


def scenario(name):
    def wrap(fn):
        SCENARIOS.append((name, fn))
        return fn
    return wrap


@scenario("a temporary network error on a read step")
def s1(mode):
    reset()
    W["net_fails"] = 2
    (run_before if mode == "before" else run_after)([{"tool": "s_net"}], "check the status")
    return "read" in W["done"]


@scenario("a step that hangs (should end in ~1 s, not claimed)")
def s2(mode):
    reset()
    W["hang"] = 3.0
    t0 = time.time()
    if mode == "before":
        run_before([{"tool": "s_hang"}], "change it")
    else:
        t = run_after([{"tool": "s_hang", "timeout_s": 0.5}], "change it")
        if t["steps"][0]["state"] != "UNKNOWN":
            return False
    return time.time() - t0 < 1.5


@scenario("a failed step: its dependent must not run, an independent one must")
def s3(mode):
    reset()
    steps = [{"tool": "s_open", "args": {"x": "missing"}}, {"tool": "s_step", "args": {"x": "dependent"}, "depends_on": [1]},
             {"tool": "s_step", "args": {"x": "independent"}}]
    (run_before if mode == "before" else run_after)(steps, "open it, use it, and do something else")
    return "dependent" not in W["done"] and "independent" in W["done"]


@scenario("Jarvis restarts after step 2 of 4: the rest gets done, step 2 not repeated")
def s4(mode):
    reset()
    steps = [{"tool": "s_step", "args": {"x": "1"}}, {"tool": "s_step", "args": {"x": "2"}},
             {"tool": "s_step", "args": {"x": "3"}}, {"tool": "s_step", "args": {"x": "4"}}]
    if mode == "before":
        run_before(steps[:2], "four things")  # (the process dies here: nothing records what was left)
        return "3" in W["done"] and "4" in W["done"]
    rt.new_turn("four things")
    t = tasks.new("four things", steps)
    t["steps"][0]["state"], t["steps"][1]["state"] = "COMPLETED", "RUNNING"
    W["done"] += ["1", "2"]
    tasks._save()
    tasks._tasks.clear()
    tasks.load()
    rt.new_turn("resume")
    rt.turn_no += 1
    tasks.resume(tasks.get(t["id"]))
    return W["done"].count("2") == 1 and "3" in W["done"] and "4" in W["done"]


@scenario("an ambiguous result: not claimed, not repeated when asked again")
def s5(mode):
    reset()
    if mode == "before":
        rt.new_turn("send it")
        plan = executor.Plan()
        plan.run("s_ambiguous", {})
        plan.round = 1
        plan.run("s_ambiguous", {})  # (the model asks again in its next round)
    else:
        t = run_after([{"tool": "s_ambiguous"}], "send it")
        if t["state"] == "COMPLETED":
            return False
    return W["done"].count("amb") == 1


@scenario("the tool says OK but the file lacks what was asked: caught")
def s6(mode):
    reset()
    steps = [{"tool": "save_file", "args": {"name": f"s6{mode}", "content": "milk"},
              "check": {"file_contains": [f"Desktop/s6{mode}.md", "coffee"]}}]
    if mode == "before":
        out = run_before(steps, "save a note with milk and coffee")
        return out[0] != "verified"
    t = run_after(steps, "save a note with milk and coffee")
    return t["steps"][0]["state"] == "FAILED"


@scenario("the PC-wide stop during step 2: step 3 never runs")
def s7(mode):
    reset()
    from room_agent import emergency

    reg("s_stop", lambda a: (emergency.stop_everything("hotkey"), "OK: did it")[1])
    steps = [{"tool": "s_step", "args": {"x": "a"}}, {"tool": "s_stop"}, {"tool": "s_step", "args": {"x": "c"}}]
    (run_before if mode == "before" else run_after)(steps, "three steps")
    time.sleep(0.01)
    return "c" not in W["done"]


def main():
    rows = []
    for name, fn in SCENARIOS:
        before = fn("before")
        after = fn("after")
        rows.append((name, before, after))
        print(f"{'ok ' if before else 'BAD'} -> {'ok ' if after else 'BAD'}  {name}")
    b, a = sum(r[1] for r in rows), sum(r[2] for r in rows)
    print(f"\ncorrect end state: before {b}/{len(rows)}, after {a}/{len(rows)}")
    lines = ["# Reliability: before / after", "", "Same fault-injected scenarios, scored by the end state (files, the simulated "
             "world), offline (`python -m tests.reliability_compare`). 'Before' = steps run one by one through the existing "
             "per-request Plan (the model calling tools itself); 'after' = the same steps as one task (actions/tasks.py). "
             "Execution machinery only: a real model's planning isn't part of this.", "",
             "| Scenario | Before | After |", "|---|---|---|"]
    lines += [f"| {n} | {'correct' if b_ else '**wrong**'} | {'correct' if a_ else '**wrong**'} |" for n, b_, a_ in rows]
    lines += ["", f"**Correct end state: before {b}/{len(rows)}, after {a}/{len(rows)}.**"]
    open("RELIABILITY_COMPARE.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return b, a, len(rows)


if __name__ == "__main__":
    main()
