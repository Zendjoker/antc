"""Run the test suites:

    python -m tests                  the safe automated suite: unit + integration + audio regression (free, offline,
                                     never touches the PC, real devices or a paid API)
    python -m tests --hardware       ...plus the PC tests (really opens/moves apps, changes the volume, plays music
                                     briefly; each puts things back afterwards). --live is the old name for this.
    python -m tests --live-api       ...plus the suites that call a real model (paid API or the local Ollama model):
                                     only when that's been approved
    python -m tests apps media       only the suites whose names contain these words
    python -m tests --list           what's in each category, without running anything

Writes tests/report.json: one entry per suite (category, passed, seconds, failing checks), secrets redacted.
Not included: measurement scripts (latency_profile, token_measure, *_bench, ...).
"""

import json
import os
import re
import subprocess
import sys
import time

CATEGORIES = {
    "unit": ["test_isolation", "test_cost", "test_intent", "test_turn_intent", "test_model_routing", "test_claims", "test_speech", "test_generalization", "test_reliability",
             "test_conversation_policy", "test_memory_lifecycle", "test_proactive", "test_learning", "test_location",
             "test_cognition"],
    "integration": ["test_actions", "test_apps", "test_media", "test_windows", "test_integrations", "test_phone",
                    "test_pending", "test_social", "test_fixes", "test_live_fixes", "test_reliability_fixes", "test_tasks",
                    "test_greet", "test_zigbee", "test_smart_home", "test_voice_delivery", "test_turn_taking",
                    "test_timer_correction", "test_diagnostics", "test_browser", "test_screen", "test_research", "test_service", "test_lists", "test_files", "test_music",
                    "test_texts", "test_pcsettings", "test_safety", "test_eval_scoring", "test_cancellation", "test_google_health",
                    "test_tool_registry", "test_task_engine", "test_benchmark_scoring",
                    "test_reliability_compare", "test_missions", "test_security", "test_recovery", "test_hardening", "test_intelligence", "test_v2_intelligence", "test_errand", "test_action_safety", "test_memory_context",
                    "test_security_hardening", "test_personality"],
    "audio": ["test_listening", "test_speaker", "test_latency"],
    "hardware": ["apps_live", "media_live", "windows_live", "actions_live", ("computer_live", "--act")],
    "live_api": [("test_turn_taking", "--live-api"), "test_ring_ack", "cognition_live", "pending_live"],
    # (tool_choice_live needs an explicit --provider/--model/--yes: run by hand once approved)
}
SAFE = ("unit", "integration", "audio")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, "tests", "report.json")
_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|sk_[A-Za-z0-9]{8,}|AC[0-9a-f]{32}|\+\d{10,15}|"
                     r"(?i:(?:key|token|secret|password|auth)\s*[=:]\s*)\S+)")


def _secrets():
    """The values in .env that look like credentials, so a test's output can never leak them into the report."""
    out = set()
    try:
        for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
            k, _, v = line.partition("=")
            v = v.split("#")[0].strip().strip('"').strip("'")
            if len(v) >= 8 and re.search(r"KEY|TOKEN|SECRET|PASSWORD|SID|PHONE|NUMBER", k.upper()):
                out.add(v)
    except OSError:
        pass
    return out


def redact(text, secrets):
    for s in secrets:
        text = text.replace(s, "[redacted]")
    return _SECRET.sub("[redacted]", text)


def plan(argv):
    hardware = "--hardware" in argv or "--live" in argv
    live_api = "--live-api" in argv
    words = [a for a in argv if not a.startswith("--")]
    cats = list(SAFE) + (["hardware"] if hardware else []) + (["live_api"] if live_api else [])
    out = []
    for cat in cats:
        for entry in CATEGORIES[cat]:
            name, *args = entry if isinstance(entry, tuple) else (entry,)
            if os.path.exists(os.path.join(ROOT, "tests", name + ".py")) and (not words or any(w in name for w in words)):
                out.append((cat, name, args))
    return out, hardware, live_api


def main(argv):
    suites, hardware, live_api = plan(argv)
    if "--list" in argv:
        for cat, names in CATEGORIES.items():
            print(f"{cat:12} " + ", ".join(" ".join(n) if isinstance(n, tuple) else n for n in names))
        return 0
    secrets, results = _secrets(), []
    for cat, name, args in suites:
        t0 = time.time()
        try:
            env = {k: v for k, v in os.environ.items() if k != "JARVIS_LIVE_API"}  # (real keys: the live_api group only)
            if cat == "live_api":
                env["JARVIS_LIVE_API"] = "1"
            p = subprocess.run([sys.executable, "-u", "-m", f"tests.{name}", *args], cwd=ROOT, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=600, env=env)
            ok, out, code = p.returncode == 0, p.stdout + p.stderr, p.returncode
        except subprocess.TimeoutExpired:
            ok, out, code = False, "timed out after 600 s", None
        fails = [line.strip() for line in out.splitlines() if line.strip().startswith(("FAIL", "[FAIL]", "Traceback"))]
        ok = ok and not fails and ("PASSED" in out or "SKIPPED" in out)  # (older suites print their result without an exit code)
        dt = time.time() - t0
        entry = {"suite": name + (" " + " ".join(args) if args else ""), "category": cat, "passed": ok,
                 "seconds": round(dt, 1), "exit_code": code}
        if not ok:  # (diagnostics: the failing checks, and the end of the output for a crash)
            entry["failures"] = [redact(f, secrets)[:300] for f in fails[:20]]
            entry["output_tail"] = redact("\n".join(out.splitlines()[-25:]), secrets)
        results.append(entry)
        print(f"  {'ok  ' if ok else 'FAIL'} {cat:11} {entry['suite']:28} {dt:5.1f}s"
              + ("" if ok else "   " + "; ".join(entry["failures"][:3])), flush=True)
    bad = [r for r in results if not r["passed"]]
    report = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "python": sys.version.split()[0],
              "categories_run": sorted({r["category"] for r in results}), "suites": len(results),
              "passed": len(results) - len(bad), "failed": [r["suite"] for r in bad], "results": results}
    if suites:
        with open(REPORT, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    print(f"\n{len(results) - len(bad)} of {len(results)} suites passed"
          + ("" if hardware else " (add --hardware for the PC tests)")
          + ("" if live_api else " (--live-api for the real-model ones)") + f"\nreport: {os.path.relpath(REPORT, ROOT)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
