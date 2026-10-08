"""A separate process for tests/test_recovery.py: builds one mission scenario in the (temporary) missions database and
runs it until the armed crash point (JARVIS_CRASH_AT) kills the process with os._exit - no cleanup of any kind.

Run only by tests/test_recovery.py, which passes its own isolated environment (temp MISSIONS_DB / MISSIONS_DIR /
spend file, fake keys, JARVIS_TEST=1). It deliberately does NOT import the `tests` package (that would switch to a new
temp database). Every provider is a fake: models answer from a mock transport, Gmail is a JSON file.

    python tests/recovery_child.py <scenario>      prints "MISSION <id>" (and "APPROVAL <id>"), then runs
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_real_connect = socket.socket.connect


def _guard(self, addr):
    host = addr[0] if isinstance(addr, tuple) else addr
    if not (str(host).startswith("127.") or host in ("::1", "localhost")):
        raise OSError(f"recovery test: network to {addr!r} blocked")
    return _real_connect(self, addr)


SDK = {"calls": 0, "reply": "ok"}


def install_fakes():
    """No network, fake model SDKs (real clients on a mock transport), a file-backed fake Gmail, silent announcements."""
    socket.socket.connect = _guard
    import anthropic
    import httpx2
    import openai

    import room_agent.llm.client as llm_client
    import room_agent.llm.openai_backend as oai_backend
    from room_agent import config
    from room_agent.missions import engine

    assert config.TEST_MODE and tempfile.gettempdir() in str(config.MISSIONS_DB), config.MISSIONS_DB

    def transport(req):
        SDK["calls"] += 1
        if req.url.path.endswith("/messages"):
            return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "x",
                                              "content": [{"type": "text", "text": SDK["reply"]}], "stop_reason": "end_turn",
                                              "usage": {"input_tokens": 1000, "output_tokens": 300}})
        return httpx2.Response(200, json={"id": "c", "object": "chat.completion", "created": 1, "model": "x",
                                          "choices": [{"index": 0, "finish_reason": "stop",
                                                       "message": {"role": "assistant", "content": SDK["reply"]}}],
                                          "usage": {"prompt_tokens": 1000, "completion_tokens": 300, "total_tokens": 1300}})

    oai = openai.OpenAI(api_key="sk-test-not-real", base_url="https://api.openai.test/v1",
                        http_client=httpx2.Client(transport=httpx2.MockTransport(transport)), max_retries=0)
    ant = anthropic.Anthropic(api_key="sk-ant-test-not-real", base_url="https://api.anthropic.test",
                              http_client=httpx2.Client(transport=httpx2.MockTransport(transport)), max_retries=0)
    oai_backend.openai_client = lambda: oai
    llm_client.client = lambda: ant
    engine._announce = lambda text, mid: None
    engine.BACKOFF_S = (0.05, 0.05, 0.05)
    config.CODER_BACKEND = "anthropic"

    import room_agent.integrations as integ
    import room_agent.integrations.google.gmail as gmailmod

    drafts_file = Path(config.MISSIONS_DIR) / "fake_gmail_drafts.json"

    class FakeGmail:
        def __init__(self, provider, account=None):
            pass

        def _all(self):
            return json.loads(drafts_file.read_text(encoding="utf-8")) if drafts_file.exists() else []

        def create_draft(self, to, subject, body, reply_to=None, message_id=""):
            drafts = self._all()
            drafts.append({"id": f"d{len(drafts) + 1}", "to": to, "subject": subject, "message_id": message_id})
            drafts_file.parent.mkdir(parents=True, exist_ok=True)
            drafts_file.write_text(json.dumps(drafts), encoding="utf-8")
            return {"id": drafts[-1]["id"], "to": to, "subject": subject, "body": body}

        def find_draft_by_message_id(self, message_id):
            return next((d["id"] for d in self._all() if d["message_id"] == message_id), None)

    integ.provider = lambda pid: NS(can=lambda service, level: True)
    gmailmod.GmailService = FakeGmail
    return drafts_file


def register():
    """The test workflow 'rec' (also registered by the parent, for recovery)."""
    from room_agent.missions import business, crashpoints, engine, llm, meter
    from room_agent.missions.engine import StepResult, StepSpec, Workflow

    def quick(ctx, st):
        ctx.store.event(ctx.id, f"ran {st['key']}")
        return StepResult(True, evidence="done")

    def fanout(ctx, st):
        ctx.store.event(ctx.id, f"ran {st['key']}")
        return StepResult(True, evidence="fanned out", add=[StepSpec(f"{st['key']}:c{i}", "quick", f"child {i}", {},
                                                                    [st["key"]]) for i in (1, 2)])

    def crash_mid(ctx, st):
        ctx.store.event(ctx.id, f"started {st['key']}")
        crashpoints.hit("during_operation")
        return StepResult(True, evidence="done")

    def paid(ctx, st):
        try:
            text = llm.complete("summarize this " * 20, tier="light", max_tokens=60, what="recovery paid call",
                                idem_key=f"paid:{ctx.id}:{st['key']}")
        except meter.OperationUncertain as e:
            return StepResult(False, error=str(e), final=True)
        ctx.store.event(ctx.id, f"ran {st['key']}")
        return StepResult(True, {"text": text}, evidence="paid call done")

    return engine.register(Workflow(
        kind="rec", title="recovery test", plan=lambda p: [], describe=lambda p: p.get("title", "recovery test"),
        summarize=lambda ctx: "done", handlers={"quick": quick, "fanout": fanout, "crash_mid": crash_mid, "paid": paid,
                                                "edit": business.step_edit},
        reconcile=lambda m, st: business.reconcile(m, st), recover=business.recover))


def make_mission(steps, title, budget=1.0, start=True, setup=None):
    """steps: [(key, kind, idempotent, depends)] -> mission id (running, unless start=False)."""
    import uuid

    from room_agent import config
    from room_agent.missions import engine
    from room_agent.missions.store import store

    s = store()
    mid = time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]
    ws = Path(config.MISSIONS_DIR) / mid
    ws.mkdir(parents=True, exist_ok=True)
    s.create_mission(mid, "rec", title, {"title": title}, budget, ws)
    extra = setup(mid, ws) if setup else {}
    for key, kind, idem, deps in steps:
        s.add_step(mid, key, kind, key, extra.get(key, {}), deps, idem, 1 if not idem else 3)
    print(f"MISSION {mid}", flush=True)  # (before anything runs: an early crash point must not hide the id)
    if start:
        s.set_state(mid, "running", "test start")
        engine.start_runner()
    return mid


def edit_setup(mid, ws):
    """A demo site + project for an 'edit' step; the fake model answers with a CSS change."""
    from room_agent.missions import sitegen
    from room_agent.missions.store import store

    s = store()
    lid, _ = s.upsert_lead({"key": f"osm:node/{mid}", "name": "Crash Cafe", "category": "cafe",
                            "address": "1 Main St, Testville", "sources": ["https://www.openstreetmap.org/node/1"]}, mid)
    folder, problems = sitegen.build(s.lead(lid), ws)
    assert not problems, problems
    proj = s.add_project(lid, mid, folder.name, folder, sitegen.STACK)
    css = (folder / "styles.css").read_text(encoding="utf-8")
    SDK["reply"] = json.dumps({"files": {"styles.css": css + "\n/* edited by the recovery test */\n"}, "summary": "edited"})
    return {"edit": {"project_id": proj["id"], "instruction": "tighten the footer", "sig": "x"}}


SCENARIOS = {
    "quick2": lambda: make_mission([("a", "quick", True, []), ("b", "quick", True, ["a"])], "two quick steps"),
    "crash_mid": lambda: make_mission([("x", "crash_mid", True, []), ("y", "quick", True, ["x"])], "idempotent mid-crash"),
    "crash_mid_nonidem": lambda: make_mission([("x", "crash_mid", False, []), ("z", "quick", True, ["x"])],
                                              "non-idempotent mid-crash"),
    "fanout": lambda: make_mission([("f", "fanout", True, [])], "fan-out"),
    "paid": lambda: make_mission([("p", "paid", True, [])], "paid call"),
    "edit": lambda: make_mission([("edit", "edit", False, [])], "site edit", setup=edit_setup),
}


def wait_end(mid, timeout=60):
    from room_agent.missions.store import store

    end = time.time() + timeout
    while time.time() < end:
        if store().mission(mid)["state"] in ("completed", "finished_with_problems", "cancelled", "paused", "paused_budget"):
            return
        time.sleep(0.05)


def gmail_scenario():
    from room_agent.missions import engine, outreach
    from room_agent.missions.store import store

    s = store()
    mid = make_mission([], "gmail", start=False)
    lid, _ = s.upsert_lead({"key": f"osm:node/g{mid}", "name": "Mail Diner", "category": "restaurant",
                            "email": "owner@maildiner.example", "sources": ["https://www.openstreetmap.org/node/2"]}, mid)
    out, _ = outreach.prepare(s.lead(lid), mid, Path(s.mission(mid)["workspace"]), has_demo=False)
    print(f"APPROVAL {out['approval_id']}", flush=True)
    ok, msg = outreach.approve(out["approval_id"], "recovery test")
    print(f"APPROVED {ok} {msg}", flush=True)
    _ = engine


if __name__ == "__main__":
    install_fakes()
    register()
    name = sys.argv[1]
    if name == "gmail":
        gmail_scenario()
        sys.exit(0)
    mid = SCENARIOS[name]()
    wait_end(mid)
    print("NO_CRASH", flush=True)
    time.sleep(0.2)
    os._exit(0)
