"""Budget-exceeded fallback (room_agent/llm/router.py: _ollama_up, over_budget), offline: no real Ollama server, no
real spend.json. Covers the real 2026-10-09 live failure (Ollama server reachable, but the configured model
'qwen3:14b' had never been pulled -> an unhandled 404 right after Jarvis had already said "switching to the free
local model") and the graceful-degradation path that replaces it.

Run:  .venv\\Scripts\\python -m tests.test_ollama_fallback
"""

import requests

from tests.harness import setup_env

setup_env()

from room_agent.llm import router  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
said = []
router.say = lambda text: said.append(text)
router.finish_speaking = lambda: None


class FakeResponse:
    def __init__(self, ok=True, models=()):
        self.ok = ok
        self._models = models

    def json(self):
        return {"models": [{"name": n} for n in self._models]}


class FakeBudget:
    def __init__(self, total=1.01, limit=1.00):
        self._total, self.limit, self.warned_on = total, limit, ""

    def total(self):
        return self._total

    def exceeded(self):
        return self.limit > 0 and self._total >= self.limit


# ---------------------------------------------------------------- _ollama_up: reachability alone is NOT enough
router.requests.get = lambda url, timeout=None: FakeResponse(ok=True, models=["llama3:8b"])
t.check("server up but the configured model isn't pulled -> not usable (the real 2026-10-09 bug)",
        router._ollama_up("qwen3:14b") is False)

router.requests.get = lambda url, timeout=None: FakeResponse(ok=True, models=["qwen3:14b", "llama3:8b"])
t.check("server up and the exact model is pulled -> usable", router._ollama_up("qwen3:14b") is True)

router.requests.get = lambda url, timeout=None: FakeResponse(ok=True, models=["qwen3:8b"])
t.check("a different tag of the same model name is still accepted (name match, not exact tag)",
        router._ollama_up("qwen3:14b") is True)


def _down(url, timeout=None):
    raise requests.RequestException("connection refused")


router.requests.get = _down
t.check("server unreachable -> not usable", router._ollama_up("qwen3:14b") is False)

router.requests.get = lambda url, timeout=None: FakeResponse(ok=False)
t.check("server responds but not ok (non-200) -> not usable", router._ollama_up("qwen3:14b") is False)

# ---------------------------------------------------------------- over_budget(): the model check gates the claim
router.BUDGET_FALLBACK = "ollama"

# Case A: exactly the real failure - model not pulled. Must NOT claim "switching to the free local model", and
# must NOT call ask_ollama at all (nothing to call).
said.clear()
router.budget = FakeBudget()
router.requests.get = lambda url, timeout=None: FakeResponse(ok=True, models=["llama3:8b"])
router.ask_ollama = lambda temp: t.check("ask_ollama is never called when the model isn't available", False)
router.over_budget([{"role": "user", "content": "hi"}])
t.check("model unavailable: the honest 'can't answer until tomorrow' message is said",
        any("can't answer" in s for s in said), said)
t.check("model unavailable: never claims it's 'switching to the free local model'",
        not any("switching" in s.lower() for s in said), said)

# Case B: model genuinely available and the call succeeds - the normal happy path still works.
said.clear()
router.budget = FakeBudget()
router.requests.get = lambda url, timeout=None: FakeResponse(ok=True, models=["qwen3:14b"])
router.ask_ollama = lambda temp: temp.append({"role": "assistant", "content": "(local model reply)"})
history = [{"role": "user", "content": "hi"}]
router.over_budget(history)
t.check("model available: says it's switching to the local model", any("switching" in s.lower() for s in said), said)
t.check("model available: the local model's reply is kept in history",
        any(m.get("content") == "(local model reply)" for m in history), history)

# Case C: the up-front check passes, but the call itself still fails (model unloaded / server restarted mid-call).
# Must not crash, and must say so instead of leaving a dangling "switching" claim unresolved.
said.clear()
router.budget = FakeBudget()
router.requests.get = lambda url, timeout=None: FakeResponse(ok=True, models=["qwen3:14b"])


def _boom(temp):
    raise RuntimeError("model unloaded mid-request")


router.ask_ollama = _boom
try:
    router.over_budget([{"role": "user", "content": "hi"}])
    crashed = False
except Exception:
    crashed = True
t.check("a failure after the availability check still doesn't crash the turn", not crashed)
t.check("the local-model failure is reported honestly, not silently swallowed",
        any("local backup" in s.lower() or "fail" in s.lower() for s in said), said)

t.done("OLLAMA FALLBACK")
