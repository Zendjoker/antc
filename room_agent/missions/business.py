"""The 'business' mission: find local businesses with no / a poor website, research them, rank them, build demo sites and
prepare outreach for the best ones. Category- and city-agnostic (restaurants in San Francisco, dentists in Austin...).

Plan (steps run by missions/engine.py, checkpointed in missions.db):
    discover               find candidates (OpenStreetMap; Google Places if configured) -> one research step each.
                           Re-running it (a resumed mission) reuses the businesses already found: no second paid search.
    research:<lead>        verify the website status (their listed site, else a web search), check its quality, collect
                           a public email from their own site, score it with reasons; stops early once enough qualify.
                           Google-only businesses: facts are stored only once their own website confirms them.
    rank                   pick the best `count` that match the website filter; write leads.csv / leads.json / report.md
    demo:<lead>            build a demo site for the top `demos` (staged, checked, swapped in atomically)
    outreach:<lead>        email draft + call notes + proposal; one approval request (ever) to put the email in Gmail as
                           a draft
    redesign:<project>:<n> colour / layout change, rebuilt from the facts (free, no model)
    edit:<project>:<n>     a free-form change by the sandboxed coding worker (paid; never repeated automatically)
Every step is safe to resume: leads are deduplicated (key, place id, phone, name + address) under one lock, research
rows are keyed per mission + lead, drafts are updated in place, approvals are created at most once per draft, and
sites change only by an atomic swap. Code decides everything here; models are only used (optionally) for wording and
for site edits you ask for.
"""

import hashlib
import logging
import re
import time
from pathlib import Path

from room_agent import config
from room_agent.missions import discovery, engine, outreach, places, runctx, sitegen, websites
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
GOOGLE_PLACEHOLDER = "[Google Maps data]"


def normalize(params):
    p = dict(params or {})
    p["category"] = str(p.get("category") or "").strip()[:60]
    p["location"] = str(p.get("location") or "").strip()[:120]
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


def _write(path, text):
    runctx.check()
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def display_name(lead):
    """The name to show / speak: the stored one, or (Google-only, unconfirmed) the in-memory Google one, attributed."""
    if str(lead.get("name", "")).startswith("Google place ") and lead.get("place_id"):
        c = places.recall(lead["place_id"])
        if c and c.get("name"):
            return f"{c['name']} (Google Maps)"
    return lead.get("name", "")


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


def _research_steps(leads):
    return [StepSpec(f"research:{x['id']}", "research", f"Check {x['name']}", {"lead_id": x["id"]}, ["discover"],
                     max_attempts=2) for x in leads]


def step_discover(ctx, step):
    """Each batch (OpenStreetMap, then every paid Google page) is stored the moment it arrives and the progress is
    checkpointed, so a run cut off by a budget pause / restart resumes without buying the same pages again."""
    p = ctx.params
    cp_key = f"discover:{ctx.id}"
    cp = ctx.store.cache_get(cp_key, 10 ** 9) or {}
    known = ctx.store.leads(mission_id=ctx.id, limit=1000)
    if known and (cp.get("done") or not cp):  # (finished before, or stored by an older version: reuse, don't pay)
        return StepResult(True, {"found": len(known), "reused": True},
                          evidence=f"{len(known)} businesses already found by this mission (not searched again)",
                          add=_research_steps(known))
    pool = min(max(p["count"] * 3, 30), 150)
    counts = {"new": 0}

    def store_batch(profiles, progress):
        # (a batch that arrived was already paid for: it's recorded with the plain store even if a pause lands right
        # now, so the checkpoint never lags behind what was bought and nothing is bought twice)
        raw = store()
        for prof in _dedupe(profiles):
            _, created = raw.upsert_lead(prof, ctx.id)
            counts["new"] += created
        cp.update(progress)
        raw.cache_put(cp_key, cp)
        ctx.check()

    center, _, notes = discovery.discover(p["category"], p["location"], p["radius_km"], pool, p["use_places"],
                                          cancel=ctx.stop_requested, on_batch=store_batch, resume=cp)
    cp["done"] = True
    ctx.store.cache_put(cp_key, cp)
    for n in notes:
        ctx.log(n)
    leads = ctx.store.leads(mission_id=ctx.id, limit=1000)
    if not leads:
        return StepResult(False, error="no businesses of that kind were found there (" + "; ".join(notes) + ")")
    return StepResult(True, {"found": len(leads), "new": counts["new"], "center": center, "notes": notes},
                      evidence=f"{len(leads)} businesses from: " + "; ".join(notes), add=_research_steps(leads))


def _matches_filter(lead, flt):
    return lead.get("website_status") in FILTERS[flt] and (lead.get("score") or 0) >= MIN_SCORE and lead.get("level") != "excluded"


def _check_website(view, cancel):
    """-> (status, analysis, search, sources) for the business described by `view` (stored or in-memory facts)."""
    analysis, search, sources = {}, None, []
    site = view.get("website") or ""
    if site and websites.kind_of(site) == "site":
        analysis = websites.analyze(site, view, cancel)
        status = analysis["status"]
        sources.append(site)
        if status == "broken":  # (the listed address may just be out of date: look for a current one)
            search = websites.find_official_site(view, cancel)
            if search["status"] == "found" and websites.host(search["website"]) != websites.host(site):
                analysis = websites.analyze(search["website"], view, cancel)
                status = analysis["status"]
                sources.append(search["website"])
    else:
        search = websites.find_official_site(view, cancel)
        if search["status"] == "found":
            analysis = websites.analyze(search["website"], view, cancel)
            status = analysis["status"]
            sources.append(search["website"])
        else:
            status = search["status"]
            if site:  # (the listing pointed at a social page / directory)
                search.setdefault("social" if websites.kind_of(site) == "social" else "directories", []).insert(0, site)
                if status in ("none_found", "possible", "unknown"):
                    status = "social_only" if websites.kind_of(site) == "social" else "directory_only"
    return status, analysis, search, sources


def _scrub(obj, values):
    """Replace Google-only values (not confirmed elsewhere) inside what's about to be stored."""
    if isinstance(obj, str):
        for v in values:
            obj = obj.replace(v, GOOGLE_PLACEHOLDER)
        return obj
    if isinstance(obj, list):
        return [_scrub(x, values) for x in obj]
    if isinstance(obj, dict):
        return {k: _scrub(v, values) for k, v in obj.items()}
    return obj


def step_research(ctx, step):
    s = ctx.store
    lead = s.lead(step["args"]["lead_id"])
    if lead is None:
        return StepResult(False, error="the lead is gone from the database", final=True)
    flt = ctx.params["website_filter"]
    if lead.get("researched") and time.time() - lead["researched"] < FRESH_S and lead["website_status"] != "unchecked":
        return _maybe_enough(ctx, lead, flt, StepResult(True, {"reused": True}, evidence=f"researched on "
                             f"{time.strftime('%Y-%m-%d', time.localtime(lead['researched']))}: {lead['website_status']}"))
    google_only = bool((lead.get("extra") or {}).get("google_only"))
    view, google = dict(lead), {}
    if google_only:
        google = places.content(lead["place_id"]) or {}  # (memory, or a paid Place Details request)
        if not google:
            return StepResult(False, error="this business was found on Google Maps, and its details can't be fetched "
                                           "again (no Places key?)", final=True)
        if google.get("status") in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
            s.update_lead(lead["id"], level="excluded", score=0, status="not a fit", researched=time.time(),
                          reasons=[{"points": 0, "why": "listed as closed (Google Maps)"}])
            return StepResult(True, {"status": "closed"}, evidence="listed as closed (Google Maps): excluded")
        view.update({k: google.get(k) or "" for k in ("name", "phone", "address", "website")})
    status, analysis, search, sources = _check_website(view, ctx.stop_requested)
    ctx.check()
    fields = {"website_status": status, "researched": time.time()}
    extra = dict(lead.get("extra") or {})
    if google_only:
        # store only what a non-Google source (the business's own site, loaded by us) confirms
        site_url = analysis.get("final_url") if analysis.get("status") in ("ok", "poor") else ""
        conf = analysis.get("confirms") or {}
        verified = dict(extra.get("verified_by") or {})
        if site_url and (conf.get("phone") or conf.get("address")):
            verified["website"] = site_url
            fields["website"] = site_url
            for f in ("phone", "address", "name"):
                if conf.get(f) and view.get(f):
                    verified[f] = site_url
                    fields[f] = view[f]
        extra["verified_by"] = verified
        unconfirmed = [view[f] for f in ("name", "phone", "address", "website") if view.get(f) and f not in verified]
    else:
        unconfirmed = []
        if search and search["status"] == "found" and (not lead.get("website") or
                                                       websites.kind_of(lead.get("website", "")) != "site"):
            fields["website"] = search["website"]
            extra["verified_by"] = {**(extra.get("verified_by") or {}),
                                    "website": "web search; the site shows their phone / address"}
    emails = analysis.get("emails") or []
    if google_only and "website" not in (extra.get("verified_by") or {}):
        emails = []  # (a site Google pointed to that nothing confirms is theirs: its email isn't kept either)
    if emails and not lead.get("email"):
        fields["email"] = emails[0]  # (from the business's own site: not Google content)
        extra["verified_by"] = {**(extra.get("verified_by") or {}),
                                "email": analysis.get("contact_page") or analysis.get("final_url") or "their website"}
    if search:
        extra["web_search"] = {k: search.get(k) for k in ("query", "social", "directories", "possible") if search.get(k)}
    fields["extra"] = extra
    merged = {**view, **fields, "analysis": analysis}
    score, level, reasons, confidence = websites.score(merged, analysis, search)
    evidence = (analysis.get("evidence") or []) + ((search or {}).get("evidence") or [])
    stored_analysis = {k: v for k, v in {**analysis, "evidence": evidence}.items() if k != "confirms"}
    fields.update(analysis=stored_analysis, score=score, level=level, reasons=reasons, confidence=confidence,
                  missing=websites.missing({**lead, **fields}), sources=sorted(set(lead.get("sources") or []) | set(sources)))
    if unconfirmed:
        fields = _scrub(fields, sorted(set(unconfirmed), key=len, reverse=True))
        search = _scrub(search, unconfirmed)
        analysis = _scrub(stored_analysis, unconfirmed)
        sources = _scrub(sources, unconfirmed)
    s.update_lead(lead["id"], **fields)
    s.add_research(lead["id"], ctx.id, "website", {"status": status, "analysis": fields["analysis"], "search": search,
                                                   "score": score, "reasons": fields["reasons"]}, sources,
                   ikey=f"{ctx.id}:{lead['id']}:website")
    lead = s.lead(lead["id"])
    ev = fields["analysis"].get("evidence") or []
    res = StepResult(True, {"status": status, "score": score, "level": level},
                     evidence=f"{lead['name']}: {status}, score {score} ({level}); " + (ev[0] if ev else ""))
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


def _demo_specs(leads, outreach_too):
    specs = []
    for x in leads:
        specs.append(StepSpec(f"demo:{x['id']}", "demo", f"Build a demo site for {x['name']}", {"lead_id": x["id"]}, []))
        if outreach_too:
            specs.append(StepSpec(f"outreach:{x['id']}", "outreach", f"Prepare outreach for {x['name']}",
                                  {"lead_id": x["id"]}, [f"demo:{x['id']}"]))
    return specs


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
    _, n = s.export(ctx.workspace / "leads.csv", ctx.id, "csv")
    s.export(ctx.workspace / "leads.json", ctx.id, "json")
    _write(ctx.workspace / "report.md", _report(ctx, chosen, researched))
    buildable = [x for x in chosen if not sitegen.demo_blockers(x)]
    if len(buildable) < len(chosen[:p["demos"]]):
        ctx.log("some top businesses are only known from Google Maps (no other source confirms them), so no demo is "
                "built for them", "warn")
    add = _demo_specs(buildable[:p["demos"]], p["outreach"])
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
             "Businesses named \"Google place ...\" were found on Google Maps and nothing else confirmed their details; "
             "Google's terms don't allow storing them, so they're shown live on the dashboard instead.", "",
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
        lines.append("- Sources: " + (", ".join(x.get("sources") or []) or "-"))
    lines += ["", "---", "Business data from public sources; OpenStreetMap data © OpenStreetMap contributors (ODbL). "
              "Verify details with the business before relying on them."]
    return "\n".join(lines) + "\n"


def step_demo(ctx, step):
    s = ctx.store
    lead = s.lead(step["args"]["lead_id"])
    if lead is None:
        return StepResult(False, error="the lead is gone from the database", final=True)
    try:
        folder, problems = sitegen.build(lead, ctx.workspace)
    except ValueError as e:
        return StepResult(False, error=f"no demo for this business: {e}", final=True)
    if problems:
        return StepResult(False, error="the demo failed its own checks (nothing was changed): " + "; ".join(problems),
                          final=True)
    proj = s.add_project(lead["id"], ctx.id, folder.name, folder, sitegen.STACK)
    s.project_history(proj["id"], {"what": "generated from the lead's facts", "by": "template"})
    if lead["status"] in ("candidate", "qualified"):
        s.update_lead(lead["id"], status="demo built")
    from room_agent.missions import preview

    url = preview.url_for(folder)  # ("" if MISSIONS_DIR changed since: the site is still built and checked)
    return StepResult(True, {"project_id": proj["id"], "folder": str(folder), "preview": url,
                             "open": url or str(folder / "index.html")},
                      evidence=f"{folder / 'index.html'} built in staging, checked (name, 'not official' banner, noindex, "
                               "no reviews / prices / external resources, contrast) and swapped in")


def step_redesign(ctx, step):
    s = ctx.store
    proj = next((p for p in s.projects(ctx.id) if p["id"] == step["args"]["project_id"]), None)
    if proj is None:
        return StepResult(False, error="that demo site isn't part of this mission", final=True)
    lead = s.lead(proj["lead_id"])
    overrides = {k: step["args"].get(k) for k in ("layout", "color") if step["args"].get(k)}
    try:
        folder, problems = sitegen.build(lead, ctx.workspace, overrides)
    except ValueError as e:
        return StepResult(False, error=str(e), final=True)
    if problems:
        return StepResult(False, error="the new design failed its checks (nothing was changed): " + "; ".join(problems),
                          final=True)
    store().project_history(proj["id"], {"what": f"redesign {overrides}", "by": "template"})
    engine._announce(f"{lead['name']}'s demo has the new look.", ctx.id)
    return StepResult(True, {"folder": str(folder), **overrides}, evidence=f"rebuilt with {overrides}, checked, swapped in",
                      committed=True)


def step_outreach(ctx, step):
    s = ctx.store
    lead = s.lead(step["args"]["lead_id"])
    if lead is None:
        return StepResult(False, error="the lead is gone from the database", final=True)
    if sitegen.demo_blockers(lead):
        return StepResult(False, error="no outreach: this business's details are only known from Google Maps", final=True)
    has_demo = bool(s.projects(lead_id=lead["id"]))
    out, problems = outreach.prepare(lead, ctx.id, ctx.workspace, has_demo, s=s)
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
        return StepResult(False, error="that demo site isn't part of this mission", final=True)
    lead = s.lead(proj["lead_id"])
    r = coder.edit(proj["path"], lead, step["args"]["instruction"], workspace=ctx.workspace)
    entry = {"what": step["args"]["instruction"][:200], "by": r.get("backend"), "ok": r["ok"],
             "changed": r.get("changed"), "problems": r.get("problems")}
    if not r["ok"]:
        s.project_history(proj["id"], entry)
        why = "; ".join(r.get("problems") or ["the edit didn't work"])
        engine._announce(f"The change to {lead['name']}'s demo wasn't applied: {why[:200]}", ctx.id)
        return StepResult(False, error=why + " (the site is unchanged)", final=True)
    # The new version is live (swapped in). From here on nothing may turn that into a "failure": the bookkeeping uses
    # the plain store (a pause right now must not block recording what already happened) and the result is committed.
    store().project_history(proj["id"], entry)
    engine._announce(f"The change to {lead['name']}'s demo is done. {r.get('summary') or ''}"[:300], ctx.id)
    return StepResult(True, r, evidence=f"changed {', '.join(r['changed'])} in a sandbox copy; checked; swapped in",
                      committed=True)


# ---------------------------------------------------------------- reporting / recovery
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
    state = {"running": "working", "paused": "paused", "paused_budget": "paused (the mission's budget is used up)",
             "paused_daily": "paused (today's overall model budget is used up; the mission's own budget isn't)",
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
    if p.get("approvals_unknown"):
        parts.append(f"{p['approvals_unknown']} Gmail draft{'s' if p['approvals_unknown'] != 1 else ''} may or may not "
                     "have been created: check Gmail's Drafts.")
    parts.append(f"Spent ${p['spent_usd']:.2f} of ${p['budget_usd']:.2f}" +
                 (f" (${p['uncertain_usd']:.2f} of it counted at full estimate because the provider's charge was unclear)."
                  if p.get("uncertain_usd") else "."))
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
    _write(ctx.workspace / "summary.md", f"# {ctx.mission['title']}\n\n{text}\n")
    return text


def recover(m):
    """At startup: finish / undo half-done demo-site swaps in this mission's folder."""
    ws = Path(m["workspace"])
    return [f"demo sites: {x}" for x in sitegen.recover(ws)] if (ws / "sites").exists() else []


WORKFLOW = engine.register(Workflow(
    kind="business", title="Business research & website demos", plan=plan,
    handlers={"discover": step_discover, "research": step_research, "rank": step_rank, "demo": step_demo,
              "outreach": step_outreach, "edit": step_edit, "redesign": step_redesign},
    describe=describe, summarize=summarize, progress=progress, status_line=status_line, recover=recover,
    timeouts={"discover": 300, "research": 180, "rank": 60, "demo": 60, "redesign": 60, "outreach": 120,
              "edit": config.CODER_TIMEOUT_S + 60}))


# ---------------------------------------------------------------- what the voice tools / dashboard call
def start(params, budget_usd=None):
    return engine.create("business", normalize(params), budget_usd)


def build_demos(mid, n=3, lead_ids=None):
    """'Build demos for the best three' -> adds demo (+ outreach) steps for the top leads without a demo yet.
    Re-asking adds nothing twice (step keys are per lead)."""
    s = store()
    m = s.mission(mid)
    if m is None:
        raise ValueError("no such mission")
    p = m["params"]
    have = {pr["lead_id"] for pr in s.projects(mid)}
    queued = {st["args"].get("lead_id") for st in s.steps(mid) if st["kind"] == "demo" and st["state"] in ("pending",
                                                                                                         "running")}
    if lead_ids:
        pick = [s.lead(i) for i in lead_ids if s.lead(i)]
    else:
        flt = p.get("website_filter", "none_or_poor")
        pick = sorted((x for x in s.leads(mission_id=mid, limit=1000) if x.get("researched") and _matches_filter(x, flt)),
                      key=lambda x: (-(x.get("score") or 0), -(x.get("confidence") or 0)))
    pick = [x for x in pick if x["id"] not in have and x["id"] not in queued and not sitegen.demo_blockers(x)]
    pick = pick[: max(1, min(int(n), 10))]
    if m["state"] == "cancelled":
        raise engine.MissionClosed("that mission was stopped for good; start a new one")
    if not pick:
        return pick, {"added": 0, "state": m["state"], "running": m["state"] == "running", "why_not": ""}
    return pick, engine.add_steps(mid, _demo_specs(pick, p.get("outreach", True)))


def _same_pending(mid, kind, project_id, sig):
    return any(st["kind"] == kind and st["state"] in ("pending", "running") and st["args"].get("project_id") == project_id
               and st["args"].get("sig") == sig for st in store().steps(mid))


def request_edit(mid, project_id, instruction):
    """Queue a coding-worker edit. The same request already waiting / running isn't queued again.
    -> engine.add_steps' result, or None if it was already queued. Raises engine.MissionClosed for a stopped mission."""
    sig = hashlib.sha1(re.sub(r"\s+", " ", instruction.lower()).strip().encode()).hexdigest()[:10]
    if _same_pending(mid, "edit", project_id, sig):
        return None
    key = f"edit:{project_id}:{sig}:{int(time.time())}"
    return engine.add_steps(mid, [StepSpec(key, "edit", f"Edit demo: {instruction[:60]}",
                                           {"project_id": project_id, "instruction": instruction, "sig": sig}, [],
                                           idempotent=False, max_attempts=1)])


def request_redesign(mid, project_id, layout=None, color=None):
    sig = f"{layout or ''}:{color or ''}"
    if _same_pending(mid, "redesign", project_id, sig):
        return None
    key = f"redesign:{project_id}:{int(time.time())}"
    return engine.add_steps(mid, [StepSpec(key, "redesign", f"New look for demo ({layout or ''} {color or ''})".strip(),
                                           {"project_id": project_id, "layout": layout, "color": color, "sig": sig}, [],
                                           max_attempts=2)])
