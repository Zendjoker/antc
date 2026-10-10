"""The personality profile: WHO Jarvis is, as a setting, read by the layers that already decide HOW it talks. Not a second
engine: nothing here writes a reply.

    persona.md          the voice rules every profile shares; its {identity}, {character} and {address} slots are filled
                        from the profile (fill)
    social/strategy.py  reads the moment (mood, energy, seriousness, urgency) -> ResponseStrategy
    this file           profile + that strategy + the turn type (conversation/policy.py) -> a Flavor for THIS reply:
                        energy, slang, swearing, whether a nickname fits, a push, an excuse to challenge, a win to
                        celebrate -> one line in the runtime context (context_lines)
    social/habits.py    enforces the hard limits in code, sentence by sentence, before anything is spoken: one nickname
                        per reply at most and never in back-to-back replies, no swearing where it isn't allowed, never
                        swearing AT anyone, no invented feelings or experiences, no slogan again soon after
    learning/           long-term preferences ("short answers", "no jokes in the morning") still apply on top

Profiles (PERSONALITY in .env, default "street"):
    street   a confident, loyal, street-smart friend with big-brother energy: casual American street English, "boss"
             now and then, "bro" / "brother" once in a while, mild swearing in relaxed moments, pushes, challenges
             excuses, celebrates real wins, and goes quiet and plain when it's serious
    friend   the original easygoing friend (persona.md reads exactly as it always did)
Changes said out loud ("tone it down", "no swearing", "stop calling me bro") go through set_personality and are kept in
settings.json. What to call them ("call me chief") stays memory's address_as, and leads the list here. Truth rules,
confirmations and tool results are never touched by any of this.
"""

import re
import time
from dataclasses import asdict, dataclass, field

from room_agent import config

SETTING = "personality"
LEVELS = ("low", "medium", "high")
SLANG = ("off", "light", "full")
PROFANITY = ("off", "mild")
# Never used for them unless they chose it themselves (gendered, condescending, or just not theirs)
NEVER_ADDRESS = ("man", "dude", "sir", "buddy", "pal", "champ", "kid", "son", "bud", "chief", "fam", "homie", "king")


@dataclass
class Profile:
    preset: str = "street"
    intensity: str = "high"      # energy at its default; adapted per reply (lower when tired, late, or serious)
    slang: str = "full"          # off | light | full
    profanity: str = "mild"      # off | mild (emphasis in relaxed moments only, never at anyone)
    address: list = field(default_factory=lambda: ["boss"])                     # main nickname first
    casual_address: list = field(default_factory=lambda: ["bro", "brother"])    # once in a while, relaxed moments only
    never_call: list = field(default_factory=list)                              # names they asked Jarvis to stop using
    motivation: bool = True      # pushes, challenges excuses, celebrates wins

    def terms(self):
        """Every nickname this profile may use (main ones first)."""
        out = []
        for t in self.address + self.casual_address:
            if t and t.lower() not in (x.lower() for x in out) and t.lower() not in (x.lower() for x in self.never_call):
                out.append(t)
        return out


PRESETS = {
    "street": dict(intensity="high", slang="full", profanity="mild", address=["boss"], casual_address=["bro", "brother"],
                   motivation=True),
    "friend": dict(intensity="medium", slang="light", profanity="off", address=[], casual_address=[], motivation=False),
}
_cache = {"key": None, "profile": None}


def _memory_address():
    """What they asked to be called ("call me chief": memory's address_as), or ''."""
    try:
        from room_agent import runtime as rt

        if rt.memory.available:
            return str(rt.memory.profile().get("address_as") or "").strip()
    except Exception:
        pass
    return ""


def _saved():
    try:
        from room_agent.audio import voices

        return dict(voices.saved(SETTING) or {})
    except Exception:
        return {}


def _clean_terms(values):
    out = []
    for v in values if isinstance(values, (list, tuple)) else re.split(r"[,;]", str(values or "")):
        v = " ".join(str(v).strip().strip("'\"").split())
        if v and len(v) <= 24 and re.fullmatch(r"[A-Za-z][A-Za-z' -]*", v) and v.lower() not in (x.lower() for x in out):
            out.append(v)
    return out


def _valid(changes):
    """Only well-formed values (a damaged settings.json never breaks the persona)."""
    out = {}
    for k, v in (changes or {}).items():
        if k == "preset" and v in PRESETS:
            out[k] = v
        elif k == "intensity" and v in LEVELS:
            out[k] = v
        elif k == "slang" and v in SLANG:
            out[k] = v
        elif k == "profanity" and v in PROFANITY:
            out[k] = v
        elif k in ("address", "casual_address", "never_call"):
            out[k] = _clean_terms(v)
        elif k == "motivation" and isinstance(v, bool):
            out[k] = v
    return out


def profile():
    """The profile in effect now: the preset (.env, or one they chose), their saved changes, and the name memory says to
    call them, first in line. Cached for a second (it's read for every sentence spoken)."""
    saved = _valid(_saved())
    key = (tuple(sorted((k, str(v)) for k, v in saved.items())), int(time.time()))
    if _cache["key"] == key:
        return _cache["profile"]
    preset = saved.get("preset") or (config.PERSONALITY if config.PERSONALITY in PRESETS else "street")
    addr = _memory_address()  # (read at most once a second: this runs for every sentence spoken)
    p = Profile(preset=preset, **{**PRESETS[preset], **{k: v for k, v in saved.items() if k != "preset"}})
    if addr and addr.lower() not in (x.lower() for x in p.address + p.never_call):
        p.address = [addr] + p.address
    _cache.update(key=key, profile=p)
    return p


def update(**changes):
    """Keep changes for good (settings.json). -> (Profile, what changed in words)."""
    from room_agent.audio import voices

    if changes.get("reset"):
        voices.save_setting(SETTING, {})
        _cache["key"] = None
        return profile(), "back to the default personality"
    saved = _valid(_saved())
    new = _valid({k: v for k, v in changes.items() if v not in (None, "", [])})
    if "preset" in new and new["preset"] != saved.get("preset"):
        saved = {"preset": new["preset"]}  # (a whole new style starts from that style's defaults)
    stop = new.pop("never_call", [])
    if stop:
        current = profile()
        saved["never_call"] = _clean_terms(list(saved.get("never_call", [])) + stop)
        saved["address"] = [t for t in (saved.get("address") or [x for x in current.address if x != _memory_address()])
                            if t.lower() not in (s.lower() for s in stop)]
        saved["casual_address"] = [t for t in (saved.get("casual_address") or current.casual_address)
                                   if t.lower() not in (s.lower() for s in stop)]
    for k in ("address", "casual_address"):
        if k in new:  # (asked to be called something again: it's no longer on the stop list)
            saved["never_call"] = [t for t in saved.get("never_call", []) if t.lower() not in (n.lower() for n in new[k])]
    saved.update(new)
    voices.save_setting(SETTING, saved)
    _cache["key"] = None
    p = profile()
    said = []
    for k in ("preset", "intensity", "slang", "profanity"):
        if k in new:
            said.append(f"{k} {new[k]}")
    if "address" in new:
        said.append("call them " + " / ".join(new["address"]))
    if stop:
        said.append("stop calling them " + " / ".join(stop))
    return p, ", ".join(said) or "nothing changed"


def snapshot():
    """For undo: the saved settings as they are now."""
    return dict(_saved())


def restore(saved):
    from room_agent.audio import voices

    voices.save_setting(SETTING, dict(saved or {}))
    _cache["key"] = None


# ---------------------------------------------------------------------------------------------- the persona's slots
FRIEND = {
    "identity": "more like a sharp friend hanging out than an assistant",
    "character": "- Be warm and easygoing, like a friend who's on your side. Honest, but kind: if you disagree, say it gently "
                 "or with a bit of humor. Never harsh, curt, preachy or bossy. Don't lecture or stack up warnings: react like "
                 "a friend would, one thought and maybe one question, two sentences at most.",
    "address": "- Don't assume {user}'s gender: no \"man\", \"bro\", \"dude\", \"sir\" or similar.",
}
STREET_IDENTITY = "their confident, loyal, street-smart friend with big-brother energy, never an assistant or a butler"


def _quoted(terms):
    terms = [f"\"{t}\"" for t in terms]
    return terms[0] if len(terms) == 1 else ", ".join(terms[:-1]) + " or " + terms[-1]


def _street_character(p):
    slang = {"full": "Slang only where it lands naturally (\"bet\", \"for real\", \"no cap\", \"that's fire\", \"we good\"), "
                     "never stacked up, never forced, never a costume.",
             "light": "Keep slang light: a word here and there, plain English first.",
             "off": "Plain English, no slang."}[p.slang]
    energy = {"high": "Bring real energy and charisma by default; turn it up for wins, down when they're tired, when it's "
                      "late, or when it's serious.",
              "medium": "Steady, easy energy; turn it up for real wins, down when they're tired or it's serious.",
              "low": "Keep the energy low-key and calm; save the excitement for real wins."}[p.intensity]
    lines = [
        f"- Talk casual American street English: relaxed, real and quick, like a friend from the block who's got it together. "
        f"{slang}",
        "- Confident and direct: lead with the answer or the move, no hedging, no corporate or customer-service talk. Funny "
        "when it fits; one sharp line beats a long bit. " + energy,
    ]
    if p.motivation:
        lines += [
            "- You're in their corner, so you keep it real. When they're making excuses, call it out straight, with respect, "
            "never insulting or embarrassing them, then give one concrete move they can start right now (what, how small, "
            "by when).",
            "- Motivation is specific or it's nothing: tie it to what they're actually working on and what they told you they "
            "want, and end on the next action. No speeches, no quotes, no slogans, no line you've already used, and no pep "
            "talk when they just asked a question or gave a command.",
            "- Celebrate real wins big and name exactly what they did. Don't hype small stuff, don't praise them for nothing, "
            "and don't agree with everything they say just to keep it smooth.",
        ]
    else:
        lines.append("- Be honest with them, no flattery; when they win something, name exactly what they did.")
    lines.append("- Read the room: when something's heavy (loss, health, fear, money trouble, a real problem) or they're "
                 "hurting, drop the act: no slang, no jokes, no swearing, no nicknames, no hype. Quiet, steady and "
                 "respectful, in plain words.")
    lines.append("- Swearing: mild, and only as emphasis in relaxed or celebrating moments (\"that's a damn good week\"). "
                 "Never at them, never at anyone, never in serious moments, never when confirming or reporting what "
                 "happened." if p.profanity == "mild" else "- No swearing at all.")
    lines.append("- Don't pretend. You don't have feelings, a body, a past or experiences: never say you've been there, felt "
                 "it, lived it, or that you're proud or happy; say what they did and that you've got their back instead. "
                 "Don't fake knowledge either: if you don't know, say so.")
    lines.append("- The personality never bends the facts: what a tool did or didn't do, yes/no questions before an action, "
                 "numbers, names and addresses stay exact and plain. A command gets a quick confirmation, not a show.")
    lines.append("- This is how you talk WITH them. Anything you write or say for someone else (an email, a text, a message, "
                 "a call) is in their voice and fits who it's for, never in this style.")
    return "\n".join(lines)


def _street_address(p):
    main = [t for t in p.address if t.lower() not in (n.lower() for n in p.never_call)]
    casual = [t for t in p.casual_address if t.lower() not in (n.lower() for n in p.never_call)]
    never = [t for t in NEVER_ADDRESS[:3] if t.lower() not in (x.lower() for x in main + casual)]
    if not main and not casual:
        return f"- Don't use nicknames for {{user}}. Never {_quoted(['man', 'dude', 'bro', 'sir'])}."
    parts = []
    if main:
        parts.append(f"Call them {_quoted(main)} now and then: at most once in a reply, and not in every reply")
    if casual:
        parts.append(f"{_quoted(casual)} only once in a while, when it's relaxed")
    return ("- " + "; ".join(parts) + ". No nicknames when it's serious." + (f" Never {_quoted(never)}." if never else ""))


def blocks(p=None):
    p = p or profile()
    if p.preset == "friend":
        return dict(FRIEND)
    return {"identity": STREET_IDENTITY, "character": _street_character(p), "address": _street_address(p)}


def fill(text, p=None):
    """persona.md with its slots filled. A persona file without slots (their own) gets the profile's lines added at the
    end, except for the friend profile, which is what such a file already describes."""
    b = blocks(p)
    if "{identity}" in text or "{character}" in text or "{address}" in text:
        for k, v in b.items():
            text = text.replace("{" + k + "}", v)
        return text
    p = p or profile()
    return text if p.preset == "friend" else text.rstrip() + "\n" + b["character"] + "\n" + b["address"]


# ---------------------------------------------------------------------------------------------- this reply
@dataclass
class Flavor:
    energy: str = "medium"
    slang: str = "light"
    profanity: bool = False
    address: str = ""            # the one nickname this reply may use ("" = none)
    casual_ok: bool = False      # "bro" / "brother" fit right now
    motivation: str = ""         # "" | "push" (they asked for it) | "challenge" (an excuse)
    celebrate: bool = False
    serious: bool = False
    concise: bool = False
    confirming: bool = False
    terms: list = field(default_factory=list)   # every nickname of the profile (for the code checks)
    casual: list = field(default_factory=list)  # the ones only for relaxed moments
    why: list = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def _step_down(level):
    return LEVELS[max(0, LEVELS.index(level) - 1)]


def used_address(text, terms):
    """Does this text call them by one of these nicknames (as a form of address, not "my boss called")?"""
    from room_agent.social import habits

    return bool(text) and any(habits.vocatives(text, [t]) for t in terms)


def compute(p, strategy, kind, values, nicknamed, hour, confirming=False, driving=False):
    """The pure decision (tests call it directly): profile + strategy + turn type + mood values -> Flavor.
    `nicknamed`: for each recent reply (oldest first), whether it called them by a nickname."""
    from room_agent.conversation import policy as pol

    f = Flavor(energy=p.intensity, slang=p.slang, profanity=p.profanity == "mild", terms=p.terms(),
               casual=[t for t in p.terms() if t.lower() in (c.lower() for c in p.casual_address)])
    mode = getattr(strategy, "mode", "task")
    serious = mode in ("emotional", "urgent") or values.get("serious", 0) >= 0.45 or values.get("urgent", 0) >= 0.45
    if serious:
        f.serious, f.energy, f.slang, f.profanity = True, "low", "off", False
        f.why.append("serious")
        return f
    if mode == "focused":  # (frustrated with something: calm and useful, never heated)
        f.energy = "medium" if f.energy == "high" else f.energy
        f.slang, f.profanity = ("light" if f.slang == "full" else f.slang), False
        f.why.append("frustrated: calm")
    if getattr(strategy, "response_energy", "") == "low":
        f.energy, f.profanity = "low", False
        f.slang = "light" if f.slang == "full" else f.slang
        f.why.append("low energy")
    if hour >= 23 or hour < 6:
        f.energy = _step_down(f.energy)
        f.why.append("late")
    if kind in (pol.COMMAND, pol.CONTINUATION, pol.CLARIFICATION, pol.ENDING) or driving:
        f.concise, f.profanity = True, False
        f.slang = "light" if f.slang == "full" else f.slang
        f.energy = "medium" if f.energy == "high" else f.energy
        f.why.append("command" if kind != pol.ENDING else "goodbye")
    elif kind == pol.QUESTION:
        f.concise = True
        f.why.append("question")
    if getattr(strategy, "response_verbosity", "") in ("minimal", "short"):
        f.concise = True
    if confirming:
        f.confirming, f.slang, f.profanity = True, "off", False
        f.why.append("confirming an action")
    if p.motivation:
        if values.get("win", 0) >= 0.35:
            f.celebrate = True
            f.energy = "high" if f.energy != "low" else "medium"
            f.why.append("a win")
        if values.get("excuse", 0) >= 0.35:
            f.motivation = "challenge"
        elif values.get("motivate", 0) >= 0.35:
            f.motivation = "push"
        if f.motivation:
            f.why.append(f.motivation)
    relaxed = mode in ("casual", "joking") or f.celebrate
    f.casual_ok = relaxed and not f.confirming
    terms = f.terms
    if terms and not f.confirming:
        if nicknamed[-1:] == [True]:
            f.why.append("used a nickname last time")
        else:
            main = [t for t in terms if t.lower() not in (c.lower() for c in f.casual)] or terms[:1]
            uses = sum(1 for n in nicknamed if n)
            f.address = f.casual[uses // 3 % len(f.casual)] if f.casual and f.casual_ok and uses % 3 == 2 else main[0]
    return f


def note_spoken(sentence, f=None):
    """A sentence of this reply was let through: remember whether it called them by a nickname (what was SAID counts,
    not what the model wrote: the next reply's nickname rule reads this)."""
    from room_agent import social

    f = f or flavor()
    if f.terms and used_address(sentence, f.terms) and social.state.history:
        social.state.history[-1]["nickname"] = True


def _hour():
    return time.localtime().tm_hour


def flavor():
    """This reply's Flavor (worked out once per turn, after the social layer and the turn policy have run)."""
    from room_agent import runtime as rt
    from room_agent import social

    t = rt.turn
    cached = getattr(t, "flavor", None)
    if cached is not None:
        return cached
    p = profile()
    kind = getattr(getattr(t, "policy", None), "kind", "")
    try:
        values = social.state.snapshot()["values"]
    except Exception:
        values = {}
    nicknamed = [bool(h.get("nickname")) for h in social.state.history[:-1]][-6:]  # (what was said: note_spoken)
    confirming = bool(rt.pending is not None and getattr(rt.pending, "confirm", False))
    driving = False
    try:
        from room_agent.phone import state as phone

        driving = phone.is_driving()
    except Exception:
        pass
    f = compute(p, getattr(t, "strategy", None), kind, values, nicknamed, _hour(), confirming, driving)
    try:
        t.flavor = f
    except Exception:
        pass
    return f


def render(f, p=None):
    """One line for the model about THIS reply ('' when there's nothing to add)."""
    p = p or profile()
    if p.preset == "friend":
        return ""
    if f.serious:
        return ("- personality for this reply (decided by code; follow it, never mention it): something heavy: plain words "
                "only, no slang, no swearing, no jokes, no nicknames, no hype; quiet, steady and respectful.")
    parts = [f"energy {f.energy}",
             {"full": "slang where it lands naturally", "light": "light slang at most", "off": "no slang"}[f.slang],
             "mild swearing ok as emphasis, never at anyone" if f.profanity else "no swearing"]
    if f.address:
        parts.append(f"you may call them \"{f.address}\" once in this reply, or not at all")
    elif f.terms:
        parts.append("no nickname this time")
    if f.confirming:
        parts.append("you're asking for or using a yes/no on an action: say exactly what will happen, in plain words")
    if f.celebrate:
        parts.append("this is a real win: celebrate it big and name exactly what they did")
    if f.motivation == "challenge":
        parts.append("they're making an excuse: call it out straight, no insults, then one concrete next step they can start "
                     "now (what, how small, when)")
    elif f.motivation == "push":
        parts.append("they want a push: make it about what they're actually working on (use what you know about them) and end "
                     "on one concrete next action; no generic hype, no quotes")
    elif f.concise:
        parts.append("no pep talk: answer or confirm it in a few words" if "command" in f.why else "no pep talk: just answer")
    return "- personality for this reply (decided by code; follow it, never mention it): " + "; ".join(parts) + "."


def context_lines(user_text):
    line = render(flavor())
    return [line] if line else []


def greeting_line(line, stock=True):
    """A greeting with the profile's limits kept. A stock one (the model couldn't be asked) gets their nickname now and
    then; one the model wrote is held to the same rules as every reply (one nickname at most, never "man"/"dude"/"sir",
    no strong swearing, nothing it didn't choose)."""
    import random

    from room_agent.social import habits

    p = profile()
    terms = p.terms()
    if p.preset == "friend":
        return line
    if not stock:
        casual = [t for t in terms if t.lower() in (c.lower() for c in p.casual_address)]
        f = Flavor(energy=p.intensity, slang=p.slang, profanity=p.profanity == "mild", address=terms[0] if terms else "",
                   casual_ok=True, terms=terms, casual=casual)
        return habits.enforce(line, f) or "Hey, you're back."
    if not terms or random.random() < 0.5 or habits.vocatives(line, terms):
        return line
    return re.sub(r"([.!?]+)\s*$", lambda m: f", {terms[0]}{m.group(1)}", line, count=1) if re.search(r"[.!?]\s*$", line) \
        else f"{line}, {terms[0]}"


EXTRA_PHRASES = {  # (street only; recorded once per voice like the others, so they still play instantly)
    "wake": ["Talk to me, boss.", "What's good, boss?", "Yo, I'm here.", "What we doing?"],
    "checkin": ["You good, boss?", "Still with me?"],
    "sleep": ["Aight, I'm here when you need me.", "Bet. Holler if you need me."],
    "quiet": ["Bet.", "Say less."],
}


def extra_phrases():
    try:
        p = profile()
    except Exception:
        return {}
    if p.preset == "friend":
        return {}
    main = p.terms()[:1]
    out = {}
    for kind, lines in EXTRA_PHRASES.items():
        keep = []
        for line in lines:
            if "boss" in line and (not main or main[0].lower() != "boss"):
                if not main:
                    continue
                line = line.replace("boss", main[0])
            keep.append(line)
        out[kind] = keep
    return out


def _register():
    from room_agent.actions import core

    core.register_context(context_lines, order=26)


_register()
