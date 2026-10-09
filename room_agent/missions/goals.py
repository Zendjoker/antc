"""Goals: what the user asked a mission to achieve, in a structured form that code can check.

    interpret(text)   the user's own words -> Goal: which existing capability it needs, the business-mission parameters,
                      success criteria, constraints, permissions, deliverables, budget / time limits, and the questions
                      that MUST be asked (a missing category or place). Deterministic (no model call): nothing is
                      invented - a value not in the words is either a documented default (listed under `defaults`, never
                      presented as their preference) or a question.
    start(goal)       a business mission from a goal (only when nothing must be asked); the goal is stored with it
    evaluate(mid)     each success criterion against the database and the files (never against what a model said)
    explain(mid)      what was understood, the plan, what it's doing, what changed and why, what's done / failed /
                      waiting for approval - for the dashboard and (shortened) for voice

Goals live in missions.db (table goals: the goal, its criteria results, the decision log, the replanning count), next
to the mission they belong to. Untrusted content (pages, search results) never enters a goal: only the user's words and
the database's verified facts do.
"""

import re
import time
from dataclasses import asdict, dataclass, field

from room_agent import config

NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
       "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "a dozen": 12,
       "a couple of": 2, "a few": 3}
N = r"(\d{1,3}|" + "|".join(sorted((re.escape(k) for k in NUM), key=len, reverse=True)) + r")"
GENERIC = {"businesses", "business", "local businesses", "companies", "local companies", "places", "shops", "stores",
           "small businesses", "local shops", "clients", "leads", "prospects", "customers"}
STOP = r"(?=\s+(?:that|which|who|whose|with|without|lacking|lacks?|having|missing|using|and|but|so|then|to)\b|[.;!?]|,|$)"
VERB = r"\b(?:find|finding|look(?:ing)? for|search(?:ing)? for|prospect(?:ing)?|get|list|discover|locate|research)\s+(?:me\s+)?"
CAPABILITY_TOOLS = {"business_mission": "start_business_mission", "research": "research_web", "desktop_task": "run_task",
                    "site_edit": "edit_demo_site", "email_draft": "decide_mission_approval", "unsupported": ""}
ROUTES = [
    ("unsupported", re.compile(r"\b(hack|crack|break into|ddos|steal|phish|spam)\b|\bsend\b[^.]*\b(e-?mails?|messages?)\b[^.]*"
                               r"\b(\d{3,}|every|everyone|all|mass|bulk|right now)\b", re.I)),
    ("email_draft", re.compile(r"\b(approve|reject)\b[^.]*\b(drafts?|e-?mails?|number|#\s?\d)|\bgmail\b|\bin my drafts\b", re.I)),
    # (an EXISTING demo: "the ... demo" / "X's demo"; "make 2 demo sites" is new work, not an edit)
    ("site_edit", re.compile(r"\b(make|change|edit|update|turn|recolou?r|redesign)\s+(?:the|[\w']+'s)\b[^.]*\bdemo\b"
                             r"(?!\s+(?:web)?sites\b)|\b(?:the|[\w']+'s)\s+demo( site)?\b[^.]*\b"
                             r"(blue|green|red|darker|lighter|bold|layout|footer|header|font)\b", re.I)),
    ("business_mission", re.compile(VERB + r"[^.]*\b(web ?sites?|sites?|demos?|leads?|prospects?|outreach|online "
                                           r"presence|facebook page)\b|\bdemo (web)?sites? for\b|\bprospect\b", re.I)),
    ("research", re.compile(r"\b(research|look up|lookup|news|compare|what (is|are)|who (is|are)|find out|opening hours|"
                            r"how (much|many|does))\b", re.I)),
    ("desktop_task", re.compile(r"\b(open|close|launch|move|maximi[sz]e|minimi[sz]e|play|pause|resume|mute|volume|"
                                r"monitor|window|screenshot)\b", re.I)),
]


@dataclass
class Goal:
    text: str
    capability: str = "conversation"
    tool: str = ""
    objective: str = ""
    params: dict = field(default_factory=dict)        # for the business workflow (business.normalize)
    success_criteria: list = field(default_factory=list)   # [{id, what, metric, target}]
    constraints: list = field(default_factory=list)   # the user's own limits, as said
    resources: list = field(default_factory=list)     # what will be used (data sources, workers)
    permissions: list = field(default_factory=list)   # [{what, how}] what needs a yes, and how it's given
    deliverables: list = field(default_factory=list)
    budget_usd: float = None
    deadline_s: float = None
    defaults: list = field(default_factory=list)      # values NOT stated by the user (documented defaults)
    questions: list = field(default_factory=list)     # [{field, ask}] must be answered before anything starts
    learned: list = field(default_factory=list)       # hints from earlier VERIFIED outcomes (experience), labelled

    def to_dict(self):
        return asdict(self)


def _num(s):
    s = (s or "").strip().lower()
    return int(s) if s.isdigit() else NUM.get(s)


def route(text):
    t = " ".join((text or "").split())
    for cap, rx in ROUTES:
        if rx.search(t):
            return cap
    return "conversation"


def _clean_cat(c):
    c = re.sub(r"^(?:the|some|all|any)\s+", "", c.strip(" ,.").lower())
    c = re.sub(r"\s+(?:in|near|around)$", "", c)
    return c


def _business_fields(t, answers):
    low = t.lower()
    p, defaults, questions = {}, [], []
    cat = loc = None
    n = None
    for rx in (VERB + rf"(?:{N}\s+)?(?P<cat>[a-z][a-z' &-]*?)\s+(?:in|near|around|within \d+ ?km of)\s+(?P<loc>[^,.;!?]+?(?:,\s*[A-Z]{{2}}\b)?){STOP}",
               r"\bdemo (?:web)?sites? for\s+(?:the best\s+)?" + rf"(?:{N}\s+)?(?P<cat>[a-z][a-z' &-]*?)\s+(?:in|near|around)\s+(?P<loc>[^,.;!?]+?(?:,\s*[A-Z]{{2}}\b)?){STOP}",
               VERB + rf"(?:{N}\s+)?(?P<cat>[a-z][a-z' &-]*?)\s+(?:that|which|who|with|without|lacking|lacks?|missing|having)\b"):
        m = re.search(rx, t, re.I)
        if m:
            cat = _clean_cat(m.group("cat"))
            loc = m.groupdict().get("loc")
            n = _num(m.group(1)) if m.group(1) else None
            break
    if cat and cat not in GENERIC and not re.fullmatch(r"local\s+\w+", cat or "x"):
        p["category"] = cat
    if loc:
        p["location"] = loc.strip(" ,.")
    if n:
        p["count"] = n
    m = re.search(rf"\b{N}\s+(?:new\s+)?demo", low) or re.search(rf"\bdemos?\s+(?:sites?\s+)?for\s+(?:the\s+)?best\s+{N}", low) \
        or re.search(rf"\bdemo (?:web)?sites? for\s+(?:the best\s+)?{N}\b", low)
    if re.search(r"\bno demos?\b|\bwithout (?:any )?demos?\b|\bdon'?t (?:build|make) (?:any )?demos?\b", low):
        p["demos"] = 0
    elif m:
        p["demos"] = _num(m.group(1))
    if re.search(r"\bno\s+or\s+(?:a\s+)?(?:poor|bad|weak)\b|\bnone or (?:a )?poor\b", low):
        p["website_filter"] = "none_or_poor"
    elif re.search(r"\b(no|without|lack(?:s|ing)?|don'?t have|doesn'?t have|missing|have no)\s+(?:a\s+|any\s+)?(?:verified\s+)?"
                   r"(?:web ?sites?|sites?|online presence)\b|\bonly (?:a |have a )?(?:facebook|instagram|social)", low):
        p["website_filter"] = "none"
    elif re.search(r"\b(bad|poor|weak|outdated|old|ugly|terrible|broken)\s+web ?sites?\b|\bneeds? a (?:new|better) web ?site", low):
        p["website_filter"] = "poor"
    if re.search(r"\b(don'?t|do not|no|without|never)\s+(?:write\s+|send\s+|prepare\s+|draft\s+|make\s+)?(?:any\s+)?"
                 r"(?:e-?mails?|outreach|drafts?)\b|\bjust the list\b|\bonly the list\b", low):
        p["outreach"] = False
    elif re.search(r"\b(outreach|drafts?|e-?mails?|reach out|contact them)\b", low):
        p["outreach"] = True
    m = re.search(r"\$\s?(\d+(?:\.\d+)?)", t) or re.search(r"\b(\d+(?:\.\d+)?)\s*(?:dollars|bucks|usd)\b", low)
    if m:
        p["budget_usd"] = float(m.group(1))
    if re.search(r"\b(don'?t|do not|without|no|never)\s+(?:use\s+|using\s+)?google\b", low):
        p["use_places"] = False
    elif re.search(r"\b(using|use|with|from)\s+google\b|\bgoogle (data|maps|places)\b", low):
        p["use_places"] = True
    for k, v in (answers or {}).items():
        if v not in (None, ""):
            p[k] = v
    if "location" not in p and re.search(r"\blocal\b|\bnear me\b|\baround here\b", low):
        home = _home_location()
        if home:
            p["location"] = home
            defaults.append(f"location: your home location from memory ({home})")
    if "category" not in p:
        questions.append({"field": "category", "ask": "Which kind of business should I look for (for example restaurants, "
                                                      "plumbers, hair salons)?"})
    if "location" not in p:
        questions.append({"field": "location", "ask": "Which town or area should I search?"})
    for k, v, why in (("count", 20, "how many businesses"), ("website_filter", "none_or_poor", "no or a poor website"),
                      ("demos", 3, "demo sites"), ("outreach", True, "outreach drafts")):
        if k not in p:
            defaults.append(f"{why}: the default ({v})")
    return p, defaults, questions


def _home_location():
    """Their home location as stored in long-term memory (what they told Jarvis), or None (then it's asked)."""
    try:
        from room_agent import runtime as rt

        return (rt.memory.home_location() or None) if rt.memory.available else None
    except Exception:  # noqa: BLE001 (no memory here: the place is asked instead)
        return None


def interpret(text, answers=None):
    """The user's words (+ answers to earlier questions) -> Goal. Never a model call; never an invented value."""
    t = " ".join((text or "").split())
    g = Goal(text=t, capability=route(t))
    g.tool = CAPABILITY_TOOLS.get(g.capability, "")
    if g.capability != "business_mission":
        g.objective = t
        return g
    p, g.defaults, g.questions = _business_fields(t, answers)
    from room_agent.missions import business

    if not g.questions:
        try:
            norm = business.normalize({k: v for k, v in p.items() if k != "budget_usd"})
        except (ValueError, TypeError) as e:
            g.questions.append({"field": "category", "ask": str(e)})
            norm = None
    else:
        norm = None
    g.params = dict(p) if norm is None else {**norm, **({"budget_usd": p["budget_usd"]} if "budget_usd" in p else {})}
    g.budget_usd = p.get("budget_usd")
    if norm:
        _learn(g)
        _fill_business(g)
    return g


def from_params(text, params, budget_usd=None):
    """A goal from parameters already confirmed elsewhere (the voice tool's arguments), with the user's words kept."""
    from room_agent.missions import business

    g = Goal(text=" ".join((text or "").split()), capability="business_mission", tool=CAPABILITY_TOOLS["business_mission"])
    g.params = business.normalize(params)
    g.budget_usd = budget_usd
    _fill_business(g)
    return g


def _fill_business(g):
    """Criteria, deliverables, resources, permissions and constraints of a business goal (g.params normalized)."""
    from room_agent.missions import business

    q = g.params
    g.objective = business.describe(q)
    g.success_criteria = [{"id": "qualified", "what": f"{q['count']} businesses that match '{q['website_filter']}' "
                           "chosen, each with evidence", "metric": "qualified", "target": q["count"]}]
    if q["demos"]:
        g.success_criteria.append({"id": "demos", "what": f"{q['demos']} demo sites that pass the site checks",
                                   "metric": "demos_verified", "target": q["demos"]})
        if q["outreach"]:
            g.success_criteria.append({"id": "drafts", "what": f"{q['demos']} outreach drafts written (not sent)",
                                       "metric": "drafts", "target": q["demos"]})
    g.success_criteria.append({"id": "nothing_sent", "what": "no email sent and nothing published",
                               "metric": "sent", "target": 0})
    g.deliverables = ["report.md and leads.csv (ranked, with evidence)"] + (
        [f"{q['demos']} demo sites (local previews, not published)"] if q["demos"] else []) + (
        ["outreach drafts: email, call notes, proposal (files; nothing sent)"] if q["demos"] and q["outreach"] else [])
    g.resources = ["OpenStreetMap (free)", "web search + the businesses' own sites (free)", "site generator (free)"]
    if q["use_places"] is True or (q["use_places"] == "auto" and _places_enabled()):
        g.resources.append("Google Places (paid per request, within the mission budget)")
    if config.MISSION_LLM_COPY:
        g.resources.append("a light model polishes outreach wording (paid, within the budget)")
    g.permissions = [{"what": "putting an email in Gmail as a draft", "how": "your approval, one draft at a time "
                                                                            "(never sent)"},
                     {"what": "spending", "how": f"up to the mission budget (${(g.budget_usd or config.MISSION_DEFAULT_BUDGET_USD):.2f})"}]
    g.constraints = [c for c in (
        "no outreach drafts" if q["outreach"] is False else "",
        "no demo sites" if q["demos"] == 0 else "",
        "don't use Google data" if q["use_places"] is False else "",
        f"spend at most ${g.budget_usd:.2f}" if g.budget_usd else "",
        "nothing is sent or published (drafts only)") if c]


def _places_enabled():
    try:
        from room_agent.missions import places

        return places.enabled()
    except Exception:  # noqa: BLE001
        return False


def _learn(g):
    """A wider starting radius when an earlier mission for the same kind of business in the same place VERIFIABLY
    needed one (recorded by remember(), from the database's results). A hint, labelled as such; never a preference."""
    try:
        from room_agent.missions.store import store

        key = f"goal-knowledge:{g.params['category'].lower()}|{g.params['location'].lower()}"
        k = store().cache_get(key, 180 * 86400) or {}
    except Exception:  # noqa: BLE001
        return
    r = k.get("radius_km_needed")
    if r and r > g.params["radius_km"]:
        g.params["radius_km"] = float(min(r, 25.0))
        g.learned.append(f"starting radius {r:g} km: a mission on {k.get('when', 'an earlier date')} needed it to find "
                         f"enough (verified result)")


def remember(mid):
    """After a mission: keep what was VERIFIED and reusable (the radius that was actually needed). Nothing from pages."""
    from room_agent.missions.store import store

    s = store()
    m = s.mission(mid)
    row = s.goal(mid)
    if m is None or row is None:
        return
    p = m["params"]
    radius = max([float(st["args"].get("radius_km") or 0) for st in s.steps(mid) if st["kind"] == "discover"
                  and st["state"] == "completed"] + [float(p.get("radius_km") or 0)])
    met = {c["id"]: c["met"] for c in (row.get("criteria") or [])}
    if met.get("qualified") and radius > float(p.get("radius_km") or 0) - 1e-9:
        s.cache_put(f"goal-knowledge:{p['category'].lower()}|{p['location'].lower()}",
                    {"radius_km_needed": radius, "when": time.strftime("%Y-%m-%d"), "mission": mid})


def start(goal, budget_usd=None):
    """A business mission from an interpreted goal. Raises ValueError if something must still be asked."""
    if goal.capability != "business_mission":
        raise ValueError(f"this goal needs '{goal.tool or goal.capability}', not a business mission")
    if goal.questions:
        raise ValueError("first: " + " ".join(q["ask"] for q in goal.questions))
    from room_agent.missions import business

    params = {k: v for k, v in goal.params.items() if k != "budget_usd"}
    return business.start(params, budget_usd if budget_usd is not None else goal.budget_usd, goal=goal.to_dict())


# ---------------------------------------------------------------- checking the criteria (facts only)
def measure(mid):
    """The deliverables that VERIFIABLY exist now. -> {qualified, demos_verified, drafts, sent}"""
    from pathlib import Path

    from room_agent.missions import business, sitegen
    from room_agent.missions.store import store

    s = store()
    m = s.mission(mid)
    if m is None:
        return {}
    ws = Path(m["workspace"])
    flt = m["params"].get("website_filter", "none_or_poor")
    ranked = [x for x in s.leads(mission_id=mid, limit=1000) if mid in ((x.get("extra") or {}).get("rank") or {})]
    qualified = [x for x in ranked if business._matches_filter(x, flt)]
    demos = 0
    demo_leads = set()
    for proj in s.projects(mid):
        folder = Path(proj["path"])
        lead = s.lead(proj["lead_id"])
        owner, _, _ = sitegen.load_owner(ws, folder.name)
        if (folder / "index.html").exists() and lead and not sitegen.check(folder, lead, owner):
            demos += 1
            demo_leads.add(proj["lead_id"])
    drafts = 0
    for o in s.outreach(mid):
        lead = s.lead(o["lead_id"])
        if o["kind"] == "email" and lead and (ws / "outreach" / sitegen.slug(lead["name"], lead["id"]) / "email.txt").exists():
            drafts += 1
    sent = sum(1 for a in s.approvals(mid) if a["action"] != "gmail_draft" and a["status"] == "done")
    return {"qualified": len(qualified), "demos_verified": demos, "drafts": drafts, "sent": sent,
            "demo_leads": sorted(demo_leads)}


def evaluate(mid, save=True):
    """Each success criterion -> met or not, with the measured value. Stored with the goal."""
    from room_agent.missions.store import store

    s = store()
    row = s.goal(mid)
    if row is None:
        return []
    got = measure(mid)
    out = []
    for c in (row["goal"] or {}).get("success_criteria") or []:
        v = got.get(c["metric"])
        met = v is not None and (v <= c["target"] if c["metric"] == "sent" else v >= c["target"])
        out.append({**c, "got": v, "met": bool(met)})
    if save:
        s.update_goal(mid, criteria=out, status="met" if out and all(c["met"] for c in out) else "open")
    return out


# ---------------------------------------------------------------- failure classification (for self-correction)
def classify(error, step=None):
    """-> (kind, retry_safe). kinds: transient, verification, budget, cancelled, uncertain, permanent."""
    e = (error or "").lower()
    if "budget" in e:
        return "budget", False
    if any(w in e for w in ("stopped", "paused", "emergency", "cancel")):
        return "cancelled", bool(step and step.get("idempotent"))
    if "may have" in e or "uncertain" in e or "not repeated" in e:
        return "uncertain", False
    if "verification failed" in e:
        return "verification", bool(step and step.get("idempotent"))
    if any(w in e for w in ("timed out", "timeout", "connection", "didn't answer", "temporar", "503", "502", "network",
                            "failed (")):
        return "transient", bool(step and step.get("idempotent"))
    return "permanent", False


def explain(mid, short=False):
    """What was understood, the plan, what it's doing, what changed and why, done / failed / waiting. Facts only."""
    from room_agent.missions.store import store

    s = store()
    m = s.mission(mid)
    if m is None:
        return {}
    row = s.goal(mid) or {}
    g = row.get("goal") or {}
    crit = evaluate(mid, save=False) if row else []
    steps = s.steps(mid)
    kinds = {}
    for st in steps:
        k = kinds.setdefault(st["kind"], {"total": 0, "done": 0, "failed": 0, "running": 0})
        k["total"] += 1
        k["done"] += st["state"] in ("completed", "skipped")
        k["failed"] += st["state"] in ("failed", "blocked", "uncertain")
        k["running"] += st["state"] == "running"
    running = [st["title"] for st in steps if st["state"] == "running"]
    failed = [{"step": st["title"], "why": st["error"][:200], "kind": classify(st["error"], st)[0]}
              for st in steps if st["state"] in ("failed", "blocked", "uncertain")]
    waiting = [a["summary"][:120] for a in s.approvals(mid, "pending")] + [
        f"#{a['id']} outcome unknown: check Gmail" for a in s.approvals(mid, "unknown")]
    spend = {}
    for c in s.charges(mid, 5000):
        k = spend.setdefault(f"{c['provider']}:{c['model'] or '-'}", {"calls": 0, "usd": 0.0, "by_basis": {}})
        k["calls"] += 1
        k["usd"] = round(k["usd"] + (c["actual_usd"] or 0.0), 6)
        b = c.get("basis") or ("estimate" if c["state"] == "uncertain" else "pending")
        k["by_basis"][b] = k["by_basis"].get(b, 0) + 1
    out = {"understood": g.get("objective") or m["title"], "criteria": crit, "spend": spend,
           "constraints": g.get("constraints") or [], "defaults": g.get("defaults") or [], "learned": g.get("learned") or [],
           "plan": kinds, "now": running, "changes": [d for d in (row.get("decisions") or []) if d.get("type") == "replan"],
           "decisions": row.get("decisions") or [], "failed": failed[:10], "waiting_for_you": waiting[:10],
           "state": m["state"], "replans": row.get("replans", 0)}
    if short:
        met = sum(1 for c in crit if c["met"])
        bits = [f"Goal: {out['understood']}.", f"{met} of {len(crit)} success criteria met" + (
            " (" + "; ".join(f"{c['id']} {c['got']}/{c['target']}" for c in crit if c["metric"] != "sent") + ")." if crit else ".")]
        if running:
            bits.append(f"Now: {running[0]}.")
        if out["changes"]:
            bits.append(f"Plan changed {len(out['changes'])}x: {out['changes'][-1]['why']}.")
        if failed:
            bits.append(f"{len(failed)} step(s) failed or blocked.")
        if waiting:
            bits.append(f"{len(waiting)} item(s) wait for your approval.")
        return " ".join(bits)
    return out
