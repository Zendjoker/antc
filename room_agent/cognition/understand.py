"""What the user's own words say about a request, read by code: the deterministic check on the model's interpretation.

The conversational model stays the primary interpreter (its tool calls and update_goal). This layer reads the words
themselves, without a model call, for what must never be lost or guessed:

    families      which capability areas the request needs (research, browser, desktop, files, business, coding,
                  email, calendar, lists, info); a negated one ("don't open Chrome") doesn't count
    constraints   explicit limits ("don't send it", "don't touch the text files", "only the PNG files", "keep the
                  screenshots", "don't use Google", "at most $2"), as objects the executor enforces (blocks())
    permissions   what the request implies needs a yes or money (send_email, delete, move, paid, code_change,
                  calendar_write) - never granted here, only named
    missing       what must be asked instead of guessed ("send it to him": who and what)

merge() combines it with what the model recorded (update_goal): the model's own fields are kept, every explicit constraint
from the words is added, and a contradiction (the model planning what the words forbid) is listed. The optional second
pass (GOAL_VERIFIER, off by default) asks a cheap model only when something is ambiguous, and keeps only constraints whose
words actually appear in the request: a model's confidence is never trusted on its own.

read_turn(text) is the per-turn reading, computed once at the start of every turn (cognition.begin_turn ->
rt.turn.intent): a TurnIntent with op (turn_on, close, cancel_for_good...), scope (tab, app, device, mission...),
target, value, route (business_mission only when it names a business and a website / lead / demo), families and
negation. Regexes only (well under 2 ms), never raises.
"""

import re
from dataclasses import dataclass, field

from room_agent.cognition.goal import Constraint

APPS = r"(spotify|chrome|google chrome|firefox|edge|vs ?code|visual studio code|discord|slack|word|excel|outlook|notepad|" \
       r"terminal|teams|zoom|steam|obs)"
NEG = r"(?:don'?t|do not|never|without|no need to|not)\s+(?:\w+\s+){0,2}?"
FAMILY_RX = {
    "research": r"\b(research|look (?:up|into)|find out|compare|search the web|look for info|what'?s the (?:population|capital)|"
                r"(?:check|find) (?:the )?(?:hotel |flight )?prices?|current [\w ]{0,20}rate|latest news|news about)\b",
    "browser": r"\b(docs? page|documentation|go to (?:the )?\w+|login page|web ?page|website for|github|\w+\.(?:com|org|io)\b|"
               r"open (?:the|my) [\w' ]{0,30}(?:page|docs|site))",
    "desktop": rf"\b(?:open|launch|start|close|quit|put|move|focus)\s+{APPS}\b|\b(monitor|volume|maximi[sz]e|minimi[sz]e|"
               r"window)\b",
    "files": r"\b(files?|folders?|downloads|documents|desktop|pictures|\.?(?:pdf|png|jpe?g|csv|docx?|txt|xlsx)\b|"
             r"note called|save (?:a|the|it)|write it up|as a (?:file|doc)|in a doc\b|rename|trash|delete (?:the|that|my)|"
             r"tidy|clean up my|sort my|jot down|checklist)",
    "business": r"\b(web ?sites?|demos?|mock-?ups?|prospect\w*|leads?|outreach)\b",
    "coding": r"\b(tests?|unit tests|bugs?|debug|the build|my [\w-]+ project|constant in|code ?base|compile)\b",
    "email": r"\b(e-?mail\w*|mail|send (?:it|that|them)|forward|reply|draft|write to)\b|[\w.+-]+@[\w-]+\.\w+",
    "calendar": r"\b(book (?:it|a)|schedule|calendar|set up a (?:meeting|call)|move it to (?:next )?\w+day|"
                r"appointment)\b",
    "lists": r"\b(lists?|remind(?:er| me)|to-?do|shopping|my notes|umbrella reminder)\b",
    "info": r"\b(weather|forecast|what time)\b",
}
PERMISSIONS = {
    "send_email": r"\b(send|forward|email \S+@\S+ that|write to \S+@\S+)\b",
    "delete": r"\b(delete|trash|remove (?:the|my|old)|get rid of)\b",
    "move": r"\b(move|put (?:the|my)|sort|tidy|clean up|rename|organi[sz]e)\b",
    "paid": r"(\$\s?\d|\b\d+(?:\.\d+)?\s*(?:dollars|bucks|usd)\b|\bbudget\b|\bspend\b)",
    "code_change": r"\b(fix|debug|update the|change the|make the build pass|correct)\b",
    "calendar_write": r"\b(book|schedule|set up|move it to|reschedule)\b",
}
EXT_WORDS = {"text": ".txt", "pdf": ".pdf", "image": "image", "photo": "image", "picture": "image", "png": ".png",
             "jpg": ".jpg", "jpeg": ".jpg", "csv": ".csv", "word": ".docx", "excel": ".xlsx", "spreadsheet": ".xlsx",
             "screenshot": "screenshot", "installer": "installer", "video": "video"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic"}
FILE_TOOLS = {"move_file", "delete_file", "save_file", "open_file", "rename_file"}
SEND_TOOLS = {"gmail_send", "text_me", "call_me"}


@dataclass
class Rule(Constraint):
    """A constraint from the words, enforced by the executor through blocks() (cognition.check_call)."""
    kind: str = "forbid"          # forbid | only | keep | budget
    target: str = ""              # what it's about: ".txt", "send", "google", "image"...
    amount: float = 0.0

    def key(self):
        if self.kind == "budget":
            return f"budget:{self.amount:g}"
        if self.kind == "only":
            return f"only:{self.target}"
        if self.kind == "keep":
            return f"keep:{self.subject or self.target}"
        return f"forbid:{self.verb}" + (f":{self.target}" if self.target else "")

    def blocks(self, capability, args, subject=None):
        args = args or {}
        if self.kind == "forbid" and self.verb == "send":
            return capability in SEND_TOOLS
        if self.kind == "forbid" and self.verb in ("book", "reserve"):
            return capability.startswith(("calendar_create", "book", "purchase"))
        if self.kind == "forbid" and self.verb == "google":
            return capability == "start_business_mission" and args.get("use_google_places") is True
        if self.kind == "forbid" and self.verb == "outreach":
            return capability == "start_business_mission" and args.get("outreach") is True
        if self.kind == "forbid" and self.verb == "delete":
            return capability in ("delete_file", "clear_list", "calendar_delete_event")
        if self.kind == "forbid" and self.verb == "change":
            return capability in ("apply_code_fix", "fix_code") and self.target in str(args.get("allow", "")).lower()
        if capability not in FILE_TOOLS:
            return Constraint.blocks(self, capability, args, subject) if self.kind == "forbid" and self.verb in (
                "open", "close") else False
        name = str(args.get("file") or args.get("name") or subject or "").lower()
        if not name:
            return False
        match = _matches(name, self.target)
        if self.kind == "forbid" and self.verb == "touch":
            return match
        if self.kind == "keep":
            return match and capability in ("move_file", "delete_file")
        if self.kind == "only":
            return capability in ("move_file", "delete_file") and not match
        return False


def _matches(name, target):
    name = name.lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] else ""
    if target == "image":
        return ext in IMAGE_EXT
    if target == "screenshot":
        return "screenshot" in name or "screen shot" in name or name.rsplit("/", 1)[-1].startswith("shot")
    if target == "installer":
        return ext in (".exe", ".msi", ".msix", ".dmg")
    if target.startswith("."):
        return ext == target or (target == ".docx" and ext == ".doc")
    return target in name


@dataclass
class Spec:
    text: str
    families: list = field(default_factory=list)
    constraints: list = field(default_factory=list)   # Rule
    permissions: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    intention: str = ""
    outcome: list = field(default_factory=list)       # what should end up true (from the model)
    success_criteria: list = field(default_factory=list)
    deliverables: list = field(default_factory=list)
    budget_usd: float = None
    conflicts: list = field(default_factory=list)     # where the model's interpretation contradicts the words
    source: str = "words"                             # words | words+model | words+model+verifier

    def constraint_keys(self):
        return [c.key() for c in self.constraints]

    def describe(self):
        bits = []
        if self.families:
            bits.append("needs: " + ", ".join(self.families))
        if self.constraints:
            bits.append("their limits: " + "; ".join(c.text for c in self.constraints))
        if self.permissions:
            bits.append("needs their yes / money for: " + ", ".join(self.permissions))
        if self.missing:
            bits.append("must ask: " + ", ".join(self.missing))
        if self.conflicts:
            bits.append("CONFLICT: " + "; ".join(self.conflicts))
        return ". ".join(bits)


def _negated(text, m):
    """Is the match at m preceded (within a few words) by a negation?"""
    before = text[max(0, m.start() - 30):m.start()].lower()
    return bool(re.search(r"(?:don'?t|do not|never|without|no need to|not)\s+(?:\w+\s+){0,2}$", before))


def _families(text):
    out = []
    for fam, rx in FAMILY_RX.items():
        hits = [m for m in re.finditer(rx, text, re.I) if not _negated(text, m)
                and not (fam == "browser" and "@" in text[max(0, m.start() - 40):m.start()].split(" ")[-1])]
        if hits:
            out.append(fam)
    if "business" in out and not re.search(r"\b(find|prospect|get|look for|search for|build|businesses|shops|restaurants|"
                                            r"\w+s in [A-Z])", text, re.I):
        out.remove("business")
    if "business" in out:  # (a business request: its "websites" / "demos" aren't files or browser pages)
        out = [f for f in out if f not in ("browser", "files")]
    if "coding" in out and "lists" in out and not re.search(r"\blist\b", text, re.I):
        out.remove("lists")
    if "email" in out and "files" in out and not re.search(r"\.\w{2,4}\b|\bfile|folder|documents", text, re.I):
        out.remove("files")
    if "calendar" in out and "files" in out and not re.search(r"\.\w{2,4}\b|\bfiles?\b|folders?", text, re.I):
        out.remove("files")
    return out


def _constraints(text):
    out = []
    low = text.lower()
    for m in re.finditer(r"(?:don'?t|do not|never|without|no need to|but not)\s+(touch(?:ing)?|chang(?:e|ing)|modify(?:ing)?|edit(?:ing)?|mov(?:e|ing))\s+(?:the\s+|my\s+|any\s+)?"
                         r"([\w .-]{2,30}?)(?=[,.;!?]|$|\s+(?:and|but|or|then)\b)", text, re.I):
        obj = m.group(2).strip().lower()
        ext = next((v for k, v in EXT_WORDS.items() if re.search(rf"\b{k}s?\b", obj)), None)
        verb = "touch" if m.group(1).lower().startswith(("touch", "mov")) else "change"
        if verb == "change" and re.search(r"\btests?\b", obj):
            out.append(Rule(m.group(0), "change", "tests", kind="forbid", target="tests"))
        else:
            out.append(Rule(m.group(0), "touch", obj, kind="forbid", target=ext or obj.replace(" files", "")))
    for m in re.finditer(r"\bleave\s+(?:the\s+|my\s+)?([\w .-]{2,30}?)\s+alone\b", text, re.I):
        obj = m.group(1).lower()
        ext = next((v for k, v in EXT_WORDS.items() if re.search(rf"\b{k}s?\b", obj)), None)
        out.append(Rule(m.group(0), "touch", obj, kind="forbid", target=ext or obj))
    for m in re.finditer(r"\b(?:but\s+)?keep\s+(?:the\s+|my\s+)?([\w .-]{2,30}?)(?=[,.;!?]|$|\s+(?:and|but)\b)", text, re.I):
        obj = m.group(1).lower().strip()
        tgt = next((v for k, v in EXT_WORDS.items() if re.search(rf"\b{k}s?\b", obj)), obj.rstrip("s"))
        out.append(Rule(m.group(0), "", obj, kind="keep", target=tgt))
    for m in re.finditer(r"\b(?:only|just)\s+(?:the\s+)?(?:\.)?([a-z0-9]{2,5})\s+(?:files?|screenshots?|images?|photos?)",
                         text, re.I):
        ext = EXT_WORDS.get(m.group(1).lower(), "." + m.group(1).lower())
        out.append(Rule(m.group(0), "", m.group(1), kind="only", target=ext))
    if re.search(r"\b(?:don'?t|do not|never|but not|without)\s+(?:\w+\s+){0,2}?send", low) or \
            re.search(r"\bnot send anything\b", low):
        out.append(Rule("don't send it", "send", "", kind="forbid", target=""))
    if re.search(r"\b(?:don'?t|do not|never|without)\s+(?:use\s+|using\s+)?google", low):
        out.append(Rule("don't use Google", "google", "", kind="forbid"))
    for v in ("book", "reserve", "delete", "buy"):
        if re.search(rf"\b(?:don'?t|do not|never|without)\s+(?:\w+\s+){{0,1}}?{v}\w*", low):
            out.append(Rule(f"don't {v}", v, "", kind="forbid"))
    if re.search(r"\b(?:no|skip|without)\s+(?:the\s+)?outreach\b|\bdon'?t (?:write|prepare) (?:any )?e-?mails\b", low):
        out.append(Rule("no outreach", "outreach", "", kind="forbid"))
    for m in re.finditer(rf"\b(?:don'?t|do not|never|without)\s+(open|launch|close|opening|launching)\s+(?:a\s+|the\s+|my\s+)?"
                         rf"({APPS[1:-1]}|browser)\b", text, re.I):
        verb = {"opening": "open", "launching": "open", "launch": "open"}.get(m.group(1).lower(), m.group(1).lower())
        out.append(Rule(m.group(0), verb, m.group(2).lower(), kind="forbid", target=m.group(2).lower()))
    m = re.search(r"\$\s?(\d+(?:\.\d+)?)", text) or re.search(r"\b(\d+(?:\.\d+)?)\s*(?:dollars|bucks|usd)\b", low)
    if m and re.search(r"\b(at most|max(?:imum)?|up to|budget|spend|no more than|limit)\b", low):
        out.append(Rule(m.group(0), "", "", kind="budget", amount=float(m.group(1))))
    seen, uniq = set(), []
    for c in out:
        if c.key() not in seen:
            seen.add(c.key())
            uniq.append(c)
    return uniq


def _missing(text, families):
    out = []
    low = text.lower()
    m = re.search(r"\b(send|forward|book|move|delete|reschedule|rename|open|reply to)\s+(it|that|this|them|him|her)\b", low)
    if (m and len(low[:m.start()].split()) < 2) or re.search(r"^(delete|send|forward|open)\s+that\s+(?!to\b)\w+$", low):
        out.append("referent")  # (a pronoun with nothing before it to refer to: "send it to him", "book it for 3")
    if "email" in families and not re.search(r"[\w.+-]+@[\w-]+\.\w+", text) and (
            re.search(r"\b(to|for)\s+(him|her|them)\b", low) or re.search(r"\breply to the \w+", low)):
        out.append("recipient")
    if re.fullmatch(r"\s*(rename|delete|open|move)\s+the\s+file\s*", low):
        out.append("file")
    if "business" in families:
        try:
            from room_agent.missions import goals

            g = goals.interpret(text)
            if g.capability == "business_mission":
                out += [q["field"] for q in g.questions if q["field"] not in out]
        except Exception:  # noqa: BLE001
            pass
    return out


def from_words(text):
    """-> Spec from the user's words alone (no model call)."""
    t = " ".join(str(text or "").split())
    fams = _families(t)
    perms = [p for p, rx in PERMISSIONS.items() if re.search(rx, t, re.I)]
    cons = _constraints(t)
    if any(c.key() == "forbid:send" for c in cons) and "send_email" in perms:
        perms.remove("send_email")
    if "email" not in fams:
        perms = [p for p in perms if p != "send_email"] if not re.search(r"\bsend\b", t, re.I) else perms
    if "coding" not in fams and "code_change" in perms:
        perms.remove("code_change")
    if "calendar" not in fams and "calendar_write" in perms:
        perms.remove("calendar_write")
    if "files" not in fams and not re.search(r"\b(files?|photos?|invoices?|duplicates?)\b", t, re.I):
        perms = [p for p in perms if p not in ("move", "delete")]
    if "paid" in perms and "business" not in fams:
        perms.remove("paid")
    budget = next((c.amount for c in cons if c.kind == "budget"), None)
    return Spec(text=t, families=fams, constraints=cons, permissions=perms, missing=_missing(t, fams), budget_usd=budget)


def merge(words, model=None):
    """The model's interpretation (update_goal fields) + what the words say. The model's fields are kept; every explicit
    constraint from the words is preserved; contradictions are listed (never silently resolved)."""
    model = model or {}
    s = Spec(text=words.text, families=list(words.families), constraints=list(words.constraints),
             permissions=list(words.permissions), missing=list(words.missing), budget_usd=words.budget_usd,
             source="words+model" if model else "words")
    s.intention = str(model.get("intention") or model.get("summary") or "")[:200]
    s.outcome = [str(x)[:120] for x in (model.get("desired_state") or [])][:6]
    s.success_criteria = [str(x)[:120] for x in (model.get("success_conditions") or [])][:6]
    s.deliverables = [str(x)[:120] for x in (model.get("deliverables") or [])][:6]
    for f in model.get("capabilities") or []:
        if f in FAMILY_RX and f not in s.families:
            s.families.append(f)
    for m in model.get("missing") or []:
        if str(m) not in s.missing:
            s.missing.append(str(m)[:40])
    said = {c.key() for c in words.constraints}
    for text in model.get("constraints") or []:  # (the model's own reading of their limits, if grounded in the words)
        for c in _constraints(str(text)):
            if c.key() not in said and _grounded(c, words.text):
                s.constraints.append(c)
                said.add(c.key())
    for p in model.get("permissions") or []:
        if p in PERMISSIONS and p not in s.permissions:
            s.permissions.append(p)
    if words.budget_usd and model.get("budget_usd") not in (None, words.budget_usd):
        s.conflicts.append(f"budget: they said ${words.budget_usd:g}, the interpretation has ${model.get('budget_usd')}")
    return s


def _grounded(c, text):
    """A constraint the model proposes is kept only if its object / amount appears in the user's own words."""
    low = text.lower()
    if c.kind == "budget":
        return f"{c.amount:g}" in low
    word = (c.target or c.verb or "").strip(".")
    return bool(word) and word in low


def plan_conflicts(spec, steps):
    """Steps of a plan that the words rule out. -> [(step number, why)]"""
    out = []
    for i, st in enumerate(steps, 1):
        for c in spec.constraints:
            try:
                if c.blocks(st.get("tool", ""), st.get("args") or {}):
                    out.append((i, c.text))
                    break
            except Exception:  # noqa: BLE001
                continue
    return out


def second_pass(spec, ask=None):
    """Optional (GOAL_VERIFIER=1): a cheap model re-reads an AMBIGUOUS request. Only its constraints that are grounded
    in the words are added; its missing-information list can add questions, never remove them. -> spec"""
    from room_agent import config

    if not config.GOAL_VERIFIER or not (spec.missing or spec.conflicts or len(spec.families) >= 3):
        return spec
    ask = ask or _ask_cheap_model
    try:
        out = ask(spec.text) or {}
    except Exception:  # noqa: BLE001 (the verifier is optional: its failure changes nothing)
        return spec
    for text in out.get("constraints") or []:
        for c in _constraints(str(text)):
            if c.key() not in spec.constraint_keys() and _grounded(c, spec.text):
                spec.constraints.append(c)
    for m in out.get("missing") or []:
        if str(m) not in spec.missing:
            spec.missing.append(str(m)[:40])
    spec.source += "+verifier"
    return spec


def _ask_cheap_model(text):
    """The cheap model (memory_calls: skipped when today's budget is used up). -> {"constraints": [...], "missing": [...]}"""
    from room_agent.llm.memory_calls import memory_call_tool

    schema = {"type": "object", "properties": {
        "constraints": {"type": "array", "items": {"type": "string"}, "description": "limits the user stated, quoted"},
        "missing": {"type": "array", "items": {"type": "string"}, "description": "information that must be asked"}}}
    return memory_call_tool("Read the user's request. List only limits the user explicitly stated (quote them) and "
                            "information that is missing and must be asked. Do not infer preferences or permissions.",
                            "Request: " + text,
                            {"name": "goal_check", "description": "stated limits and missing information",
                             "input_schema": schema})


# ---------------------------------------------------------------- the turn's intent (read once, at the start of a turn)
@dataclass
class TurnIntent:
    """What this turn's words ask for, read by code once per turn (read_turn -> rt.turn.intent). Routing, the executor
    and the reflex read it instead of re-reading the words; it never grants a permission by itself."""
    text: str = ""
    act: str = ""                 # the kind of turn (conversation/policy.py kind), "" until policy has read it
    op: str = None                # what to do: one of OPS ("turn_on", "close", "cancel_for_good"...), "ask", or None
    scope: str = None             # what it acts on: tab | app | window | device | media | volume | mission | timer |
                                  # memory, or None
    target: str = ""              # the thing named: "chrome", "LED", "mission"...
    value: int = None             # a number / percent said ("50%" -> 50)
    route: str = "conversation"   # missions/goals.route(), "business_mission" only when it really is one
    business: bool = False        # route == "business_mission"
    families: list = field(default_factory=list)  # capability areas the words need (_families)
    negated: bool = False         # the op's verb is negated ("don't close it")
    correction: dict = None       # {"wanted", "not"} from "I said X, not Y" (filled by the corrections layer)
    style_pref: str = None        # how they want Jarvis to talk this turn ("short"...) (filled by the social layer)
    answer: str = None            # "yes" / "no" to what's pending (filled by the confirmation layer)

    def describe(self):
        """One short line for the logs: "op=turn_off scope=device target=LED"."""
        bits = [f"op={self.op}", f"scope={self.scope}"]
        if self.target:
            bits.append(f"target={self.target}")
        if self.value is not None:
            bits.append(f"value={self.value}")
        if self.negated:
            bits.append("negated")
        if self.route and self.route != "conversation":
            bits.append(f"route={self.route}")
        if self.act:
            bits.append(f"act={self.act}")
        return " ".join(bits)


_NOT_CALL = r"(?!(?:at|in|on|when|later|tomorrow|back|tonight|now|if|after|before|again|asap|\d)\b)"
# What to do, from their words: the first that matches wins (order matters: "cancel it for good" before "cancel").
OPS = [(op, re.compile(rx, re.I)) for op, rx in (
    ("cancel_for_good", r"\b(?:cancel|stop|end|kill|abort|delete)\b[^.?!]{0,40}?\b(?:for good|permanently|completely|"
                        r"for ever|forever|altogether)\b"),
    ("pause", r"\bpause\b"),
    ("resume", r"\b(?:resume|unpause|continue|carry on)\b"),
    ("cancel", r"\b(?:cancel|abort|call off|never ?mind)\b"),
    ("stop", r"\b(?:stop|halt)\b"),
    ("close", r"\b(?:close|quit|exit)\b"),
    ("open", r"\b(?:open|launch|bring up|pull up)\b"),
    ("switch", r"\b(?:switch|go|flip)\s+(?:over\s+|back\s+)?to\b"),
    ("search", r"\b(?:search|google|look up)\b"),
    ("research", r"\bresearch\b"),
    ("find", r"\b(?:find|look(?:ing)? for|locate)\b"),
    ("start", r"\b(?:start|begin|kick off|set (?:a|an|the|my) (?:timer|alarm|reminder))\b"),
    ("set", r"\b(?:set|put|make)\b[^.?!]{0,30}?(?:\b(?:to|at)\s+\d|\d+\s*(?:%|percent\b))|"
            r"\bturn\b[^.?!]{0,30}?(?:\bto\s+\d|\d+\s*(?:%|percent\b))"),
    ("raise", r"\b(?:raise|increase|turn (?:\w+\s+){0,2}?up|crank (?:\w+\s+){0,2}?up|bump (?:\w+\s+){0,2}?up|louder|"
              r"brighter)\b"),
    ("lower", r"\b(?:lower|reduce|decrease|turn (?:\w+\s+){0,2}?down|quieter|dimmer|dim)\b"),
    ("turn_on", r"\b(?:turn|switch|flip)\s+(?:[\w.'-]+\s+){0,3}?on\b"),
    ("turn_off", r"\b(?:turn|switch|flip|shut)\s+(?:[\w.'-]+\s+){0,3}?off\b"),
    ("play", r"\bplay\b|\bput on some\b"),
    ("remember", rf"\b(?:remember|forget)\b|\bcall me {_NOT_CALL}\w+"),
    ("report", r"\bhow (?:much|many)\b|\bwhat'?s my usage\b|\bmy usage\b"),
)]
_REPORT = dict(OPS)["report"]
QUESTION = re.compile(r"^(?:(?:hey|ok(?:ay)?|so|and|well|um+|uh+|jarvis)[\s,]+)*(?:is|are|was|were|am|isn'?t|aren'?t|"
                      r"wasn'?t|does|doesn'?t|did|didn'?t|has|have|hasn'?t|what|what'?s|whats|which|who|who'?s|whose|"
                      r"where|where'?s|when|why(?! don'?t you)|how(?! about))\b", re.I)
DEVICE_OPS = ("turn_on", "turn_off", "set", "raise", "lower")
SCOPE_RX = {k: re.compile(v, re.I) for k, v in {
    "tab": r"\btabs?\b|\bthis (?:web ?)?page\b",
    "window": r"\bwindows?\b",
    "timer": r"\b(timers?|alarms?|reminders?|remind me)\b",
    "mission": r"\b(missions?|lead search|business search)\b",
    "volume": r"\b(volume|sound level|louder|quieter|mute|unmute)\b",
    "device": r"\b(LEDs?|lights?|lamps?|strips?|bulbs?)\b(?!\s+(?:mode|theme))",
    "heard_as_led": r"\b(leads?|lids?)\b",  # (speech recognition's "LED": only with a device op)
    "media": r"\b(music|songs?|tracks?|playback|spotify|videos?|podcasts?)\b",
    "app": rf"\b(apps?|programs?|applications?)\b|\b{APPS}\b",
    "memory": rf"\b(remember|forget)\b|\bcall me {_NOT_CALL}(\w+)",
}.items()}
_APP_NAME = re.compile(rf"\b{APPS}\b", re.I)
_SPELLED_LED = re.compile(r"(?<!\w)l\.?\s?e\.?\s?d\b\.?", re.I)
_NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                 "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
                 "ninety": 90, "hundred": 100}
_BUSINESS_OBJECT = re.compile(r"\b(web ?sites?|sites?|demos?|leads?|prospects?|outreach|online presence|facebook page)\b",
                              re.I)
_category_rx = []


def read_turn(text):
    """-> TurnIntent from the user's words alone: regexes, no model call, no I/O, never raises (whatever could be read)."""
    it = TurnIntent(text=" ".join(text.split()) if isinstance(text, str) else "")
    try:
        _read(it)
    except Exception:  # noqa: BLE001 (a bug here must never break a turn: keep what was read)
        pass
    return it


def _read(it):
    t = _SPELLED_LED.sub("LED", it.text)
    if not t:
        return
    m = None
    if QUESTION.search(t):  # ("is the door closed?": a question about it, not a request to close it)
        m = _REPORT.search(t)
        it.op = "report" if m else "ask"
    else:
        for op, rx in OPS:
            m = rx.search(t)
            if m:
                it.op = op
                break
        if it.op is None and t.endswith("?"):
            it.op = "ask"
    it.negated = bool(m and _negated(t, m))
    it.scope, it.target = _scope(t, it.op)
    it.value = _value(t)
    it.families = _families(t)
    it.route = _route(t)
    it.business = it.route == "business_mission"


def _scope(t, op):
    """-> (scope, target) named by the words, or (None, "")."""
    def hit(name):
        return SCOPE_RX[name].search(t)

    app = _APP_NAME.search(t)
    if hit("tab"):
        return "tab", "tab"
    if hit("window"):
        return "window", app.group(0).lower() if app else "window"
    if m := hit("timer"):
        return "timer", "reminder" if m.group(1).lower() == "remind me" else m.group(1).lower().rstrip("s")
    if hit("mission"):
        return "mission", "mission"
    if hit("volume"):
        return "volume", "volume"
    device = _device(t, op)
    if device:
        return "device", device
    if app and op in ("open", "close", "switch", "start"):  # ("close Spotify": the app, not the music)
        return "app", app.group(0).lower()
    if m := hit("media"):
        return "media", m.group(1).lower()
    if hit("app"):
        return "app", app.group(0).lower() if app else ""
    if m := hit("memory"):
        return "memory", (m.group(2) or "").lower()
    return None, ""


def _device(t, op):
    """The device named: a phrase from core.vocabulary() as it's spelled there, "LED" (also when heard as "lead" / "lid"
    with a device op), "light", "lamp"... or ""."""
    from room_agent.actions import core

    names = core.vocabulary()
    for name in sorted(names, key=len, reverse=True):  # (the longest first: "desk lamp" before "lamp")
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", t, re.I):
            return name
    led = next((n for n in names if n.lower() == "led"), "LED")
    m = SCOPE_RX["device"].search(t)
    if m:
        word = m.group(1).lower()
        return led if word.startswith("led") else word.rstrip("s")
    if op in DEVICE_OPS and SCOPE_RX["heard_as_led"].search(t):
        return led
    return ""


def _value(t):
    """A number they said, as an int: a percent first, then "to / at N", then the first number (digits or a word)."""
    for rx in (r"(?<![\d:.,$])(\d{1,4})\s*(?:%|percent\b)", r"\b(?:to|at)\s+(\d{1,4})(?![\d:])",
               r"(?<![\d:.,$])(\d{1,4})(?![\d:])"):
        m = re.search(rx, t, re.I)
        if m:
            return int(m.group(1))
    m = re.search(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", t, re.I)
    return _NUMBER_WORDS[m.group(1).lower()] if m else None


def _route(t):
    """missions/goals.route(), but "business_mission" only when the words name both a kind of business and a website /
    lead / demo object (so "turn on the lead" can't start one)."""
    try:
        from room_agent.missions import goals

        route = goals.route(t)
    except Exception:  # noqa: BLE001
        return "conversation"
    if route == "business_mission" and not (_BUSINESS_OBJECT.search(t) and _business_category(t)):
        route = "conversation"
    return route


def _business_category(t):
    """The kind of business their words name ("restaurants", "plumbers", "businesses"), or ""."""
    from room_agent.missions import goals

    if not _category_rx:  # (goals.py's own category patterns, without its memory lookup)
        loc = r"(?P<loc>[^,.;!?]+?(?:,\s*[A-Z]{2}\b)?)"
        _category_rx.extend(re.compile(rx, re.I) for rx in (
            goals.VERB + rf"(?:{goals.N}\s+)?(?P<cat>[a-z][a-z' &-]*?)\s+(?:in|near|around|within \d+ ?km of)\s+{loc}"
            + goals.STOP,
            r"\bdemo (?:web)?sites? for\s+(?:the best\s+)?" + rf"(?:{goals.N}\s+)?(?P<cat>[a-z][a-z' &-]*?)\s+"
            rf"(?:in|near|around)\s+{loc}" + goals.STOP,
            goals.VERB + rf"(?:{goals.N}\s+)?(?P<cat>[a-z][a-z' &-]*?)\s+(?:that|which|who|with|without|lacking|lacks?|"
            r"missing|having)\b"))
    for rx in _category_rx:
        m = rx.search(t)
        if m:
            cat = re.sub(r"^(?:of|for|about)\s+|^(?:(?:leads?|prospects?|demos?(?: sites?)?|web ?sites?)\s+for\s+)", "",
                         goals._clean_cat(m.group("cat")))
            if cat and not _BUSINESS_OBJECT.fullmatch(cat) and cat not in ("clients", "customers"):
                return cat
    return ""
