"""Delivery generalizes to wording nobody wrote a rule for (tests/paraphrases.py): SEEN was calibrated on, TEST2 is
held out. Guards the floor, not perfection: plain requests stay plain, nothing is delivered the wrong WAY (upbeat about
bad news, amused in an emergency), and most moods are read.

    .venv\\Scripts\\python -m tests.test_generalization
"""

from tests.harness import Checker, setup_env

setup_env(SOCIAL_MEANING="1")
from tests import paraphrases as P  # noqa: E402

t = Checker()
for name, sets, floor in (("seen (calibration)", P.SEEN, 0.70), ("TEST2 (held out)", P.TEST2, 0.65)):
    results = P.run(sets)
    ok, total, bad = P.report(results, name)
    t.check(f"{name}: plain requests stay plain", all(r[1] for r in results["neutral"]))
    t.check(f"{name}: nothing delivered the wrong way", bad == 0, bad)
    t.check(f"{name}: at least {floor:.0%} of all delivery right ({ok}/{total})", ok / total >= floor)
    moods = [r for c, rows in results.items() if c not in ("neutral", "joking") for r in rows]
    t.check(f"{name}: moods read from meaning, not listed words ({sum(r[1] for r in moods)}/{len(moods)})",
            sum(r[1] for r in moods) / len(moods) >= 0.5)

results = P.run(P.PRESSURE)
_, _, bad = P.report(results, "pressure")
t.check("hurrying alone isn't an emergency; a problem under pressure is urgent, any domain",
        bad == 0 and all(r[1] for r in results["neutral"]) and sum(r[1] for r in results["urgent"]) >= 3)

from room_agent.social import meaning  # noqa: E402

t.check("the reader is fast enough to sit before the model call (< 25 ms)", _ms := __import__("timeit").timeit(
    lambda: meaning.reading("My brain is mush after all those meetings."), number=20) / 20 * 1000 < 25)
t.done("GENERALIZATION TESTS")
