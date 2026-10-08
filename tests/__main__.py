"""Run the test suites:

    python -m tests            every offline suite (free, doesn't touch the PC)
    python -m tests --live     ...plus the live ones (really opens/moves apps, changes the volume, plays music briefly;
                               each puts things back afterwards)
    python -m tests apps media only the suites whose names contain these words

Not included: tests that call a real (paid) model, and measurement scripts.
"""

import os
import subprocess
import sys
import time

OFFLINE = ["test_cost", "test_reliability", "test_listening", "test_speaker", "test_fixes", "test_intent", "test_claims",
           "test_apps", "test_media", "test_windows", "test_actions", "test_learning", "test_integrations", "test_location", "test_phone", "test_pending", "test_social", "test_cognition", "test_speech", "test_generalization", "test_zigbee", "test_live_fixes", "test_greet", "test_reliability_fixes", "test_conversation_policy", "test_latency", "test_memory_lifecycle", "test_proactive", "test_tasks", "test_voice_delivery", "test_smart_home"]
LIVE = ["apps_live", "media_live", "windows_live", "actions_live"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv):
    live = "--live" in argv
    words = [a for a in argv if not a.startswith("--")]
    suites = [s for s in OFFLINE + (LIVE if live else [])
              if os.path.exists(os.path.join(ROOT, "tests", s + ".py")) and (not words or any(w in s for w in words))]
    results = []
    for name in suites:
        t0 = time.time()
        try:
            p = subprocess.run([sys.executable, "-u", "-m", f"tests.{name}"], cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=600)
            ok, out = p.returncode == 0, p.stdout + p.stderr
        except subprocess.TimeoutExpired:
            ok, out = False, "timed out"
        fails = [line.strip() for line in out.splitlines() if line.strip().startswith(("FAIL", "[FAIL]", "Traceback"))]
        ok = ok and not fails and ("PASSED" in out or "SKIPPED" in out)  # (older suites print their result without an exit code)
        results.append((name, ok, time.time() - t0, fails))
        print(f"  {'ok  ' if ok else 'FAIL'} {name:20} {time.time() - t0:5.1f}s" + ("" if ok else "   " + "; ".join(fails[:3])),
              flush=True)
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)} of {len(results)} suites passed" + ("" if live else " (add --live for the PC tests)"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
