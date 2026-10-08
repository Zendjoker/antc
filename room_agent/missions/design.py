"""Design system for demo sites: layouts, palettes (contrast-checked), decorative art made here, opening hours.

Everything visual is generated locally: no web fonts, no stock photos, no icon libraries, no external CSS / JS. A demo
gets a layout and palette chosen from its category and a stable hash of its name (so a rebuild looks the same), or the
ones asked for ("make it the bold layout", "make it blue").
"""

import colorsys
import hashlib
import html
import re

LAYOUTS = ("editorial", "modern", "bold")
FONTS = {  # (display, body): system font stacks only
    "editorial": ('"Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif',
                  'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif'),
    "modern": ('"Segoe UI Variable Display", "SF Pro Display", system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif',
               'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif'),
    "bold": ('"Arial Black", "Segoe UI Black", Impact, system-ui, sans-serif',
             'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif'),
}
# accent colours per family (a few each); backgrounds / text are derived and contrast-checked
ACCENTS = {
    "restaurant": ["#b5452b", "#8c3b2f", "#2f6b4f", "#7a3e6a"], "cafe": ["#7a4b2a", "#5b6b3a", "#9a5b2e"],
    "bar": ["#d9a441", "#c0563b", "#4f7cac"], "bakery": ["#c8793a", "#a4553a", "#b7863b"],
    "health": ["#1f7a8c", "#2c6e9e", "#3b7d5b"], "beauty": ["#a34e6b", "#7b5aa6", "#b06a4f"],
    "trade": ["#e07b00", "#2d6cb5", "#c0392b"], "office": ["#2f5d9e", "#2d5d4f", "#5a4a8a"],
    "default": ["#2d6a4f", "#2f5d9e", "#9a4a2e"],
}
NAMED = {"blue": "#2563eb", "navy": "#1e3a8a", "green": "#2f7d4f", "red": "#b91c1c", "orange": "#d9640a",
         "purple": "#6d28d9", "pink": "#be185d", "teal": "#0f766e", "brown": "#7a4b2a", "gold": "#b7862b",
         "black": "#111827", "gray": "#4b5563", "grey": "#4b5563", "yellow": "#a16207"}
DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
DAY_NAMES = {"Mo": "Monday", "Tu": "Tuesday", "We": "Wednesday", "Th": "Thursday", "Fr": "Friday", "Sa": "Saturday",
             "Su": "Sunday"}


def seed(text):
    return int(hashlib.sha256(str(text).encode()).hexdigest()[:8], 16)


# ---------------------------------------------------------------- colour
def _rgb(hex_):
    h = hex_.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def luminance(hex_):
    def ch(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (ch(c) for c in _rgb(hex_))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _shade(hex_, lightness):
    h, l, s = colorsys.rgb_to_hls(*_rgb(hex_))
    return _hex(colorsys.hls_to_rgb(h, max(0.0, min(1.0, lightness)), s))


def readable(fg, bg, ratio=4.5):
    """fg moved darker / lighter (same hue) until it reaches `ratio` against bg (WCAG AA for text)."""
    if contrast(fg, bg) >= ratio:
        return fg
    h, l, s = colorsys.rgb_to_hls(*_rgb(fg))
    step = -0.03 if luminance(bg) > 0.5 else 0.03
    for _ in range(40):
        l = max(0.0, min(1.0, l + step))
        cand = _hex(colorsys.hls_to_rgb(h, l, s))
        if contrast(cand, bg) >= ratio:
            return cand
    return "#000000" if luminance(bg) > 0.5 else "#ffffff"


def palette(accent, dark=False):
    """A full palette from one accent colour, every text pair at WCAG AA (4.5:1)."""
    h, l, s = colorsys.rgb_to_hls(*_rgb(accent))
    if dark:
        bg, surface, text = _hex(colorsys.hls_to_rgb(h, 0.08, min(s, 0.25))), _hex(colorsys.hls_to_rgb(h, 0.13, min(s, 0.2))), "#f4f1ea"
    else:
        bg, surface, text = _hex(colorsys.hls_to_rgb(h, 0.975, min(s, 0.35))), "#ffffff", _hex(colorsys.hls_to_rgb(h, 0.12, min(s, 0.3)))
    accent_ok = readable(accent, surface, 4.5)
    on_accent = "#ffffff" if contrast("#ffffff", accent_ok) >= contrast("#111111", accent_ok) else "#111111"
    muted = readable(_shade(text, 0.45 if not dark else 0.7), surface, 4.5)
    soft = _hex(colorsys.hls_to_rgb(h, 0.93 if not dark else 0.18, min(s, 0.45)))
    return {"bg": bg, "surface": surface, "text": readable(text, bg, 7.0), "muted": muted, "accent": accent_ok,
            "on_accent": on_accent, "soft": soft, "dark": dark}


def palette_problems(p):
    out = []
    for fg, bg, need, what in ((p["text"], p["bg"], 4.5, "text on background"), (p["text"], p["surface"], 4.5, "text on cards"),
                               (p["muted"], p["surface"], 4.5, "secondary text"), (p["accent"], p["surface"], 4.5, "links"),
                               (p["on_accent"], p["accent"], 4.5, "button text")):
        if contrast(fg, bg) < need:
            out.append(f"{what} contrast {contrast(fg, bg):.1f}:1 is below {need}:1")
    return out


def choose(family, name, layout=None, color=None):
    """-> (layout, palette) for a business: stable across rebuilds; overrides from the user win."""
    sd = seed(name)
    lay = layout if layout in LAYOUTS else LAYOUTS[sd % len(LAYOUTS)]
    accents = ACCENTS.get(family, ACCENTS["default"])
    accent = NAMED.get(str(color or "").lower()) or (color if re.fullmatch(r"#[0-9a-fA-F]{6}", str(color or "")) else None) \
        or accents[(sd // 7) % len(accents)]
    return lay, palette(accent, dark=(lay == "bold" and family in ("bar",)))


# ---------------------------------------------------------------- decorative art (made here: no rights issues)
def art_svg(p, sd, layout):
    """A small abstract SVG (circles / arcs / lines) in the palette: decoration with no third-party content."""
    import random

    rnd = random.Random(sd)
    a, s = p["accent"], p["soft"]
    parts = []
    if layout == "modern":
        for _ in range(7):
            r = rnd.randint(30, 120)
            parts.append(f'<circle cx="{rnd.randint(0, 600)}" cy="{rnd.randint(0, 400)}" r="{r}" fill="{a}" '
                         f'fill-opacity="{rnd.choice([0.08, 0.12, 0.18])}"/>')
        for _ in range(4):
            y = rnd.randint(40, 360)
            parts.append(f'<path d="M0 {y} C 150 {y - 80}, 450 {y + 80}, 600 {y}" stroke="{a}" stroke-opacity=".35" '
                         f'stroke-width="2" fill="none"/>')
    elif layout == "editorial":
        for i in range(0, 600, 24):
            parts.append(f'<line x1="{i}" y1="0" x2="{i + 200}" y2="400" stroke="{a}" stroke-opacity=".08" stroke-width="1"/>')
        parts.append(f'<circle cx="460" cy="160" r="110" fill="{s}"/><circle cx="460" cy="160" r="110" fill="none" '
                     f'stroke="{a}" stroke-opacity=".5" stroke-width="2"/>')
    else:
        for _ in range(5):
            x, y, w = rnd.randint(0, 520), rnd.randint(0, 320), rnd.randint(60, 200)
            parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{w // 3}" rx="6" fill="{a}" '
                         f'fill-opacity="{rnd.choice([0.15, 0.25, 0.35])}" transform="rotate({rnd.randint(-12, 12)} {x} {y})"/>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400" preserveAspectRatio="xMidYMid slice" '
            'aria-hidden="true" focusable="false">' + "".join(parts) + "</svg>")


def monogram_svg(name, p):
    letters = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", name)[:2]).upper() or "•"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="31" '
            f'fill="{p["accent"]}"/><text x="32" y="40" text-anchor="middle" font-family="Georgia, serif" font-size="24" '
            f'fill="{p["on_accent"]}">{html.escape(letters)}</text></svg>')


# ---------------------------------------------------------------- opening hours (OpenStreetMap syntax, common subset)
def _days(spec):
    out = []
    for part in spec.split(","):
        part = part.strip()
        m = re.fullmatch(r"(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?", part)
        if not m:
            return None
        a = DAYS.index(m.group(1))
        b = DAYS.index(m.group(2)) if m.group(2) else a
        idx = list(range(a, b + 1)) if a <= b else list(range(a, 7)) + list(range(0, b + 1))
        out += [DAYS[i] for i in idx]
    return out


def _fmt(t):
    h, m = (int(x) for x in t.split(":"))
    suffix = "am" if h < 12 or h == 24 else "pm"
    hh = h % 12 or 12
    return f"{hh}{'' if m == 0 else f':{m:02d}'}{suffix}" if h != 24 else "midnight"


def parse_hours(text):
    """'Mo-Fr 11:00-22:00; Sa,Su 10:00-23:00' -> [(day name, '11am – 10pm' or 'Closed')], or None when the rule uses
    anything this subset doesn't understand (then the raw text is shown, never a guess)."""
    t = str(text or "").strip()
    if not t:
        return None
    if t == "24/7":
        return [(DAY_NAMES[d], "Open 24 hours") for d in DAYS]
    table = {}
    for rule in [r.strip() for r in t.split(";") if r.strip()]:
        if re.fullmatch(r"PH\b.*", rule):
            continue  # (public holidays: not shown in the weekly table)
        m = re.fullmatch(r"([A-Za-z,\- ]+?)\s+(off|closed|(?:\d{2}:\d{2}-\d{2}:\d{2})(?:\s*,\s*\d{2}:\d{2}-\d{2}:\d{2})*)", rule)
        if not m:
            return None
        days = _days(m.group(1).replace(" ", ""))
        if days is None:
            return None
        if m.group(2) in ("off", "closed"):
            val = "Closed"
        else:
            spans = []
            for span in re.findall(r"(\d{2}:\d{2})-(\d{2}:\d{2})", m.group(2)):
                if int(span[0][:2]) > 24 or int(span[1][:2]) > 28:
                    return None
                end = f"{int(span[1][:2]) % 24:02d}:{span[1][3:]}" if int(span[1][:2]) > 24 else span[1]
                spans.append(f"{_fmt(span[0])} – {_fmt(end)}")
            val = ", ".join(spans)
        for d in days:
            table[d] = val
    if not table:
        return None
    return [(DAY_NAMES[d], table.get(d, "Closed")) for d in DAYS]
