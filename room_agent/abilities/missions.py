"""Background missions by voice: "find 20 restaurants in San Francisco with no website or a poor one and build demos for
the best", "how far are you", "build demos for the best three", "pause / resume / stop the mission", "show me the demo
for X", "make X's demo blue", "what needs my approval", "approve number 2". Implementation: room_agent/missions/."""

import re

from room_agent.abilities._kit import CONFIDENCE, params, tool
from room_agent.actions.core import Group, Risk, register_context, register_group

HINTS = re.compile(r"\bmissions?\b|\bleads?\b|\bprospects?\b|\bbusinesses\b|without (a )?websites?|poor websites?|"
                   r"\bdemos?\b|demo sites?|outreach|how far (are|along)|\bprogress\b|approv|\bthe best (one|two|three|"
                   r"\d+)\b|restaurants|\bpreview\b", re.I)
register_group(Group("missions", HINTS, lambda: _live(), "background missions",
                     "long jobs that run in the background: finding businesses without a good website, researching "
                     "them, building demo sites and outreach drafts (never sent without your approval)", rules=[
    "- A request to find / research businesses and build demos is ONE call to start_business_mission; Jarvis's code runs "
    "the whole thing in the background. Tell them it started and that you'll report back; never say it's done.",
    "- 'How far are you / how's it going' about a mission: call mission_status and say what it returns, nothing more.",
    "- Never claim leads, demos or drafts exist unless a mission tool's result just said so. Emails are never sent: an "
    "approved email only becomes a Gmail draft they send themselves. Jarvis never calls businesses."]))


def _engine():
    from room_agent.missions import business, engine  # (business registers its workflow on import)

    _ = business
    return engine


def _live():
    try:
        m = _engine().latest()
        return bool(m and m["state"] in ("running", "paused", "paused_budget", "interrupted"))
    except Exception:
        return False


def _mission(args, states=None):
    from room_agent.missions.store import store

    mid = (args.get("mission_id") or "").strip()
    if mid:
        return store().mission(mid)
    return _engine().latest(states) or (None if states is None else _engine().latest())


def _lead_by_name(mid, name):
    from room_agent.missions.store import norm_text, store

    want = norm_text(name)
    if not want:
        return None
    leads = store().leads(mission_id=mid, limit=1000)
    exact = [x for x in leads if norm_text(x["name"]) == want]
    if exact:
        return exact[0]
    part = [x for x in leads if want in norm_text(x["name"]) or norm_text(x["name"]) in want]
    return part[0] if len(part) == 1 else None


# ---------------------------------------------------------------- tools
def _start(args):
    from room_agent.missions import business
    from room_agent import config

    try:
        p = business.normalize({k: args.get(k) for k in ("category", "location", "count", "website_filter", "radius_km",
                                                          "demos", "outreach") if args.get(k) is not None}
                               | {"use_places": args.get("use_google_places", "auto")})
    except (ValueError, TypeError) as e:
        return f"NEEDS: {e}."
    busy = _engine().latest(("running",))
    if busy:
        return (f"FAILED: another mission is running ('{busy['title']}'). Pause or stop it first, or wait for it. Nothing "
                "was started.")
    budget = args.get("budget_usd")
    if budget is not None and float(budget) > config.MISSION_MAX_BUDGET_USD:
        return f"FAILED: the most a mission may spend is ${config.MISSION_MAX_BUDGET_USD:.2f} (MISSION_MAX_BUDGET_USD)."
    m = business.start(p, budget)
    from room_agent.missions.store import store

    steps = store().steps(m["id"])
    if not steps or m["state"] != "running":
        return "FAILED: the mission couldn't be planned."
    places = "Google Places and OpenStreetMap" if (p["use_places"] is True or (p["use_places"] == "auto" and
                                                                                config.GOOGLE_PLACES_API_KEY)) else "OpenStreetMap"
    return (f"OK: started in the background: '{m['title']}' (mission {m['id']}). It will find businesses on {places}, check "
            f"each one's website, rank them, then build {p['demos']} demo site{'s' if p['demos'] != 1 else ''}"
            f"{' and outreach drafts' if p['outreach'] else ''} for the best. Budget ${m['budget_usd']:.2f}. Files go to "
            f"{m['workspace']}. Nothing is sent or published. It is NOT done yet: say it has started and that you'll tell "
            "them when it's finished.")


def _status(args):
    m = _mission(args)
    if m is None:
        return "OK: there are no missions yet."
    eng = _engine()
    p = eng.progress(m["id"])
    line = eng.status_line(m["id"])
    extra = ""
    if p.get("top"):
        extra = " Best so far: " + "; ".join(f"{t['name']} ({t['status']}, score {t['score']})" for t in p["top"][:3]) + "."
    if p.get("errors"):
        extra += " Recent problems: " + " | ".join(p["errors"][-2:])
    return f"OK: {line}{extra}"


def _pause(args):
    m = _mission(args, ("running", "planned"))
    if m is None or m["state"] not in ("running", "planned"):
        return "OK: no mission is running."
    return f"OK: paused '{m['title']}'. Say 'resume the mission' to continue." if _engine().pause(m["id"]) else \
        f"FAILED: '{m['title']}' couldn't be paused (it's {m['state']})."


def _resume(args):
    m = _mission(args, ("paused", "paused_budget", "interrupted"))
    if m is None or m["state"] not in ("paused", "paused_budget", "interrupted"):
        return "OK: no mission is paused."
    if _engine().resume(m["id"], float(args.get("extra_budget_usd") or 0)):
        return f"OK: resumed '{m['title']}' in the background."
    from room_agent.missions.store import store

    m = store().mission(m["id"])
    return (f"FAILED: not resumed: it has spent ${m['spent_usd']:.2f} of its ${m['budget_usd']:.2f} budget. Ask if they want "
            "to add budget (extra_budget_usd).")


def _stop(args):
    m = _mission(args, ("running", "paused", "paused_budget", "interrupted", "planned"))
    if m is None or m["state"] in ("completed", "cancelled", "finished_with_problems"):
        return "OK: no mission is running or paused."
    return (f"OK: stopped '{m['title']}'. What it already found and built is kept in {m['workspace']}."
            if _engine().stop(m["id"]) else f"FAILED: couldn't stop '{m['title']}'.")


def _demos(args):
    from room_agent.missions import business

    m = _mission(args)
    if m is None:
        return "FAILED: there's no mission to build demos for."
    try:
        picked = business.build_demos(m["id"], int(args.get("count") or 3),
                                      [x["id"] for x in (_lead_by_name(m["id"], n) for n in args.get("businesses") or []) if x])
    except ValueError as e:
        return f"FAILED: {e}."
    if not picked:
        return ("OK: nothing to build: no researched business qualifies yet, or the best ones already have a demo. Check "
                "mission_status.")
    return (f"OK: queued demo sites for {', '.join(x['name'] for x in picked)} in the background; NOT built yet. Tell them "
            "you'll say when they're ready.")


def _leads(args):
    from room_agent.missions.store import store

    m = _mission(args)
    if m is None:
        return "OK: no missions yet, so no leads."
    rows = store().leads(mission_id=m["id"], min_score=args.get("min_score"), limit=int(args.get("limit") or 5))
    rows = [x for x in rows if x.get("researched")]
    if not rows:
        return f"OK: no researched businesses yet in '{m['title']}'."
    return "OK: " + " | ".join(f"{x['name']}: {x['website_status']}, score {x.get('score')} ({x.get('level')}), "
                               f"phone {'yes' if x.get('phone') else 'no'}, email {'yes' if x.get('email') else 'no'}, "
                               f"status {x['status']}" for x in rows)


def _project_for(args):
    from room_agent.missions.store import store

    m = _mission(args)
    if m is None:
        return None, None, "FAILED: there's no mission."
    projects = store().projects(m["id"])
    if not projects:
        return m, None, "FAILED: this mission has no demo sites yet."
    name = args.get("business") or ""
    if name:
        lead = _lead_by_name(m["id"], name)
        proj = next((p for p in projects if lead and p["lead_id"] == lead["id"]), None)
        if proj is None:
            return m, None, f"FAILED: no demo site for '{name}' in this mission."
        return m, proj, ""
    if len(projects) == 1:
        return m, projects[0], ""
    return m, None, "NEEDS: which business's demo (" + ", ".join(
        store().lead(p["lead_id"])["name"] for p in projects[:5]) + ")?"


def _preview(args):
    from room_agent.computer import browsers
    from room_agent.missions import preview

    m, proj, err = _project_for(args)
    if err:
        return err
    if not preview.start():
        return "FAILED: the local preview server couldn't start (the port may be in use: MISSION_PREVIEW_PORT)."
    return browsers.open_url(preview.url_for(proj["path"]), args.get("browser") or "", said="", query="design preview")


def _edit(args):
    from room_agent.missions import business, coder

    instruction = (args.get("change") or "").strip()
    if not instruction:
        return "NEEDS: what to change."
    m, proj, err = _project_for(args)
    if err:
        return err
    if coder.backend() == "none":
        return "FAILED: no coding worker is set up (install Claude Code, or set ANTHROPIC_API_KEY, or CODER_BACKEND)."
    business.request_edit(m["id"], proj["id"], instruction)
    return (f"OK: queued the change to {proj['slug']} in the background ({coder.backend()}); NOT done yet. The site is "
            "backed up first and checked after; you'll hear when it's finished.")


def _approvals(args):
    from room_agent.missions.store import store

    m = _mission(args)
    rows = store().approvals(m["id"] if m else None, "pending")
    if not rows:
        return "OK: nothing waits for their approval."
    return "OK: waiting for approval: " + " | ".join(f"#{a['id']}: {a['summary']}" for a in rows[:6])


def _decide(args):
    from room_agent.missions import outreach
    from room_agent.missions.store import store

    s = store()
    a = s.approval(int(args.get("approval_id") or 0))
    if a is None or a["status"] != "pending":
        return "FAILED: there's no pending approval with that number."
    if args.get("decision") == "reject":
        s.decide_approval(a["id"], "rejected", "voice")
        return f"OK: rejected #{a['id']}; nothing was done."
    ok, result = outreach.execute_approval(a)
    s.decide_approval(a["id"], "done" if ok else "failed", "voice", result)
    return ("OK: " if ok else "FAILED: ") + result


def _describe_decide(args):
    from room_agent.missions.store import store

    a = store().approval(int(args.get("approval_id") or 0))
    if a is None:
        return "carry out an approval"
    return ("reject: " if args.get("decision") == "reject" else "") + a["summary"]


def _export(args):
    from room_agent.missions.store import store

    m = _mission(args)
    if m is None:
        return "FAILED: there's no mission."
    fmt = "json" if args.get("format") == "json" else "csv"
    from pathlib import Path

    path, n = store().export(Path(m["workspace"]) / f"leads.{fmt}", m["id"], fmt)
    if not path.exists():
        return "FAILED: the export file wasn't written."
    return f"OK: exported {n} leads to {path}."


MID = {"mission_id": {"type": "string", "description": "Leave out for the latest mission"}}
tool("start_business_mission",
     "Start a background mission: find local businesses of a kind in a place, check whether each has a website and how "
     "good it is, rank the best opportunities, build demo sites and outreach drafts for the top ones. Runs for minutes "
     "to hours; nothing is sent or published.",
     params({"category": {"type": "string", "description": "e.g. restaurants, pizza places, dentists, hair salons, plumbers"},
             "location": {"type": "string", "description": "a city / neighbourhood / address, e.g. 'San Francisco, CA'"},
             "count": {"type": "integer", "description": "how many qualifying businesses they want (default 20, max 50)"},
             "website_filter": {"type": "string", "enum": ["none", "poor", "none_or_poor", "any"],
                                "description": "none = no website, poor = a weak one; default none_or_poor"},
             "radius_km": {"type": "number", "description": "search radius around the place (default 3)"},
             "demos": {"type": "integer", "description": "demo sites to build for the best ones (default 3, 0 = none)"},
             "outreach": {"type": "boolean", "description": "prepare email drafts / call notes (default true)"},
             "budget_usd": {"type": "number", "description": "only if they name a budget"},
             "use_google_places": {"type": "boolean", "description": "only if they ask for Google data (paid per request)"},
             "confidence": CONFIDENCE},
            ["category", "location"]),
     _start, group="missions", risk=Risk.CONFIRM, min_confidence=0.7,
     examples=["find 20 restaurants in San Francisco that have no website or a poor website and make demos for the best"],
     verification="internal", verified_by="the mission and its plan are read back from the database")
tool("mission_status", "How the background mission is going (from its stored state): found / checked / qualifying "
     "businesses, demos, drafts, spend, problems.", params(MID), _status, group="missions", changes_state=False,
     examples=["how far are you", "how's the mission going"])
tool("pause_mission", "Pause the running background mission (resumable).", params(MID), _pause, group="missions",
     verification="internal", verified_by="the mission state is written and read back")
tool("resume_mission", "Resume a paused / interrupted mission, optionally with more budget.",
     params({**MID, "extra_budget_usd": {"type": "number", "description": "only if they agree to spend more"}}), _resume,
     group="missions", verification="internal", verified_by="the mission state is written and read back")
tool("stop_mission", "Stop the background mission for good (what it found and built is kept).",
     params({**MID, "confidence": CONFIDENCE}), _stop,
     group="missions", risk=Risk.CONFIRM, min_confidence=0.7, verification="internal",
     verified_by="the mission and its pending steps are marked cancelled")
tool("build_mission_demos", "Build demo sites (and outreach drafts) for the best businesses a mission found, or for "
     "named ones.", params({**MID, "count": {"type": "integer", "description": "how many (default 3)"},
                            "businesses": {"type": "array", "items": {"type": "string"}, "description": "names, if they said"}}),
     _demos, group="missions", examples=["build demos for the best three"], verification="internal",
     verified_by="the queued steps are read back; the build itself is reported when it ends")
tool("list_mission_leads", "The best businesses a mission found, with website status, score and contact availability.",
     params({**MID, "limit": {"type": "integer"}, "min_score": {"type": "integer"}}), _leads, group="missions",
     changes_state=False, private=True)
tool("open_demo_preview", "Open a mission's demo site in the browser (local preview, not published).",
     params({**MID, "business": {"type": "string"}, "browser": {"type": "string"}}), _preview, group="missions",
     claim="browser", verification="internal", verified_by="the browser window is checked for the page")
tool("edit_demo_site", "Change a demo site ('make it blue', 'add an opening-hours section'): done in the background by "
     "the coding worker, backed up first and checked after.",
     params({**MID, "business": {"type": "string"}, "change": {"type": "string", "description": "what to change, in their words"},
             "confidence": CONFIDENCE}, ["change"]), _edit, group="missions", risk=Risk.CONFIRM, min_confidence=0.7, verification="internal",
     verified_by="the edit step is queued; its outcome is checked by coder.py and announced")
tool("mission_approvals", "What the mission is waiting for their approval on (e.g. putting an email in Gmail as a draft).",
     params(MID), _approvals, group="missions", changes_state=False, private=True)
tool("decide_mission_approval", "Approve or reject one waiting item by its number. Approving an email creates a Gmail "
     "DRAFT only (never sent).",
     params({"approval_id": {"type": "integer"}, "decision": {"type": "string", "enum": ["approve", "reject"]}},
            ["approval_id", "decision"]), _decide, group="missions", risk=Risk.SENSITIVE, confirm_keys=["approval_id", "decision"],
     describe=_describe_decide, private=True, verification="internal", verified_by="the Gmail draft is read back")
tool("export_mission_leads", "Save a mission's leads as a CSV (or JSON) file in its folder.",
     params({**MID, "format": {"type": "string", "enum": ["csv", "json"]}}), _export, group="missions",
     verification="internal", verified_by="the file is checked on disk")


def _context(user_text):
    """One line while a mission is live, so 'how far are you' / 'stop' refer to it."""
    try:
        m = _engine().latest()
    except Exception:
        return []
    if not m or m["state"] not in ("running", "paused", "paused_budget", "interrupted"):
        return []
    return [f"- background mission {m['id']} '{m['title']}' is {m['state']} (mission_status for details)"]


register_context(_context, order=16)
