"""The 'business' mission: find local businesses with no / a poor website, research them, rank them, build demo sites and
prepare outreach for the best ones. Category- and city-agnostic (restaurants in San Francisco, dentists in Austin...).

Plan (steps run by missions/engine.py, checkpointed in missions.db):
    discover               find candidates (OpenStreetMap; Google Places if configured) -> one research step each
    research:<lead>        verify the website status (their listed site, else a web search), check its quality, collect
                           a public email from their own site, score it with reasons; stops early once enough qualify
    rank                   pick the best `count` that match the website filter; write leads.csv / leads.json / report.md
    demo:<lead>            build a demo site for the top `demos` (checked against the content rules)
    outreach:<lead>        email draft + call notes + proposal; an approval request to put the email in Gmail as a draft
Code decides everything here; models are only used (optionally) for wording and for site edits you ask for.
"""

import logging
import time
from pathlib import Path

from room_agent import config
from room_agent.missions import discovery, engine, outreach, sitegen, websites
from room_agent.missions.engine import StepResult, StepSpec, Workflow
from room_agent.missions.store import norm_phone, norm_text, store

log = logging.getLogger("room-agent")
FILTERS = {
    "none": ("none_found", "social_only", "directory_only", "broken"),
    "poor": ("poor", "broken"),
    "none_or_poor": ("none_found", "social_only", "directory_only", "broken", "poor"),
    "any": ("none_found", "social_only", "directory_only", "broken", "poor", "ok", "blocked", "possible"),
}
FRESH_S = 14 * 86400  # research younger than this is reused instead of fetched again
MIN_SCORE = 35


def normalize(params):
    p = dict(params or {})
    p["category"] = str(p.get("category") or "").strip()
    p["location"] = str(p.get("location") or "").strip()
    p["count"] = max(1, min(int(p.get("count") or 20), 50))
    p["radius_km"] = max(0.5, min(float(p.get("radius_km") or 3.0), 25.0))
    p["website_filter"] = p.get("website_filter") if p.get("website_filter") in FILTERS else "none_or_poor"
    p["demos"] = max(0, min(int(p.get("demos") if p.get("demos") is not None else 3), 10))
    p["outreach"] = bool(p.get("outreach", True))
    up = p.get("use_places", "auto")
    p["use_places"] = up if up in (True, False) else "auto"
    if not p["category"] or not p["location"]:
        raise ValueError("a business category and a place are needed")
    return p


def describe(p):
    f = {"none": "without a website", "poor": "with a poor website", "none_or_poor": "with no or a poor website",
         "any": ""}[p["website_filter"]]
    return f"{p['count']} {p['category']} in {p['location']} {f}".strip()


def plan(p):
    return [StepSpec("discover", "discover", f"Find {p['category']} in {p['location']}", max_attempts=3),
            StepSpec("rank", "rank", "Rank the opportunities and write the report", depends=["discover", "~research:*"])]


# ---------------------------------------------------------------- steps
def _dedupe(profiles):
    """Same business from two sources / two OSM objects: same phone, or same name within ~100 m."""
    out = []
    for p in profiles:
        dup = None
        for q in out:
            if norm_phone(p.get("phone")) and norm_phone(p.get("phone")) == norm_phone(q.get("phone")):
                dup = q
            elif norm_text(p["name"]) == norm_text(q["name"]) and p.get("lat") and q.get("lat") and \
                    discovery._distance_km(p["lat"], p["lon"], q["lat"], q["lon"]) < 0.1:
                dup = q
            if dup:
                break
        if dup is None:
            out.append(p)
        else:
            for k, v in p.items():
                if k == "sources":
                    dup["sources"] = sorted(set(dup["sources"]) | set(v))
                elif not dup.get(k) and v:
                    dup[k] = v
    return out


def step_discover(ctx, step):
    p = ctx.params
    pool = min(max(p["count"] * 3, 30), 150)
    center, profiles, notes = discovery.discover(p["category"], p["location"], p["radius_km"], pool, p["use_places"],
                                                 cancel=ctx.stop_requested)
    profiles = _dedupe(profiles)[:pool]
    if not profiles:
        return StepResult(False, error="no businesses of that kind were found there (" + "; ".join(notes) + ")")
    ids, new = [], 0
    for prof in profiles:
        lid, created = ctx.store.upsert_lead(prof, ctx.id)
        ids.append(lid)
        new += created
    for n in notes:
        ctx.log(n)
    add = [StepSpec(f"research:{lid}", "research", f"Check {prof['name']}", {"lead_id": lid}, ["discover"], max_attempts=2)
           for lid, prof in zip(ids, profiles)]
    return StepResult(True, {"found": len(ids), "new": new, "center": center, "notes": notes},
                      evidence=f"{len(ids)} businesses from: " + "; ".join(notes), add=add)


def _matches_filter(lead, flt):
    return lead.get("website_status") in FILTERS[flt] and (lead.get("score") or 0) >= MIN_SCORE and lead.get("level") != "excluded"


def step_research(ctx, step):
    s = ctx.store
    lead = s.lead(step["args"]["lead_id"])
    if lead is None:
        return StepResult(False, error="the lead is gone from the database")
    flt = ctx.params["website_filter"]
    if lead.get("researched") and time.time() - lead["researched"] < FRESH_S and lead["website_status"] != "unchecked":
        return _maybe_enough(ctx, lead, flt, StepResult(True, {"reused": True}, evidence=f"researched on "
                             f"{time.strftime('%Y-%m-%d', time.localtime(lead['researched']))}: {lead['website_status']}"))
    cancel = ctx.stop_requested
    analysis, search, sources = {}, None, []
    site = lead.get("website") or ""
    if site and websites.kind_of(site) == "site":
        analysis = websites.analyze(site, lead, cancel)
        status = analysis["status"]
        sources.append(site)
        if status == "broken":  # (the listed address may just be out of date: look for a current one)
            search = websites.find_official_site(lead, cancel)
            if search["status"] == "found" and websites.host(search["website"]) != websites.host(site):
                analysis = websites.analyze(search["website"], lead, cancel)
                status = analysis["status"]
                sources.append(search["website"])
    else:
        search = websites.find_official_site(lead, cancel)
        if search["status"] == "found":
            analysis = websites.analyze(search["website"], lead, cancel)
            status = analysis["status"]
            sources.append(search["website"])
        else:
            status = search["status"]
            if site:  # (the listing pointed at a social page / directory)
                search.setdefault("social" if websites.kind_of(site) == "social" else "directories", []).insert(0, site)
                if status in ("none_found", "possible", "unknown"):
                    status = "social_only" if websites.kind_of(site) == "social" else "directory_only"
    if ctx.stop_requested():
        return StepResult(False, error="stopped")
    fields = {"website_status": status, "researched": time.time()}
    if search and search["status"] == "found" and not lead.get("website"):
        fields["website"] = search["website"]
    elif search and search["status"] == "found" and websites.kind_of(lead.get("website", "")) != "site":
        fields["website"] = search["website"]
    emails = analysis.get("emails") or []
    if emails and not lead.get("email"):
        fields["email"] = emails[0]
    extra = dict(lead.get("extra") or {})
    if search:
        extra["web_search"] = {k: search.get(k) for k in ("query", "social", "directories", "possible") if search.get(k)}
    fields["extra"] = extra
    merged = {**lead, **fields, "analysis": analysis}
    score, level, reasons, confidence = websites.score(merged, analysis, search)
    evidence = (analysis.get("evidence") or []) + ((search or {}).get("evidence") or [])
    fields.update(analysis={**analysis, "evidence": evidence}, score=score, level=level, reasons=reasons,
                  confidence=confidence, missing=websites.missing(merged),
                  sources=sorted(set(lead.get("sources") or []) | set(sources)))
    s.update_lead(lead["id"], **fields)
    s.add_research(lead["id"], ctx.id, "website", {"status": status, "analysis": analysis, "search": search,
                                                   "score": score, "reasons": reasons}, sources)
    lead = s.lead(lead["id"])
    res = StepResult(True, {"status": status, "score": score, "level": level},
                     evidence=f"{lead['name']}: {status}, score {score} ({level}); " + (evidence[0] if evidence else ""))
    return _maybe_enough(ctx, lead, flt, res)


def _maybe_enough(ctx, lead, flt, res):
    """Once enough businesses qualify (plus a margin to choose the best from), skip the remaining research."""
    want = ctx.params["count"]
    good = sum(_matches_filter(x, flt) for x in ctx.store.leads(mission_id=ctx.id, limit=1000) if x.get("researched"))
    if good >= int(want * 1.25) + 1:
        pending = [st["key"] for st in ctx.store.steps(ctx.id) if st["kind"] == "research" and st["state"] == "pending"]
        if pending:
            res.skip = pending
            ctx.log(f"{good} businesses qualify (aim: {want}); skipping the other {len(pending)} checks")
    return res


def step_rank(ctx, step):
    s, p = ctx.store, ctx.params
    flt = p["website_filter"]
    researched = [x for x in s.leads(mission_id=ctx.id, limit=1000) if x.get("researched")]
    fits = sorted((x for x in researched if _matches_filter(x, flt)),
                  key=lambda x: (-(x.get("score") or 0), -(x.get("confidence") or 0), x["name"]))
    chosen = fits[:p["count"]]
    for rank, x in enumerate(chosen, 1):
        extra = dict(x.get("extra") or {})
        extra.setdefault("rank", {})[ctx.id] = rank
        s.update_lead(x["id"], status="qualified" if x["status"] in ("candidate", "not a fit") else x["status"], extra=extra)
    chosen_ids = {x["id"] for x in chosen}
    for x in researched:
        if x["id"] not in chosen_ids and x["status"] == "candidate":
            s.update_lead(x["id"], status="not a fit")
    csv_path, n = s.export(ctx.workspace / "leads.csv", ctx.id, "csv")
    s.export(ctx.workspace / "leads.json", ctx.id, "json")
    report = _report(ctx, chosen, researched)
    (ctx.workspace / "report.md").write_text(report, encoding="utf-8")
    add = []
    for x in chosen[:p["demos"]]:
        add.append(StepSpec(f"demo:{x['id']}", "demo", f"Build a demo site for {x['name']}", {"lead_id": x["id"]}, ["rank"]))
        if p["outreach"]:
            add.append(StepSpec(f"outreach:{x['id']}", "outreach", f"Prepare outreach for {x['name']}",
                                {"lead_id": x["id"]}, [f"demo:{x['id']}"]))
    if not chosen:
        ctx.log("no business matched the filter with enough evidence", "warn")
    return StepResult(True, {"qualified": len(chosen), "researched": len(researched), "report": str(ctx.workspace / "report.md")},
                      evidence=f"{len(chosen)} qualify of {len(researched)} researched; report.md and leads.csv "
                               f"({n} rows) written to {ctx.workspace}", add=add)


def _report(ctx, chosen, researched):
    p = ctx.params
    lines = [f"# {ctx.mission['title']}", "", f"Generated {time.strftime('%Y-%m-%d %H:%M')}. "
             f"{len(researched)} businesses researched, {len(chosen)} qualify (filter: {p['website_filter']}, minimum score "
             f"{MIN_SCORE}).", "",
             "Website status meanings: none_found = none in the listing data and none found by a web search on the date "
             "checked (it may still exist); social_only / directory_only = only social or listing pages found; broken = "
             "the listed site didn't load; poor = loaded but failed several checks.", "",
             "| # | Business | Phone | Website status | Score | Confidence | Missing |", "|---|---|---|---|---|---|---|"]
    for i, x in enumerate(chosen, 1):
        lines.append(f"| {i} | {x['name']} | {x.get('phone') or '-'} | {x['website_status']} | {x.get('score')} "
                     f"({x.get('level')}) | {x.get('confidence')} | {', '.join(x.get('missing') or []) or '-'} |")
    for i, x in enumerate(chosen, 1):
        lines += ["", f"## {i}. {x['name']}", f"- Address: {x.get('address') or 'unknown'}",
                  f"- Phone: {x.get('phone') or 'unknown'} · Email: {x.get('email') or 'none found'}",
                  f"- Website: {x.get('website') or 'none found'} ({x['website_status']})", "- Why it scores "
                  f"{x.get('score')}:"]
        lines += [f"  - +{r['points']}: {r['why']}" for r in (x.get("reasons") or [])]
        ev = (x.get("analysis") or {}).get("evidence") or []
        if ev:
            lines += ["- Evidence:"] + [f"  - {e}" for e in ev[:5]]
        lines.append("- Sources: " + ", ".join(x.get("sources") or []))
    lines += ["", "---", "Business data from public sources; OpenStreetMap data © OpenStreetMap contributors (ODbL). "
              "Verify details with the business before relying on them."]
    return "\n".join(lines) + "\n"


def step_demo(ctx, step):
    s = ctx.store
    lead = s.lead(step["args"]["lead_id"])
    if lead is None:
        return StepResult(False, error="the lead is gone from the database")
    folder, problems = sitegen.build(lead, ctx.workspace)
    if problems:
        return StepResult(False, error="the demo failed its own checks: " + "; ".join(problems))
    proj = s.add_project(lead["id"], ctx.id, folder.name, folder, sitegen.STACK)
    s.project_history(proj["id"], {"what": "generated from the lead's facts", "by": "template"})
    if lead["status"] in ("candidate", "qualified"):
        s.update_lead(lead["id"], status="demo built")
    from room_agent.missions import preview

    return StepResult(True, {"project_id": proj["id"], "folder": str(folder), "preview": preview.url_for(folder)},
                      evidence=f"{folder / 'index.html'} written and checked (name present, 'not official' banner, "
                               "no reviews / prices / external scripts)")


def step_outreach(ctx, step):
    s = ctx.store
    lead = s.lead(step["args"]["lead_id"])
    if lead is None:
        return StepResult(False, error="the lead is gone from the database")
    has_demo = bool(s.projects(lead_id=lead["id"]))
    out, problems = outreach.prepare(lead, ctx.id, ctx.workspace, has_demo)
    for pr in problems:
        ctx.log(f"{lead['name']}: {pr}", "warn")
    if lead["status"] in ("candidate", "qualified", "demo built"):
        s.update_lead(lead["id"], status="outreach drafted")
    files = [Path(out["folder"]) / n for n in ("email.txt", "call-notes.md", "proposal.md")]
    if not all(f.exists() for f in files):
        return StepResult(False, error="the outreach files weren't written")
    return StepResult(True, {**out, "problems": problems},
                      evidence=f"written: {', '.join(f.name for f in files)} in {out['folder']}"
                               + (f"; approval #{out['approval_id']} waits for you (Gmail draft, not sent)" if out["approval_id"] else ""))


def step_edit(ctx, step):
    from room_agent.missions import coder

    s = ctx.store
    proj = next((p for p in s.projects(ctx.id) if p["id"] == step["args"]["project_id"]), None)
    if proj is None:
        return StepResult(False, error="that demo site isn't part of this mission")
    lead = s.lead(proj["lead_id"])
    r = coder.edit(proj["path"], lead, step["args"]["instruction"], cancel=ctx.stop_requested)
    s.project_history(proj["id"], {"what": step["args"]["instruction"][:200], "by": r.get("backend"), "ok": r["ok"],
                                   "changed": r.get("changed"), "problems": r.get("problems")})
    if not r["ok"]:
        why = "; ".join(r.get("problems") or ["the edit didn't work"]) + (" (rolled back)" if r.get("rolled_back") else "")
        engine._announce(f"The change to {lead['name']}'s demo didn't work: {why[:200]}", ctx.id)
        return StepResult(False, error=why)
    engine._announce(f"The change to {lead['name']}'s demo is done. {r.get('summary') or ''}"[:300], ctx.id)
    return StepResult(True, r, evidence=f"changed {', '.join(r['changed'])}; site re-checked against the content rules")


# ---------------------------------------------------------------- reporting
def progress(ctx):
    leads = ctx.store.leads(mission_id=ctx.id, limit=1000)
    flt = ctx.params["website_filter"]
    projects = ctx.store.projects(ctx.id)
    drafts = ctx.store.outreach(ctx.id)
    return {"leads_found": len(leads), "leads_researched": sum(1 for x in leads if x.get("researched")),
            "leads_qualifying": sum(_matches_filter(x, flt) for x in leads if x.get("researched")),
            "target": ctx.params["count"], "demos": len(projects), "drafts": len(drafts),
            "top": [{"id": x["id"], "name": x["name"], "score": x.get("score"), "status": x["website_status"]}
                    for x in leads if x.get("researched") and _matches_filter(x, flt)][:5]}


def status_line(ctx, p):
    state = {"running": "working", "paused": "paused", "paused_budget": "paused (budget used up)",
             "interrupted": "paused (Jarvis restarted)", "completed": "finished", "cancelled": "stopped",
             "finished_with_problems": "finished, with some problems"}.get(p["state"], p["state"])
    parts = [f"The mission '{p['title']}' is {state}."]
    if p.get("leads_found"):
        parts.append(f"{p['leads_found']} businesses found, {p['leads_researched']} checked, "
                     f"{p['leads_qualifying']} qualify so far (aiming for {p['target']}).")
    elif p["state"] == "running":
        parts.append("It's still looking for businesses.")
    if p.get("demos") or p.get("drafts"):
        parts.append(f"{p['demos']} demo site{'s' if p['demos'] != 1 else ''} built, {p['drafts']} outreach draft"
                     f"{'s' if p['drafts'] != 1 else ''} ready.")
    if p.get("current") and p["state"] == "running":
        parts.append(f"Right now: {p['current'].lower()}.")
    if p.get("approvals_pending"):
        parts.append(f"{p['approvals_pending']} item{'s wait' if p['approvals_pending'] != 1 else ' waits'} for your approval.")
    parts.append(f"Spent ${p['spent_usd']:.2f} of ${p['budget_usd']:.2f}.")
    return " ".join(parts)


def summarize(ctx):
    s = ctx.store
    flt = ctx.params["website_filter"]
    leads = s.leads(mission_id=ctx.id, limit=1000)
    qualified = [x for x in leads if x.get("researched") and _matches_filter(x, flt) and x["status"] != "not a fit"]
    m = s.mission(ctx.id)
    projects = s.projects(ctx.id)
    lines = [f"{len(qualified)} of {ctx.params['count']} wanted qualify ({len(leads)} found, "
             f"{sum(1 for x in leads if x.get('researched'))} researched); {len(projects)} demo sites; "
             f"{len(s.outreach(ctx.id))} outreach drafts; {len(s.approvals(ctx.id, 'pending'))} approvals waiting; "
             f"cost ${m['spent_usd']:.2f}.",
             f"Files: {ctx.workspace}"]
    lines += [f"- {x['name']}: {x['website_status']}, score {x.get('score')}" for x in qualified[:5]]
    text = "\n".join(lines)
    (ctx.workspace / "summary.md").write_text(f"# {ctx.mission['title']}\n\n{text}\n", encoding="utf-8")
    return text


WORKFLOW = engine.register(Workflow(
    kind="business", title="Business research & website demos", plan=plan,
    handlers={"discover": step_discover, "research": step_research, "rank": step_rank, "demo": step_demo,
              "outreach": step_outreach, "edit": step_edit},
    describe=describe, summarize=summarize, progress=progress, status_line=status_line,
    timeouts={"discover": 240, "research": 180, "rank": 60, "demo": 60, "outreach": 120,
              "edit": config.CODER_TIMEOUT_S + 30}))


# ---------------------------------------------------------------- what the voice tools call
def start(params, budget_usd=None):
    return engine.create("business", normalize(params), budget_usd)


def build_demos(mid, n=3, lead_ids=None):
    """'Build demos for the best three' -> adds demo (+ outreach) steps for the top leads without a demo yet."""
    s = store()
    m = s.mission(mid)
    if m is None:
        raise ValueError("no such mission")
    p = m["params"]
    have = {pr["lead_id"] for pr in s.projects(mid)}
    if lead_ids:
        pick = [s.lead(i) for i in lead_ids if s.lead(i)]
    else:
        flt = p.get("website_filter", "none_or_poor")
        pick = [x for x in s.leads(mission_id=mid, limit=1000) if x.get("researched") and _matches_filter(x, flt)]
    pick = [x for x in pick if x["id"] not in have][: max(1, min(int(n), 10))]
    specs = []
    for x in pick:
        specs.append(StepSpec(f"demo:{x['id']}", "demo", f"Build a demo site for {x['name']}", {"lead_id": x["id"]}, []))
        if p.get("outreach", True):
            specs.append(StepSpec(f"outreach:{x['id']}", "outreach", f"Prepare outreach for {x['name']}",
                                  {"lead_id": x["id"]}, [f"demo:{x['id']}"]))
    engine.add_steps(mid, specs)
    return pick


def request_edit(mid, project_id, instruction):
    key = f"edit:{project_id}:{int(time.time())}"
    engine.add_steps(mid, [StepSpec(key, "edit", f"Edit demo: {instruction[:60]}",
                                    {"project_id": project_id, "instruction": instruction}, [], idempotent=False,
                                    max_attempts=1)])
    return key
