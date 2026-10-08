"""Regression guard for the task engine: every fault-injected scenario in tests/reliability_compare.py must end in the
correct state with the engine (the 'after' column). Offline.

Run:  .venv\Scripts\python -m tests.test_reliability_compare
"""

from tests.harness import setup_env

setup_env()

from tests import reliability_compare as rc  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
for name, fn in rc.SCENARIOS:
    t.check(f"engine: {name}", fn("after"))
t.done("RELIABILITY COMPARE")
