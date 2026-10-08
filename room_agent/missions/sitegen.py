"""Demo websites for a lead: a real, responsive, editable static site, personalised from facts and owner content.

Layout inside the mission folder (never inside Jarvis):
    sites/<slug>/               the live demo (what the preview shows): index.html, styles.css, site.json, README.md,
                                 art.svg, mark.svg, and owner photos if any
    sites/.versions/<slug>/<t>/ every previous version (a rebuild / edit moves the old folder here: undo = move back)
    sites/.staging/<slug>-<id>/ a new version being built and checked; swapped in only when complete and clean
    owner-content/<slug>/       what the owner gave you: owner.json (+ photos). Never overwritten by Jarvis.

Atomic updates: a new version is written to .staging, checked there (content rules, contrast, local resources only),
marked complete, then swapped in with two directory renames (current -> .versions, staging -> current). A crash
leaves either the old or the new site in place, and recover() finishes or undoes a half-done swap at startup.

Content rules (checked on every version, including the coding worker's):
    - facts (name, address, phone, hours, email, links) only from the lead's stored, sourced fields - never Google
      Places content (see places.py) - and credited in the footer
    - owner content (owner.json) is shown as theirs; anything else that isn't a fact is visibly marked "Sample"
    - no testimonials, ratings, reviews, prices, awards or claims unless they come from owner.json
    - images only from owner-content with "rights_confirmed": true; everything decorative is generated here (SVG)
    - no external scripts, styles, fonts, images or frames; a "not the official website" banner; noindex
Static on purpose: no install step, nothing downloaded, works offline and from a folder.
"""

import html
import json
import logging
import re
import shutil
import time
import uuid
from pathlib import Path

from room_agent import config
from room_agent.missions import design, runctx

log = logging.getLogger("room-agent")
STACK = "static-html"
TEXT_EXT = {".html", ".css", ".json", ".md", ".svg", ".txt"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_EXT = TEXT_EXT | IMAGE_EXT
MAX_TEXT_BYTES = 400_000
MAX_IMAGE_BYTES = 3_000_000
MAX_PHOTOS = 8

FAMILY = {"restaurant": "restaurant", "fast food": "restaurant", "cafe": "cafe", "coffee": "cafe", "bar": "bar",
          "pub": "bar", "bakery": "bakery", "dentist": "health", "doctor": "health", "pharmacy": "health",
          "veterinarian": "health", "salon": "beauty", "barber": "beauty", "beauty": "beauty", "spa": "beauty",
          "nail": "beauty", "plumber": "trade", "electrician": "trade", "carpenter": "trade", "roofer": "trade",
          "painter": "trade", "locksmith": "trade", "auto": "trade", "lawyer": "office", "accountant": "office",
          "real estate": "office", "insurance": "office"}
OFFER = {  # (section title, [sample card headings])
    "restaurant": ("Menu", ["Starters", "Mains", "Desserts"]), "cafe": ("Menu", ["Coffee", "Tea & more", "Pastries"]),
    "bar": ("Drinks & events", ["Cocktails", "Beer & wine", "Events"]),
    "bakery": ("What we bake", ["Breads", "Cakes", "Custom orders"]),
    "health": ("Services", ["Services", "New patients", "Insurance & payment"]),
    "beauty": ("Services", ["Hair", "Treatments", "Booking"]), "trade": ("Services", ["Services", "Areas served", "Quotes"]),
    "office": ("Services", ["Practice areas", "Approach", "Consultations"]),
    "default": ("What we offer", ["Products", "Services", "Contact"]),
}
FORBIDDEN = re.compile(r"testimonial|★|\b\d(\.\d)?\s*(/\s*5|stars?)\b|\breviews?\b|award[- ]winning|\bbest in\b|"
                       r"#1\b|number one|\brated\b", re.I)
PRICE = re.compile(r"[$€£]\s?\d")
OSM_LINK = re.compile(r"^https://www\.openstreetmap\.org/")


def family(category):
    c = (category or "").lower()
    return next((v for k, v in FAMILY.items() if k in c), "default")


def slug(name, lead_id):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40] or "business"
    return f"{s}-{lead_id}"


def _e(x):
    return html.escape(str(x or ""), quote=True)


def _site_name(url):
    return re.sub(r"^https?://(www\.)?", "", str(url)).split("/")[0]


def sites_dir(workspace):
    return Path(workspace) / "sites"


def owner_dir(workspace, s):
    return Path(workspace) / "owner-content" / s


# ---------------------------------------------------------------- owner content (the only source of real menus,
# prices, photos and "about" text)
def load_owner(workspace, s):
    """-> (owner dict (validated), {photo file name: bytes}, [problems]). Missing folder = no owner content."""
    d = owner_dir(workspace, s)
    f = d / "owner.json"
    if not f.exists():
        return {}, {}, []
    problems = []
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, {}, [f"owner.json can't be read ({e.__class__.__name__})"]
    if not isinstance(raw, dict):
        return {}, {}, ["owner.json must be an object"]

    def text(v, n):
        return re.sub(r"\s+", " ", v).strip()[:n] if isinstance(v, str) else ""

    owner = {"tagline": text(raw.get("tagline"), 160), "about": text(raw.get("about"), 1500), "offer": []}
    for group in (raw.get("menu") or raw.get("services") or [])[:8]:
        if not isinstance(group, dict):
            continue
        items = []
        for it in (group.get("items") or [])[:20]:
            if isinstance(it, dict) and text(it.get("name"), 80):
                items.append({"name": text(it.get("name"), 80), "description": text(it.get("description"), 240),
                              "price": text(it.get("price"), 24)})
        if items:
            owner["offer"].append({"title": text(group.get("section") or group.get("title"), 60) or "Menu", "items": items})
    photos = {}
    if raw.get("photos"):
        if raw.get("rights_confirmed") is not True:
            problems.append("owner photos ignored: set \"rights_confirmed\": true in owner.json once you have the rights")
        else:
            owner["photos"] = []
            for i, ph in enumerate((raw.get("photos") or [])[:MAX_PHOTOS], 1):
                if not isinstance(ph, dict):
                    continue
                src = (d / str(ph.get("file") or "")).resolve()
                if d.resolve() not in src.parents or src.suffix.lower() not in IMAGE_EXT or not src.is_file():
                    problems.append(f"photo '{ph.get('file')}' skipped (must be a .jpg / .png / .webp inside {d.name})")
                    continue
                data = src.read_bytes()
                if len(data) > MAX_IMAGE_BYTES:
                    problems.append(f"photo '{ph.get('file')}' skipped (over {MAX_IMAGE_BYTES // 1_000_000} MB)")
                    continue
                name = f"photo-{i}{src.suffix.lower()}"
                photos[name] = data
                owner["photos"].append({"file": name, "alt": text(ph.get("alt"), 140) or "Photo provided by the owner",
                                        "credit": text(ph.get("credit"), 80)})
    owner["hours_confirmed"] = raw.get("hours_confirmed") is True
    return owner, photos, problems


def owner_strings(owner):
    """Text that came from the owner (allowed to contain prices / their own wording)."""
    out = [owner.get("tagline", ""), owner.get("about", "")]
    for g in owner.get("offer") or []:
        out.append(g["title"])
        for it in g["items"]:
            out += [it["name"], it["description"], it["price"]]
    return [x for x in out if x]


# ---------------------------------------------------------------- content model
def demo_blockers(lead):
    """Why a demo can't be built from this lead's stored facts (e.g. a Google-only lead nobody else confirmed)."""
    if str(lead.get("name", "")).startswith("Google place ") or (lead.get("extra") or {}).get("google_only") and \
            "name" not in ((lead.get("extra") or {}).get("verified_by") or {}):
        return ["its name is only known from Google Maps, which may not be stored or reused; a demo needs the business "
                "confirmed by OpenStreetMap or its own website"]
    return []


def content_for(lead, owner, prev_design=None, overrides=None):
    extra = lead.get("extra") or {}
    src = (lead.get("sources") or [""])[0]
    verified = extra.get("verified_by") or {}
    facts = {}
    for k in ("name", "category", "address", "city", "phone", "email", "website"):
        if lead.get(k):
            facts[k] = {"value": lead[k], "source": verified.get(k) or src}
    if extra.get("opening_hours"):
        facts["hours"] = {"value": extra["opening_hours"], "source": src,
                          "note": "confirmed by the owner" if owner.get("hours_confirmed") else
                          "as listed on OpenStreetMap; please confirm"}
    if extra.get("cuisine"):
        facts["cuisine"] = {"value": extra["cuisine"].replace(";", ", ").replace("_", " "), "source": src}
    if lead.get("lat") is not None and lead.get("lon") is not None and "google_only" not in extra:
        facts["map"] = {"value": f"https://www.openstreetmap.org/?mlat={lead['lat']}&mlon={lead['lon']}#map=18/{lead['lat']}/{lead['lon']}",
                        "source": src}
    social = {k: v for k, v in (extra.get("social") or {}).items() if isinstance(v, str) and v.startswith("https://")}
    fam = family(lead.get("category"))
    d = {**(prev_design or {}), **{k: v for k, v in (overrides or {}).items() if v}}
    layout, pal = design.choose(fam, lead["name"], d.get("layout"), d.get("color"))
    return {"generated": time.strftime("%Y-%m-%d %H:%M"), "family": fam, "facts": facts, "social": social,
            "owner": owner, "offer": OFFER[fam], "sources": lead.get("sources") or [],
            "prepared_by": config.MISSION_SENDER_BUSINESS or config.MISSION_SENDER_NAME or "",
            "design": {"layout": layout, "color": d.get("color") or "", "palette": pal, "seed": design.seed(lead["name"])},
            "hours_table": design.parse_hours(extra.get("opening_hours", ""))}


# ---------------------------------------------------------------- rendering
def _kind(cuisine, category):
    """'pizza' + 'pizza restaurant' -> 'pizza restaurant' (not 'pizza pizza restaurant'); 'thai' + 'restaurant' ->
    'thai restaurant'."""
    cuisine, category = (cuisine or "").strip(), (category or "").strip()
    if not cuisine:
        return category
    words = set(re.findall(r"[a-z]+", category.lower()))
    if all(w in words for w in re.findall(r"[a-z]+", cuisine.lower())):
        return category
    return f"{cuisine} {category}".strip()


def _sample(text):
    return f'<p class="sample"><span class="tag">Sample</span> {_e(text)}</p>'


def render_html(c):
    f, o, dz = c["facts"], c["owner"], c["design"]
    name = f["name"]["value"]
    kind = _kind(f.get("cuisine", {}).get("value", ""), f.get("category", {}).get("value", ""))
    eyebrow = " · ".join(x for x in (kind.strip().title(), f.get("city", {}).get("value", "")) if x)
    phone = f.get("phone", {}).get("value", "")
    tel = re.sub(r"[^\d+]", "", phone)
    street = f.get("address", {}).get("value", "").split(",")[0]
    lede = o.get("tagline") or (f"{kind.strip().capitalize()} at {street}." if street and kind.strip() else "")
    actions = []
    if phone:
        actions.append(f'<a class="btn" href="tel:{_e(tel)}">Call {_e(phone)}</a>')
    if f.get("map"):
        actions.append(f'<a class="btn btn-ghost" href="{_e(f["map"]["value"])}" rel="noopener">Directions</a>')
    offer_title, cards = c["offer"]
    nav = [("about", "About"), ("offer", offer_title), ("hours", "Hours"), ("visit", "Visit")]
    if o.get("photos"):
        nav.insert(2, ("photos", "Photos"))

    facts_strip = []
    if f.get("address"):
        facts_strip.append(("Address", _e(f["address"]["value"])))
    if phone:
        facts_strip.append(("Phone", f'<a href="tel:{_e(tel)}">{_e(phone)}</a>'))
    if c.get("hours_table"):
        open_days = [d for d, v in c["hours_table"] if v != "Closed"]
        facts_strip.append(("Open", f"{len(open_days)} days a week" if open_days else "See hours"))
    strip = "".join(f'<div class="fact"><span class="fact-k">{k}</span><span class="fact-v">{v}</span></div>'
                    for k, v in facts_strip)

    about = f"<p>{_e(o['about'])}</p>" if o.get("about") else _sample("A few sentences in the owner's own words about "
                                                                     "the business, its story and what makes it theirs.")
    if o.get("offer"):
        groups = []
        for g in o["offer"]:
            items = "".join(
                f'<li><div class="item-head"><span class="item-name">{_e(it["name"])}</span>'
                + (f'<span class="item-price">{_e(it["price"])}</span>' if it["price"] else "")
                + "</div>" + (f'<p class="item-desc">{_e(it["description"])}</p>' if it["description"] else "") + "</li>"
                for it in g["items"])
            groups.append(f'<div class="card"><h3>{_e(g["title"])}</h3><ul class="items">{items}</ul></div>')
        offer = f'<div class="grid">{"".join(groups)}</div>'
    else:
        offer = '<div class="grid">' + "".join(
            f'<div class="card"><span class="tag">Sample</span><h3>{_e(h)}</h3><p class="muted">The owner\'s own '
            f'{_e(h.lower())} go here, with their descriptions and prices.</p></div>' for h in cards) + "</div>"

    photos = ""
    if o.get("photos"):
        figs = "".join(f'<figure><img src="{_e(p["file"])}" alt="{_e(p["alt"])}" loading="lazy" decoding="async">'
                       + (f'<figcaption>{_e(p["credit"])}</figcaption>' if p["credit"] else "") + "</figure>"
                       for p in o["photos"])
        photos = f'<section id="photos" class="section"><div class="wrap"><h2>Photos</h2><div class="gallery">{figs}</div></div></section>'

    if c.get("hours_table"):
        rows = "".join(f"<tr><th scope=\"row\">{_e(d)}</th><td>{_e(v)}</td></tr>" for d, v in c["hours_table"])
        hours = f'<table class="hours">{rows}</table><p class="note">{_e(f["hours"]["note"])}</p>'
    elif f.get("hours"):
        hours = f'<p>{_e(f["hours"]["value"])}</p><p class="note">{_e(f["hours"]["note"])}</p>'
    else:
        hours = _sample("Opening hours go here once the owner confirms them.")
    where = []
    if f.get("address"):
        where.append(f'<p class="addr">{_e(f["address"]["value"])}</p>')
    if f.get("map"):
        where.append(f'<p><a href="{_e(f["map"]["value"])}" rel="noopener">View on OpenStreetMap</a></p>')
    visit = []
    if phone:
        visit.append(f'<div class="fact"><span class="fact-k">Phone</span><a href="tel:{_e(tel)}">{_e(phone)}</a></div>')
    if f.get("email"):
        visit.append(f'<div class="fact"><span class="fact-k">Email</span><a href="mailto:{_e(f["email"]["value"])}">'
                     f'{_e(f["email"]["value"])}</a></div>')
    for k, u in c.get("social", {}).items():
        visit.append(f'<div class="fact"><span class="fact-k">{_e(k.title())}</span><a href="{_e(u)}" rel="noopener">'
                     f'{_e(_site_name(u))}</a></div>')
    if not visit:
        visit.append(_sample("Contact details go here once the owner confirms them."))
    by = f' by {_e(c["prepared_by"])}' if c.get("prepared_by") else ""
    credits = "Business details from public sources: " + ", ".join(
        '<a href="{}" rel="noopener">{}</a>'.format(_e(u), _e(_site_name(u))) for u in c["sources"][:3] if
        str(u).startswith(("https://", "http://")))
    if any("openstreetmap.org" in u for u in c["sources"]):
        credits += " (© OpenStreetMap contributors, ODbL)"
    navlinks = "".join(f'<a href="#{a}">{_e(t)}</a>' for a, t in nav)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'none'; frame-src 'none'; object-src 'none'">
<title>{_e(name)} – website design preview</title>
<meta name="description" content="Design preview of a website for {_e(name)}. Not the official website.">
<link rel="stylesheet" href="styles.css">
</head>
<body class="layout-{_e(dz['layout'])}">
<div class="preview-banner" role="note">Design preview{by} – <strong>not the official website of {_e(name)}</strong>. Parts marked “Sample” are placeholders for the owner’s own content.</div>
<header class="site-header"><div class="wrap nav">
  <a class="brand" href="#top"><img class="mark" src="mark.svg" alt="" width="36" height="36"><span>{_e(name)}</span></a>
  <nav aria-label="Sections">{navlinks}</nav>
</div></header>
<main id="top">
<section class="hero"><div class="wrap hero-grid">
  <div class="hero-copy">
    <p class="eyebrow">{_e(eyebrow)}</p>
    <h1>{_e(name)}</h1>
    {f'<p class="lede">{_e(lede)}</p>' if lede else ''}
    <div class="actions">{''.join(actions)}</div>
  </div>
  <div class="hero-art"><img src="art.svg" alt="" width="600" height="400"></div>
</div></section>
{f'<section class="facts"><div class="wrap facts-grid">{strip}</div></section>' if strip else ''}
<section id="about" class="section"><div class="wrap narrow"><h2>About</h2>{about}</div></section>
<section id="offer" class="section alt"><div class="wrap"><h2>{_e(offer_title)}</h2>{offer}</div></section>
{photos}
<section id="hours" class="section"><div class="wrap two-col">
  <div><h2>Hours</h2>{hours}</div>
  <div class="card where"><h2>Find us</h2>{''.join(where) or _sample("The address goes here once the owner confirms it.")}</div>
</div></section>
<section id="visit" class="section alt"><div class="wrap"><h2>Get in touch</h2><div class="facts-grid">{''.join(visit)}</div></div></section>
</main>
<footer class="footer"><div class="wrap">
  <p>{credits}. Checked {_e(c["generated"][:10])}.</p>
  <p>This page is a design preview{by}. It is not affiliated with or endorsed by {_e(name)}.</p>
</div></footer>
</body>
</html>
"""


def render_css(c):
    p = c["design"]["palette"]
    lay = c["design"]["layout"]
    display, body = design.FONTS[lay]
    return f""":root {{
  --bg: {p['bg']}; --surface: {p['surface']}; --text: {p['text']}; --muted: {p['muted']};
  --accent: {p['accent']}; --on-accent: {p['on_accent']}; --soft: {p['soft']};
  --radius: {'4px' if lay == 'editorial' else '18px' if lay == 'modern' else '10px'};
  --display: {display};
  --body: {body};
}}
* {{ box-sizing: border-box; }}
html {{ -webkit-text-size-adjust: 100%; scroll-behavior: smooth; }}
body {{ margin: 0; background: var(--bg); color: var(--text); font: 17px/1.65 var(--body); }}
img {{ max-width: 100%; height: auto; display: block; }}
a {{ color: var(--accent); text-underline-offset: 3px; }}
a:focus-visible, .btn:focus-visible {{ outline: 3px solid var(--accent); outline-offset: 3px; }}
.wrap {{ width: min(1120px, 100% - 32px); margin-inline: auto; }}
.narrow {{ max-width: 760px; }}
.preview-banner {{ background: #fff4c2; color: #3d3100; font-size: 14px; text-align: center; padding: 8px 16px; }}
.site-header {{ position: sticky; top: 0; z-index: 5; background: color-mix(in srgb, var(--bg) 92%, transparent);
  backdrop-filter: blur(8px); border-bottom: 1px solid color-mix(in srgb, var(--text) 10%, transparent); }}
.nav {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 64px; flex-wrap: wrap; }}
.brand {{ display: flex; align-items: center; gap: 10px; color: var(--text); text-decoration: none; font: 700 18px/1.2 var(--display); }}
.mark {{ width: 36px; height: 36px; }}
.nav nav {{ display: flex; gap: 18px; flex-wrap: wrap; }}
.nav nav a {{ color: var(--muted); text-decoration: none; font-size: 15px; }}
.nav nav a:hover {{ color: var(--text); }}
.hero {{ padding: clamp(48px, 9vw, 112px) 0; }}
.hero-grid {{ display: grid; grid-template-columns: 1.1fr .9fr; gap: clamp(24px, 5vw, 64px); align-items: center; }}
.hero-art img {{ width: 100%; aspect-ratio: 3 / 2; object-fit: cover; border-radius: var(--radius); background: var(--soft); }}
.eyebrow {{ margin: 0 0 10px; color: var(--muted); letter-spacing: .1em; text-transform: uppercase; font-size: 13px; }}
h1 {{ margin: 0 0 18px; font: 700 clamp(38px, 7vw, 76px)/1.02 var(--display); letter-spacing: -.015em; }}
h2 {{ margin: 0 0 16px; font: 700 clamp(26px, 3.4vw, 36px)/1.15 var(--display); }}
h3 {{ margin: 0 0 8px; font: 700 20px/1.25 var(--display); }}
.lede {{ font-size: clamp(18px, 2.2vw, 21px); color: var(--muted); margin: 0 0 28px; max-width: 34ch; }}
.actions {{ display: flex; flex-wrap: wrap; gap: 12px; }}
.btn {{ display: inline-block; padding: 13px 22px; border-radius: 999px; background: var(--accent); color: var(--on-accent);
  text-decoration: none; font-weight: 650; }}
.btn-ghost {{ background: transparent; color: var(--text); border: 1.5px solid color-mix(in srgb, var(--text) 35%, transparent); }}
.facts {{ border-block: 1px solid color-mix(in srgb, var(--text) 10%, transparent); background: var(--surface); }}
.facts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 18px; padding: 22px 0; }}
.fact {{ display: grid; gap: 2px; }}
.fact-k {{ font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }}
.section {{ padding: clamp(48px, 8vw, 96px) 0; }}
.section.alt {{ background: var(--soft); }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 20px; }}
.card {{ position: relative; background: var(--surface); border-radius: var(--radius); padding: 26px;
  border: 1px solid color-mix(in srgb, var(--text) 8%, transparent); }}
.muted, .note {{ color: var(--muted); }}
.note {{ font-size: 14px; }}
.tag {{ display: inline-block; font-size: 11px; text-transform: uppercase; letter-spacing: .08em; background: #fff4c2;
  color: #3d3100; padding: 2px 8px; border-radius: 999px; vertical-align: middle; }}
.card > .tag {{ position: absolute; top: 14px; right: 14px; }}
.sample {{ color: var(--muted); font-style: italic; }}
.items {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 14px; }}
.item-head {{ display: flex; justify-content: space-between; gap: 12px; font-weight: 650; }}
.item-desc {{ margin: 2px 0 0; color: var(--muted); font-size: 15px; }}
.gallery {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }}
.gallery img {{ aspect-ratio: 4 / 3; object-fit: cover; border-radius: var(--radius); width: 100%; }}
.gallery figcaption {{ font-size: 13px; color: var(--muted); margin-top: 4px; }}
.two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: clamp(24px, 5vw, 56px); align-items: start; }}
.hours {{ border-collapse: collapse; width: 100%; max-width: 420px; }}
.hours th, .hours td {{ text-align: left; padding: 8px 0; border-bottom: 1px solid color-mix(in srgb, var(--text) 10%, transparent); }}
.hours th {{ font-weight: 600; padding-right: 24px; }}
.addr {{ font-size: 18px; }}
.footer {{ padding: 28px 0 48px; color: var(--muted); font-size: 14px; border-top: 1px solid color-mix(in srgb, var(--text) 12%, transparent); }}
.footer p {{ margin: 4px 0; }}
/* layouts */
.layout-editorial .hero {{ text-align: left; }}
.layout-editorial h1 {{ font-weight: 600; }}
.layout-editorial .card {{ border-width: 0 0 0 3px; border-color: var(--accent); }}
.layout-modern .hero {{ background: radial-gradient(900px 380px at 0% 0%, color-mix(in srgb, var(--accent) 16%, transparent), transparent); }}
.layout-modern .card {{ box-shadow: 0 1px 2px rgba(0,0,0,.05), 0 12px 32px rgba(0,0,0,.06); border: 0; }}
.layout-bold .hero {{ background: var(--accent); color: var(--on-accent); }}
.layout-bold .hero .eyebrow, .layout-bold .hero .lede {{ color: var(--on-accent); opacity: .92; }}
.layout-bold .hero .btn {{ background: var(--on-accent); color: var(--accent); }}
.layout-bold .hero .btn-ghost {{ background: transparent; color: var(--on-accent); border-color: var(--on-accent); }}
.layout-bold h1 {{ text-transform: uppercase; letter-spacing: -.02em; }}
@media (max-width: 820px) {{
  .hero-grid, .two-col {{ grid-template-columns: 1fr; }}
  .hero-art {{ order: -1; }}
  .nav nav {{ gap: 12px; }}
}}
@media (max-width: 520px) {{ .card {{ padding: 20px; }} .btn {{ width: 100%; text-align: center; }} }}
@media (prefers-reduced-motion: reduce) {{ html {{ scroll-behavior: auto; }} }}
"""


def render_readme(c, lead, owner_problems):
    f = c["facts"]
    lines = [f"# Design preview: {f['name']['value']}", "",
             "A demo website made from public information. It is **not** the business's official site and must not be "
             "presented as one.", "", f"Design: {c['design']['layout']} layout, accent {c['design']['palette']['accent']}.",
             "", "## Real facts used (with sources)"]
    lines += [f"- {k}: {v['value']} ({v.get('source', '')})" for k, v in f.items()]
    lines += ["", "## Owner content", ("- used: owner-content/" if c["owner"] else "- none yet: ") +
              "put `owner.json` (tagline, about, menu or services, photos + \"rights_confirmed\": true) in "
              "`owner-content/<this site's folder name>/` and rebuild. Only owner content may contain prices or photos."]
    lines += [f"- note: {p}" for p in owner_problems]
    lines += ["", "## Missing information", *([f"- {m}" for m in (lead.get("missing") or [])] or ["- none"]),
              "", "## Editing",
              "- Colours / layout: ask Jarvis (\"make the demo blue\", \"use the bold layout\"): rebuilt from the facts, free.",
              "- Other changes: ask Jarvis to edit it (the coding worker); or edit `index.html` / `styles.css` here.",
              "- Every previous version is kept in `sites/.versions/`.",
              "", "## Rules", "- No testimonials, ratings, prices, awards or claims unless the owner provides them.",
              "- No photos without the rights to use them.", "- Don't publish it without the owner's written permission."]
    return "\n".join(lines) + "\n"


def build_files(lead, workspace, overrides=None):
    """-> ({file name: bytes}, content model, [owner problems]) for a lead. Raises ValueError if it can't be built."""
    blockers = demo_blockers(lead)
    if blockers:
        raise ValueError("; ".join(blockers))
    s = slug(lead["name"], lead["id"])
    owner, photos, owner_problems = load_owner(workspace, s)
    prev = None
    try:
        prev = json.loads((sites_dir(workspace) / s / "site.json").read_text(encoding="utf-8")).get("design")
        prev = {k: prev.get(k) for k in ("layout", "color")} if isinstance(prev, dict) else None
    except (OSError, ValueError, AttributeError):
        pass
    c = content_for(lead, owner, prev, overrides)
    files = {"index.html": render_html(c).encode("utf-8"), "styles.css": render_css(c).encode("utf-8"),
             "site.json": json.dumps(c, indent=1, default=str).encode("utf-8"),
             "README.md": render_readme(c, lead, owner_problems).encode("utf-8"),
             "art.svg": design.art_svg(c["design"]["palette"], c["design"]["seed"], c["design"]["layout"]).encode("utf-8"),
             "mark.svg": design.monogram_svg(lead["name"], c["design"]["palette"]).encode("utf-8")}
    files.update(photos)
    return files, c, owner_problems


# ---------------------------------------------------------------- staging, checking, swapping (atomic versions)
def stage(workspace, s, files):
    """Write a complete candidate version to sites/.staging/. -> its folder (marked complete)."""
    runctx.check()
    d = sites_dir(workspace) / ".staging" / f"{s}-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True)
    for name, data in files.items():
        p = Path(name)
        if p.name != name or p.suffix.lower() not in ALLOWED_EXT or name.startswith("."):
            shutil.rmtree(d, ignore_errors=True)
            raise ValueError(f"'{name}' isn't an allowed site file")
        (d / name).write_bytes(data)
    (d / ".complete").write_text(s, encoding="utf-8")
    return d


def _rename(src, dst, tries=6):
    for i in range(tries):
        try:
            Path(src).rename(dst)
            return
        except PermissionError:  # (Windows: a file in it is open for a moment, e.g. the preview being served)
            if i == tries - 1:
                raise
            time.sleep(0.5)


# ---------------------------------------------------------------- content fingerprints and hand-edit conflicts
class SiteConflict(Exception):
    """The live site was changed by a person since Jarvis's last version: it isn't overwritten."""

    def __init__(self, s, pending):
        super().__init__(f"the demo site '{s}' was changed by hand since Jarvis last updated it; your changes were kept "
                         f"and Jarvis's new version was set aside ({Path(pending).name}): choose 'keep my edits' or "
                         "'use Jarvis's version'")
        self.slug, self.pending = s, Path(pending)


def files_hash(files):
    """A content fingerprint of a site version ({name: bytes})."""
    import hashlib

    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode() + b"\0" + hashlib.sha256(files[name]).digest())
    return h.hexdigest()


def live_hash(folder):
    folder = Path(folder)
    if not folder.is_dir():
        return ""
    return files_hash({p.name: p.read_bytes() for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")})


def _state_file(workspace, s):
    return sites_dir(workspace) / ".versions" / s / "state.json"


def known_hash(workspace, s):
    """The fingerprint of the last version JARVIS put in place ("" for a site from before this was recorded)."""
    try:
        return json.loads(_state_file(workspace, s).read_text(encoding="utf-8")).get("known_hash", "")
    except (OSError, ValueError, AttributeError):
        return ""


def _set_known(workspace, s, h):
    f = _state_file(workspace, s)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({"known_hash": h, "at": time.time()}), encoding="utf-8")
    tmp.replace(f)


def hand_edited(workspace, s):
    """True if the live site differs from the last version Jarvis installed (someone edited it)."""
    known = known_hash(workspace, s)
    live = sites_dir(workspace) / s
    return bool(known) and live.exists() and live_hash(live) != known


def pending_conflicts(workspace):
    """[(slug, pending folder)] new versions set aside because the live site had been edited by hand."""
    d = sites_dir(workspace) / ".pending"
    if not d.exists():
        return []
    return [(p.name.rsplit("-", 1)[0], p) for p in sorted(d.iterdir()) if p.is_dir() and (p / ".complete").exists()]


def resolve_conflict(workspace, s, choice, lead):
    """choice 'keep_mine': the hand-edited site stays and becomes the new baseline; Jarvis's set-aside version is
    deleted. 'use_jarvis': Jarvis's version goes live; the hand-edited one is kept in .versions (nothing is lost).
    -> (ok, message)"""
    pend = [p for slug, p in pending_conflicts(workspace) if slug == s]
    live = sites_dir(workspace) / s
    if choice == "keep_mine":
        _set_known(workspace, s, live_hash(live))
        for p in pend:
            shutil.rmtree(p, ignore_errors=True)
        return True, "kept your edits; they're now the version Jarvis works from"
    if choice == "use_jarvis":
        if not pend:
            return False, "there's no set-aside version to use"
        latest = pend[-1]
        owner, _, _ = load_owner(workspace, s)
        problems = check(latest, lead, owner)
        if problems:
            return False, "the set-aside version no longer passes the checks: " + "; ".join(problems)
        swap_in(workspace, s, latest, "hand edits (kept)", allow_conflict=True)
        for p in pend[:-1]:
            shutil.rmtree(p, ignore_errors=True)
        return True, "Jarvis's version is live; your hand-edited version is kept in the site's versions"
    return False, f"unknown choice '{choice}'"


def swap_in(workspace, s, staged, why, allow_conflict=False):
    """Make `staged` the live site: current -> .versions/<s>/<time>, staged -> current. -> the version folder (or None).
    If the live site was edited by hand since Jarvis's last version, nothing is replaced: the staged version is moved to
    sites/.pending/ and SiteConflict is raised (unless allow_conflict: the user chose Jarvis's version; the hand-edited
    one is still kept in .versions)."""
    runctx.check()
    live = sites_dir(workspace) / s
    if not allow_conflict and hand_edited(workspace, s):
        pend = sites_dir(workspace) / ".pending" / f"{s}-{uuid.uuid4().hex[:8]}"
        pend.parent.mkdir(parents=True, exist_ok=True)
        _rename(staged, pend)
        raise SiteConflict(s, pend)
    vdir = sites_dir(workspace) / ".versions" / s
    vdir.mkdir(parents=True, exist_ok=True)
    version = None
    if live.exists():
        version = vdir / (time.strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"[^a-z0-9]+", "-", why.lower())[:30].strip("-"))
        if version.exists():
            version = version.with_name(version.name + "-" + uuid.uuid4().hex[:4])
        _rename(live, version)
    from room_agent.missions import crashpoints

    if version is not None:  # (an existing site is being replaced: the window where it's briefly absent)
        crashpoints.hit("during_site_swap")
    try:
        _rename(staged, live)
    except Exception:
        if version is not None and not live.exists():
            _rename(version, live)  # (put the old one back: never leave no site)
        raise
    (live / ".complete").unlink(missing_ok=True)
    if version is not None:
        (version / "WHY.txt").write_text(why, encoding="utf-8")
    _set_known(workspace, s, live_hash(live))
    return version


def discard(staged):
    shutil.rmtree(staged, ignore_errors=True)


def recover(workspace):
    """After a crash: finish a swap that got halfway (staging complete, site missing), put back the last version if
    a site went missing, and delete abandoned staging folders. -> [what was done]"""
    done = []
    root = sites_dir(workspace)
    staging = root / ".staging"
    if staging.exists():
        for d in sorted(staging.iterdir()):
            s = d.name.rsplit("-", 1)[0]
            if (d / ".complete").exists() and not (root / s).exists():
                prev = sorted((p for p in (root / ".versions" / s).iterdir() if p.is_dir()), key=lambda p: p.name) \
                    if (root / ".versions" / s).exists() else []
                known = known_hash(workspace, s)
                if prev and known and live_hash(prev[-1]) != known:
                    # the version being replaced had been edited by hand: put it back, set Jarvis's aside (a conflict)
                    _rename(prev[-1], root / s)
                    pend = root / ".pending" / d.name
                    pend.parent.mkdir(parents=True, exist_ok=True)
                    _rename(d, pend)
                    done.append(f"{s}: an interrupted update would have replaced hand edits: your version was put back, "
                                "Jarvis's was set aside (conflict)")
                    continue
                _rename(d, root / s)
                (root / s / ".complete").unlink(missing_ok=True)
                _set_known(workspace, s, live_hash(root / s))
                done.append(f"{s}: finished an interrupted update")
            else:
                shutil.rmtree(d, ignore_errors=True)
                done.append(f"{s}: removed an unfinished build")
    vroot = root / ".versions"
    if vroot.exists():
        for vd in vroot.iterdir():
            if not (root / vd.name).exists():
                vs = sorted(p for p in vd.iterdir() if p.is_dir())
                if vs:
                    _rename(vs[-1], root / vd.name)
                    done.append(f"{vd.name}: restored the last version (the site was missing)")
    return done


def versions(workspace, s):
    vd = sites_dir(workspace) / ".versions" / s
    return sorted((p for p in vd.iterdir() if p.is_dir()), key=lambda p: p.name) if vd.exists() else []


def restore(workspace, s, version_dir, lead):
    """Undo: a copy of an earlier version becomes the live site (the current one is versioned first)."""
    files = {p.name: p.read_bytes() for p in Path(version_dir).iterdir() if p.is_file() and p.name not in ("WHY.txt",)
             and p.suffix.lower() in ALLOWED_EXT}
    staged = stage(workspace, s, files)
    owner, _, _ = load_owner(workspace, s)
    problems = check(staged, lead, owner)
    if problems:
        discard(staged)
        return problems
    swap_in(workspace, s, staged, "before undo")
    return []


def build(lead, workspace, overrides=None):
    """Generate (or regenerate) the demo site. -> (live folder, [problems]). Nothing changes unless the new version
    passes check(); then it's swapped in atomically."""
    files, c, _ = build_files(lead, workspace, overrides)  # (c["owner"]: loaded from owner-content/, trusted)
    s = slug(lead["name"], lead["id"])
    staged = stage(workspace, s, files)
    problems = check(staged, lead, c["owner"])
    if problems:
        discard(staged)
        return sites_dir(workspace) / s, problems
    try:
        swap_in(workspace, s, staged, "before rebuild")
    except SiteConflict as e:  # (hand edits on the live site: kept; the rebuild waits in .pending for a decision)
        return sites_dir(workspace) / s, [str(e)]
    return sites_dir(workspace) / s, []


def fact_strings(lead):
    """The lead's stored, sourced facts as they can appear on the page (from the database, never from site.json)."""
    extra = lead.get("extra") or {}
    out = [lead.get(k) for k in ("name", "address", "city", "category", "phone", "email", "website")]
    out += [part.strip() for part in str(lead.get("address") or "").split(",")]  # (the page also shows the street alone)
    cuisine = (extra.get("cuisine") or "").replace(";", ", ").replace("_", " ")
    out += [cuisine, _kind(cuisine, lead.get("category", "")), extra.get("opening_hours")]
    out += [v for v in (extra.get("social") or {}).values() if isinstance(v, str)]
    return [str(x) for x in out if x]


def check(folder, lead, owner=None):
    """The site's own rules, read back from disk. -> [problems] (empty = fine).
    owner: the TRUSTED owner content (sitegen.load_owner from owner-content/, which only you write). It is never read
    from the site's own site.json: a site (or the coding worker editing it) can't authorize its own prices, menus or
    claims. None = no owner content (strictest)."""
    folder = Path(folder)
    problems = []
    try:
        page = (folder / "index.html").read_text(encoding="utf-8")
        css = (folder / "styles.css").read_text(encoding="utf-8")
    except OSError as e:
        return [f"the site's files can't be read ({e.__class__.__name__})"]
    owner = owner or {}
    if html.escape(lead["name"]) not in page and lead["name"] not in page:
        problems.append("the business name isn't on the page")
    if "not the official website" not in page.lower():
        problems.append("the 'not the official website' banner is missing")
    if 'name="viewport"' not in page:
        problems.append("no mobile viewport")
    if "noindex" not in page:
        problems.append("the page isn't marked noindex")
    body = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)))
    # the owner's own words may name prices etc.; sourced facts (a name like "Top Rated Plumbing", an address with
    # "#1") are facts, not claims. Both are removed before the check; anything else must pass it.
    for s in sorted(set(owner_strings(owner)) | set(fact_strings(lead)), key=len, reverse=True):
        body = re.sub(re.escape(s), " ", body, flags=re.I)
    hit = FORBIDDEN.search(body)
    if hit:
        problems.append(f"contains review / rating / award wording ('{hit.group(0)}') that didn't come from the owner")
    if PRICE.search(body):
        problems.append("contains a price that didn't come from the owner")
    for tag_ in re.findall(r"<(?:script|iframe|object|embed|frame|base)\b[^>]*>", page, re.I):
        problems.append(f"contains a disallowed element ({tag_[:40]})")
    if re.search(r"\son\w+\s*=", page, re.I) or "javascript:" in page.lower():
        problems.append("contains script (event handlers / javascript: links)")
    for ref in re.findall(r"""(?:src|srcset|poster)\s*=\s*["']([^"']+)""", page, re.I) + \
            re.findall(r"""<link\b[^>]*href\s*=\s*["']([^"']+)""", page, re.I) + \
            re.findall(r"""url\(\s*["']?([^"')]+)""", css, re.I) + re.findall(r"@import\s+[\"']?([^\"';]+)", css, re.I):
        ref = ref.strip()
        if ref.startswith("data:image/svg+xml") or ref.startswith("#"):
            continue
        if re.match(r"^[a-z][a-z0-9+.-]*:", ref, re.I) or ref.startswith("//") or "/" in ref or "\\" in ref:
            problems.append(f"loads a resource from outside the site folder ({ref[:60]})")
        elif not (folder / ref).is_file():
            problems.append(f"refers to a missing file ({ref[:60]})")
    for name in (p.name for p in folder.iterdir() if p.is_file() and p.name != ".complete"):
        if Path(name).suffix.lower() not in ALLOWED_EXT:
            problems.append(f"'{name}' isn't an allowed kind of file in a demo site")
    if any(p.is_dir() for p in folder.iterdir()):
        problems.append("the site folder contains sub-folders (demo sites are flat)")
    try:
        pal = json.loads((folder / "site.json").read_text(encoding="utf-8"))["design"]["palette"]
        problems += design.palette_problems(pal)
    except (OSError, ValueError, KeyError, TypeError):
        problems.append("site.json is missing its design palette")
    return problems
