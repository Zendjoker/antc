"""Demo websites for a lead: a real, responsive, editable static site made only from the facts we have.

Layout of a project (inside the mission folder, never inside Jarvis):
    sites/<slug>/index.html     the page (semantic HTML, works on phones, no external scripts, no tracking)
                 styles.css     the design (CSS variables for the palette, so "make it blue" is a one-line change)
                 site.json      the content it was built from, with each fact's source
                 README.md      what's real, what's sample, how to edit / preview
                 .versions/     a copy of the previous version before every rebuild or edit (undo)

Content rules (enforced here, and re-checked by coder.py after any edit):
    - facts (name, address, phone, hours, email) only from the lead's sources, credited in the footer
    - anything else is visibly marked as sample text for the owner to replace
    - no testimonials, ratings, reviews, prices, awards or claims; no photos (no rights to any); no logo imitation
    - a banner says it's a design preview, not the business's official site; pages are marked noindex
Why static and not Next.js: a static site runs from a folder with no install step and nothing to download, so it can be
built, previewed and checked offline. (The README explains how to move it to a framework if the owner wants one.)
"""

import html
import json
import logging
import re
import shutil
import time
from pathlib import Path

from room_agent import config

log = logging.getLogger("room-agent")
STACK = "static-html"

PALETTES = {  # (bg, surface, text, muted, accent, accent text)
    "restaurant": ("#fbf7f2", "#ffffff", "#2b2118", "#6b5d50", "#b5452b", "#ffffff"),
    "cafe": ("#f7f3ee", "#ffffff", "#2e241d", "#6e6259", "#7a4b2a", "#ffffff"),
    "bar": ("#14161b", "#1d2027", "#f1ede6", "#a8a39a", "#d9a441", "#14161b"),
    "bakery": ("#fff8f0", "#ffffff", "#3a2a1c", "#7a6655", "#c8793a", "#ffffff"),
    "health": ("#f4f8fb", "#ffffff", "#15283a", "#56687a", "#1f7a8c", "#ffffff"),
    "beauty": ("#fbf5f6", "#ffffff", "#2d1f24", "#76626a", "#a34e6b", "#ffffff"),
    "trade": ("#f5f6f7", "#ffffff", "#1c2329", "#5c6670", "#e07b00", "#ffffff"),
    "office": ("#f6f7f9", "#ffffff", "#18202b", "#5b6573", "#2f5d9e", "#ffffff"),
    "default": ("#f7f7f5", "#ffffff", "#1f2328", "#5f6670", "#2d6a4f", "#ffffff"),
}
FAMILY = {"restaurant": "restaurant", "fast food": "restaurant", "cafe": "cafe", "coffee": "cafe", "bar": "bar",
          "pub": "bar", "bakery": "bakery", "dentist": "health", "doctor": "health", "pharmacy": "health",
          "veterinarian": "health", "salon": "beauty", "barber": "beauty", "beauty": "beauty", "spa": "beauty",
          "nail": "beauty", "plumber": "trade", "electrician": "trade", "carpenter": "trade", "roofer": "trade",
          "painter": "trade", "locksmith": "trade", "auto": "trade", "lawyer": "office", "accountant": "office",
          "real estate": "office", "insurance": "office"}
SECTIONS = {  # what a visitor looks for, per family: (heading, sample text) - always shown as SAMPLE
    "restaurant": [("Our menu", "Sample section: the menu goes here, with the owner's own dishes and prices."),
                   ("About us", "Sample text: a few sentences in the owner's words about the restaurant's story.")],
    "cafe": [("Menu", "Sample section: drinks, pastries and the owner's prices go here."),
             ("About", "Sample text: what makes this cafe theirs, in the owner's words.")],
    "bar": [("Drinks & events", "Sample section: the drinks list and upcoming events go here."),
            ("About", "Sample text: the bar's story, in the owner's words.")],
    "bakery": [("What we bake", "Sample section: the owner's breads, cakes and order details go here."),
               ("About", "Sample text: the bakery's story, in the owner's words.")],
    "health": [("Services", "Sample section: the practice's services go here, as the practice describes them."),
               ("New patients", "Sample text: how to book a first visit and what to bring.")],
    "beauty": [("Services", "Sample section: services and the owner's prices go here."),
               ("Book a visit", "Sample text: how to book, in the owner's words.")],
    "trade": [("Services", "Sample section: the jobs they take on and the areas they cover go here."),
              ("Get a quote", "Sample text: how to ask for a quote, in the owner's words.")],
    "office": [("Services", "Sample section: the firm's areas of work go here, as the firm describes them."),
               ("About", "Sample text: who they are, in their own words.")],
    "default": [("What we do", "Sample section: the business's products or services go here."),
                ("About", "Sample text: the business's story, in the owner's words.")],
}
FORBIDDEN = re.compile(r"testimonial|★|\b\d(\.\d)?\s*(/\s*5|stars?)\b|\breviews?\b|award[- ]winning|\bbest in\b|"
                       r"#1\b|number one", re.I)


def family(category):
    c = (category or "").lower()
    return next((v for k, v in FAMILY.items() if k in c), "default")


def slug(name, lead_id):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40] or "business"
    return f"{s}-{lead_id}"


def content_for(lead):
    """The site's content: facts with their source, and sample sections clearly marked."""
    extra = lead.get("extra") or {}
    src = (lead.get("sources") or [""])[0]
    facts = {}
    for k in ("name", "category", "address", "city", "phone", "email"):
        if lead.get(k):
            facts[k] = {"value": lead[k], "source": src}
    if extra.get("opening_hours"):
        facts["hours"] = {"value": extra["opening_hours"], "source": src, "note": "as listed on OpenStreetMap; confirm with the owner"}
    if extra.get("cuisine"):
        facts["cuisine"] = {"value": extra["cuisine"].replace(";", ", ").replace("_", " "), "source": src}
    if lead.get("lat") and lead.get("lon"):
        facts["map"] = {"value": f"https://www.openstreetmap.org/?mlat={lead['lat']}&mlon={lead['lon']}#map=18/{lead['lat']}/{lead['lon']}",
                        "source": src}
    fam = family(lead.get("category"))
    return {"generated": time.strftime("%Y-%m-%d %H:%M"), "family": fam, "facts": facts,
            "sample_sections": [{"heading": h, "text": t, "sample": True} for h, t in SECTIONS[fam]],
            "sources": lead.get("sources") or [],
            "prepared_by": config.MISSION_SENDER_BUSINESS or config.MISSION_SENDER_NAME or "",
            "palette": dict(zip(("bg", "surface", "text", "muted", "accent", "on_accent"), PALETTES[fam]))}


def _e(x):
    return html.escape(str(x or ""), quote=True)


def _site_name(url):
    return re.sub(r"^https?://(www\.)?", "", str(url)).split("/")[0]


def render_html(c):
    f = c["facts"]
    name = f["name"]["value"]
    tagline = " · ".join(x for x in (f.get("cuisine", {}).get("value", "").title() or f.get("category", {}).get("value", "").title(),
                                      f.get("city", {}).get("value", "")) if x)
    phone = f.get("phone", {}).get("value", "")
    tel = re.sub(r"[^\d+]", "", phone)
    actions = []
    if phone:
        actions.append(f'<a class="btn" href="tel:{_e(tel)}">Call {_e(phone)}</a>')
    if f.get("map"):
        actions.append(f'<a class="btn btn-ghost" href="{_e(f["map"]["value"])}" rel="noopener">Directions</a>')
    visit = []
    if f.get("address"):
        visit.append(f'<p><strong>Address</strong><br>{_e(f["address"]["value"])}</p>')
    if phone:
        visit.append(f'<p><strong>Phone</strong><br><a href="tel:{_e(tel)}">{_e(phone)}</a></p>')
    if f.get("email"):
        visit.append(f'<p><strong>Email</strong><br><a href="mailto:{_e(f["email"]["value"])}">{_e(f["email"]["value"])}</a></p>')
    if f.get("hours"):
        visit.append(f'<p><strong>Hours</strong><br>{_e(f["hours"]["value"])}<br><small>{_e(f["hours"]["note"])}</small></p>')
    if not visit:
        visit.append('<p class="sample">Sample: contact details go here once the owner confirms them.</p>')
    sections = "\n".join(
        f'<section class="card"><span class="tag">Sample</span><h2>{_e(s["heading"])}</h2><p>{_e(s["text"])}</p></section>'
        for s in c["sample_sections"])
    by = f' by {_e(c["prepared_by"])}' if c.get("prepared_by") else ""
    credits = "Business details from public sources: " + ", ".join(
        '<a href="{}" rel="noopener">{}</a>'.format(_e(u), _e(_site_name(u))) for u in c["sources"][:3])
    if any("openstreetmap.org" in u for u in c["sources"]):
        credits += " (© OpenStreetMap contributors)"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{_e(name)} – website design preview</title>
<meta name="description" content="Design preview of a website for {_e(name)}. Not the official website.">
<link rel="stylesheet" href="styles.css">
</head>
<body>
<div class="preview-banner" role="note">Design preview{by} – <strong>not the official website of {_e(name)}</strong>. Sections marked “Sample” are placeholders for the owner’s own content.</div>
<header class="hero">
  <div class="wrap">
    <p class="eyebrow">{_e(tagline)}</p>
    <h1>{_e(name)}</h1>
    <div class="actions">{''.join(actions)}</div>
  </div>
</header>
<main class="wrap">
  <div class="grid">
{sections}
  </div>
  <section class="card visit" id="visit">
    <h2>Visit &amp; contact</h2>
    <div class="visit-grid">{''.join(visit)}</div>
  </section>
</main>
<footer class="wrap footer">
  <p>{credits}. Checked {_e(c["generated"][:10])}.</p>
  <p>This page is a design preview{by}. It is not affiliated with or endorsed by {_e(name)}.</p>
</footer>
</body>
</html>
"""


def render_css(c):
    p = c["palette"]
    return f""":root {{
  --bg: {p['bg']};
  --surface: {p['surface']};
  --text: {p['text']};
  --muted: {p['muted']};
  --accent: {p['accent']};
  --on-accent: {p['on_accent']};
  --radius: 14px;
  --font: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --display: Georgia, "Times New Roman", serif;
}}
* {{ box-sizing: border-box; }}
html {{ -webkit-text-size-adjust: 100%; }}
body {{ margin: 0; background: var(--bg); color: var(--text); font: 17px/1.6 var(--font); }}
a {{ color: var(--accent); }}
.wrap {{ width: min(1080px, 100% - 32px); margin-inline: auto; }}
.preview-banner {{ background: #fff4c2; color: #4a3b00; font-size: 14px; text-align: center; padding: 8px 16px; }}
.hero {{ padding: clamp(56px, 12vw, 128px) 0 clamp(40px, 8vw, 88px);
  background: radial-gradient(1200px 400px at 10% -10%, color-mix(in srgb, var(--accent) 22%, transparent), transparent),
              linear-gradient(180deg, color-mix(in srgb, var(--accent) 8%, var(--bg)), var(--bg)); }}
.eyebrow {{ margin: 0 0 8px; color: var(--muted); letter-spacing: .08em; text-transform: uppercase; font-size: 13px; }}
h1 {{ margin: 0 0 24px; font: 700 clamp(36px, 7vw, 68px)/1.05 var(--display); letter-spacing: -.01em; }}
h2 {{ margin: 0 0 8px; font: 700 24px/1.2 var(--display); }}
.actions {{ display: flex; flex-wrap: wrap; gap: 12px; }}
.btn {{ display: inline-block; padding: 12px 20px; border-radius: 999px; background: var(--accent); color: var(--on-accent);
  text-decoration: none; font-weight: 600; }}
.btn-ghost {{ background: transparent; color: var(--text); border: 1.5px solid color-mix(in srgb, var(--text) 30%, transparent); }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 20px; margin: 40px 0 20px; }}
.card {{ position: relative; background: var(--surface); border-radius: var(--radius); padding: 28px;
  box-shadow: 0 1px 2px rgba(0,0,0,.06), 0 8px 24px rgba(0,0,0,.05); }}
.card p {{ margin: 0; color: var(--muted); }}
.tag {{ position: absolute; top: 14px; right: 14px; font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
  background: #fff4c2; color: #4a3b00; padding: 2px 8px; border-radius: 999px; }}
.visit {{ margin: 0 0 40px; }}
.visit-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-top: 12px; }}
.visit-grid p {{ color: var(--text); }}
.visit-grid small {{ color: var(--muted); }}
.sample {{ font-style: italic; }}
.footer {{ padding: 24px 0 48px; color: var(--muted); font-size: 14px; border-top: 1px solid color-mix(in srgb, var(--text) 12%, transparent); }}
.footer p {{ margin: 4px 0; }}
@media (max-width: 520px) {{ .card {{ padding: 22px; }} .btn {{ width: 100%; text-align: center; }} }}
"""


def render_readme(c, lead):
    f = c["facts"]
    lines = [f"# Design preview: {f['name']['value']}", "",
             "A demo website made from public information. It is **not** the business's official site and must not be "
             "presented as one.", "", "## Real facts used (with sources)"]
    for k, v in f.items():
        lines.append(f"- {k}: {v['value']} ({v.get('source', '')})")
    lines += ["", "## Sample content (to be replaced by the owner's own)"]
    lines += [f"- {s['heading']}: {s['text']}" for s in c["sample_sections"]]
    lines += ["", "## Missing information", *([f"- {m}" for m in (lead.get("missing") or [])] or ["- none"]),
              "", "## Editing", "- Content: `index.html` (plain HTML). Colours / fonts: the variables at the top of `styles.css`.",
              "- Or ask Jarvis: \"make the demo for this business blue\" (the previous version is kept in `.versions/`).",
              "- Preview: open `index.html` in a browser, or ask Jarvis to show the preview.",
              "- To move it to a framework (Next.js, Astro...), copy the sections into components; no build step is needed as is.",
              "", "## Rules", "- No testimonials, ratings, prices, awards or claims unless the owner provides them.",
              "- No photos without the rights to use them (the owner's own, or properly licensed).",
              "- Don't publish it without the owner's written permission."]
    return "\n".join(lines) + "\n"


def snapshot(folder, why):
    """Copy the current files to .versions/<time>/ before changing them. -> the snapshot path, or None (nothing yet)."""
    folder = Path(folder)
    files = [p for p in folder.iterdir() if p.is_file()] if folder.exists() else []
    if not files:
        return None
    dest = folder / ".versions" / (time.strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"[^a-z0-9]+", "-", why.lower())[:30])
    dest.mkdir(parents=True, exist_ok=True)
    for p in files:
        shutil.copy2(p, dest / p.name)
    (dest / "WHY.txt").write_text(why, encoding="utf-8")
    return dest


def restore(folder, version_dir):
    folder, version_dir = Path(folder), Path(version_dir)
    snapshot(folder, "before undo")
    for p in version_dir.iterdir():
        if p.is_file() and p.name != "WHY.txt":
            shutil.copy2(p, folder / p.name)


def versions(folder):
    v = Path(folder) / ".versions"
    return sorted((p for p in v.iterdir() if p.is_dir()), key=lambda p: p.name) if v.exists() else []


def build(lead, workspace):
    """Generate (or regenerate) the demo site. -> (folder, [problems]). Checks its own output before returning."""
    folder = Path(workspace) / "sites" / slug(lead["name"], lead["id"])
    folder.mkdir(parents=True, exist_ok=True)
    snapshot(folder, "before rebuild")
    c = content_for(lead)
    page = render_html(c)
    files = {"index.html": page, "styles.css": render_css(c), "site.json": json.dumps(c, indent=1),
             "README.md": render_readme(c, lead)}
    for name, text in files.items():
        tmp = folder / (name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(folder / name)
    return folder, check(folder, lead)


def check(folder, lead):
    """The site's own rules, read back from disk. -> [problems] (empty = fine)."""
    folder = Path(folder)
    problems = []
    try:
        page = (folder / "index.html").read_text(encoding="utf-8")
    except OSError as e:
        return [f"index.html can't be read ({e.__class__.__name__})"]
    if html.escape(lead["name"]) not in page and lead["name"] not in page:
        problems.append("the business name isn't on the page")
    if "not the official website" not in page.lower():
        problems.append("the 'not the official website' banner is missing")
    if 'name="viewport"' not in page:
        problems.append("no mobile viewport")
    body = re.sub(r"<[^>]+>", " ", page)
    if FORBIDDEN.search(body):
        problems.append(f"contains review / rating / award wording ('{FORBIDDEN.search(body).group(0)}'): remove it unless the owner supplied it")
    if re.search(r"\$\s?\d", body):
        problems.append("contains a price: remove it unless the owner supplied it")
    if re.search(r"<script[^>]+src=[\"']?https?://", page, re.I) or re.search(r"<iframe", page, re.I):
        problems.append("loads an external script or frame (not allowed in a demo)")
    if re.search(r"<img[^>]+src=[\"']?https?://", page, re.I):
        problems.append("hot-links an external image (rights unknown)")
    if not (folder / "styles.css").exists():
        problems.append("styles.css is missing")
    return problems
