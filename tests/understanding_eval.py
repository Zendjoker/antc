"""Goal understanding evaluation (offline, no model): what code can tell from the user's own words.

The conversational model is the PRIMARY interpreter (update_goal / tool arguments) and can't be measured offline without
paid calls. This measures the deterministic layer that checks it: which capability families a request needs, the
explicit constraints it states (that must be preserved whatever the model does), the permissions it implies, and what is
missing (must be asked). Written BEFORE the V2 goal code; "heldout" cases are never used for tuning.

Normalized labels:
    families      research browser desktop files business coding email calendar lists info
    constraints   "forbid:<verb>[:<object>]" ("forbid:send", "forbid:touch:.txt", "forbid:google"), "only:<ext>",
                  "budget:<usd>", "keep:<object>"
    permissions   send_email delete move paid code_change calendar_write
    missing       referent recipient category location file time

    .venv\\Scripts\\python -m tests.understanding_eval --label v1-baseline
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "benchmarks"

# (text, families, constraints, permissions, missing)
DEV = [
    ("Research the best local speech recognition models and save a report on my desktop", {"research", "files"}, [],
     set(), set()),
    ("open spotify and move it to my second monitor", {"desktop"}, [], set(), set()),
    ("In my Downloads, put the PDFs in a PDFs folder and don't touch the text files", {"files"}, ["forbid:touch:.txt"],
     {"move"}, set()),
    ("Email bob@example.com that the report is ready and send it", {"email"}, [], {"send_email"}, set()),
    ("Draft an email to alice@example.com about Friday's meeting, but don't send it", {"email"}, ["forbid:send"],
     set(), set()),
    ("send it to him", {"email"}, [], {"send_email"}, {"referent", "recipient"}),
    ("book it for tomorrow at 3", {"calendar"}, [], {"calendar_write"}, {"referent"}),
    ("The tests in my calc project fail, find the bug and fix it", {"coding"}, [], {"code_change"}, set()),
    ("look up the earth's radius and fix my geo project so its tests pass", {"research", "coding"}, [],
     {"code_change"}, set()),
    ("find 20 restaurants in Austin with no website and build 3 demos, don't use Google", {"business"},
     ["forbid:google"], set(), set()),
    ("find florists in Seattle with no website and spend at most $2", {"business"}, ["budget:2"], {"paid"}, set()),
    ("delete the old invoices in my documents", {"files"}, [], {"delete"}, set()),
    ("clean up my desktop but keep the screenshots", {"files"}, ["keep:screenshots"], {"move"}, set()),
    ("open the httpx docs page about streaming", {"browser"}, [], set(), set()),
    ("check the weather and add it to my notes", {"info", "lists"}, [], set(), set()),
    ("add milk and eggs to my shopping list", {"lists"}, [], set(), set()),
    ("schedule a call with Dana on Friday at 10", {"calendar"}, [], {"calendar_write"}, set()),
    ("find businesses without websites and make demos", {"business"}, [], set(), {"category", "location"}),
    ("move only the PNG files from Downloads into Pictures", {"files"}, ["only:.png"], {"move"}, set()),
    ("research flight prices to Lisbon but don't book anything", {"research"}, ["forbid:book"], set(), set()),
    ("summarize report.pdf and email the summary to carol@example.com", {"files", "email"}, [], {"send_email"}, set()),
    ("fix the failing test in my api project without changing the tests", {"coding"}, ["forbid:change:tests"],
     {"code_change"}, set()),
    ("open chrome, go to github and show me my notifications", {"desktop", "browser"}, [], set(), set()),
    ("save a note called ideas with 'try the new layout'", {"files"}, [], set(), set()),
    ("make a Trip folder in documents and save the itinerary in it", {"files"}, [], set(), set()),
    ("compare the three cheapest robot vacuums and write it up in a doc", {"research", "files"}, [], set(), set()),
    ("don't open Chrome, just search the web for the httpx changelog", {"research"}, ["forbid:open:chrome"], set(),
     set()),
    ("rename the file", {"files"}, [], {"move"}, {"file"}),
    ("remind me to call mum when I get home", {"lists"}, [], set(), set()),
    ("Find 10 plumbers in Denver without websites, no outreach", {"business"}, ["forbid:outreach"], set(), set()),
]
HELDOUT = [
    ("Look into which password managers support passkeys and put a summary in my documents", {"research", "files"}, [],
     set(), set()),
    ("put VS Code on the left monitor and turn the volume down", {"desktop"}, [], set(), set()),
    ("Sort my Desktop: images into an Images folder, leave the Word documents alone", {"files"}, ["forbid:touch:.docx"],
     {"move"}, set()),
    ("Write to dave@example.com saying I'll be late, and send it right away", {"email"}, [], {"send_email"}, set()),
    ("prepare a reply to the landlord but do not send anything yet", {"email"}, ["forbid:send"], set(),
     {"recipient"}),
    ("forward that to her", {"email"}, [], {"send_email"}, {"referent", "recipient"}),
    ("move it to next Tuesday", {"calendar"}, [], {"calendar_write"}, {"referent"}),
    ("my parser project's unit tests are red, debug it", {"coding"}, [], {"code_change"}, set()),
    ("find the current VAT rate in Germany and update the constant in my invoicing project", {"research", "coding"},
     [], {"code_change"}, set()),
    ("get 15 bakeries in Lyon with poor websites, 2 demos, never use Google data", {"business"}, ["forbid:google"],
     set(), set()),
    ("prospect dentists in Leeds without a site, budget 4 dollars", {"business"}, ["budget:4"], {"paid"}, set()),
    ("trash the duplicate photos in my pictures folder", {"files"}, [], {"delete"}, set()),
    ("tidy my downloads, but keep the installers", {"files"}, ["keep:installers"], {"move"}, set()),
    ("go to the pandas docs for read_csv", {"browser"}, [], set(), set()),
    ("what's the weather tomorrow, and put an umbrella reminder on my todo list", {"info", "lists"}, [], set(), set()),
    ("put bread on the shopping list", {"lists"}, [], set(), set()),
    ("set up a meeting with Omar next Monday at 2pm", {"calendar"}, [], {"calendar_write"}, set()),
    ("find shops that need a new website and build mockups", {"business"}, [], set(), {"category", "location"}),
    ("copy just the .csv files from Documents into a Data folder", {"files"}, ["only:.csv"], set(), set()),
    ("check hotel prices in Rome but do not reserve anything", {"research"}, ["forbid:reserve"], set(), set()),
    ("read contract.docx and send a summary to erin@example.com", {"files", "email"}, [], {"send_email"}, set()),
    ("make the build pass in my web project but don't touch the CI config", {"coding"}, ["forbid:touch:ci config"],
     {"code_change"}, set()),
    ("launch Firefox and open my bank's login page", {"desktop", "browser"}, [], set(), set()),
    ("jot down a note that the boiler was serviced", {"files"}, [], set(), set()),
    ("create a Taxes 2026 folder in documents and save a checklist there", {"files"}, [], set(), set()),
    ("compare three mesh wifi systems and save the comparison as a file", {"research", "files"}, [], set(), set()),
    ("without opening a browser, look up the population of Lisbon", {"research"}, ["forbid:open:browser"], set(),
     set()),
    ("delete that file", {"files"}, [], {"delete"}, {"referent"}),
    ("remind me to water the plants at 6", {"lists"}, [], set(), set()),
    ("find 8 tattoo shops in Berlin with only a Facebook page, skip the outreach", {"business"}, ["forbid:outreach"],
     set(), set()),
]


def baseline_extract(text):
    """What the e071941 code can tell (missions/goals.route + cognition/goal.parse_constraints + business fields)."""
    from room_agent.cognition.goal import parse_constraints
    from room_agent.missions import goals

    fam = {"business_mission": {"business"}, "research": {"research"}, "desktop_task": {"desktop"},
           "site_edit": {"business"}, "email_draft": {"email"}}.get(goals.route(text), set())
    cons = []
    for c in parse_constraints(text):
        cons.append(f"forbid:{c.verb or 'touch'}:{c.subject}")
    g = goals.interpret(text)
    missing = {q["field"] for q in g.questions}
    if g.capability == "business_mission" and g.params.get("use_places") is False:
        cons.append("forbid:google")
    if g.budget_usd:
        cons.append(f"budget:{g.budget_usd:g}")
    return {"families": fam, "constraints": cons, "permissions": set(), "missing": missing}


def extract(text):
    try:
        from room_agent.cognition import understand
    except ImportError:
        return baseline_extract(text)
    s = understand.from_words(text)
    return {"families": set(s.families), "constraints": list(s.constraint_keys()), "permissions": set(s.permissions),
            "missing": set(s.missing)}


def _match(want, got):
    """A constraint is preserved if a produced constraint has the same kind and verb and mentions the same object."""
    w = want.split(":")
    for g in got:
        x = g.lower().split(":")
        if x[0] != w[0]:
            continue
        if w[0] == "budget" and len(x) > 1 and float(x[1]) == float(w[1]):
            return True
        if w[0] in ("only", "keep") and len(x) > 1 and w[1].strip(".") in x[1]:
            return True
        if w[0] == "forbid" and len(x) > 1 and x[1] == w[1] and (len(w) < 3 or (len(x) > 2 and (w[2].strip(".") in x[2]
                                                                                              or x[2].strip(".") in w[2]))):
            return True
    return False


def score(cases):
    fam_exact = fam_recall_n = fam_recall_d = 0
    c_hit = c_total = c_extra = 0
    p_hit = p_total = p_extra = 0
    m_exact = 0
    misses = []
    for text, fam, cons, perms, missing in cases:
        out = extract(text)
        fam_exact += out["families"] == fam
        fam_recall_n += len(fam & out["families"])
        fam_recall_d += len(fam)
        for c in cons:
            c_total += 1
            if _match(c, out["constraints"]):
                c_hit += 1
            else:
                misses.append(("constraint", text[:50], c, out["constraints"]))
        c_extra += max(0, len(out["constraints"]) - len(cons))
        p_total += len(perms)
        p_hit += len(perms & out["permissions"])
        p_extra += len(out["permissions"] - perms)
        m_exact += out["missing"] == missing
        if out["families"] != fam:
            misses.append(("families", text[:50], sorted(out["families"]), sorted(fam)))
        if out["missing"] != missing:
            misses.append(("missing", text[:50], sorted(out["missing"]), sorted(missing)))
    n = len(cases)
    return {"cases": n, "routing_exact": round(fam_exact / n, 3), "routing_recall": round(fam_recall_n / fam_recall_d, 3),
            "constraint_preservation": round(c_hit / c_total, 3) if c_total else None, "constraints_total": c_total,
            "extra_constraints": c_extra, "permission_recall": round(p_hit / p_total, 3) if p_total else None,
            "extra_permissions": p_extra, "clarification_exact": round(m_exact / n, 3), "misses": misses}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label")
    a = ap.parse_args()
    from tests.harness import setup_env

    setup_env()
    res = {"dev": score(DEV), "heldout": score(HELDOUT), "when": time.strftime("%Y-%m-%d %H:%M"),
           "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                    cwd=str(ROOT)).stdout.strip()}
    for k in ("dev", "heldout"):
        print(k, json.dumps({x: y for x, y in res[k].items() if x != "misses"}))
    if a.label:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"understanding-{a.label}.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    sys.stdout.flush()
    import os

    os._exit(0)


if __name__ == "__main__":
    main()
