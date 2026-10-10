"""PendingAction: a request that's understood but not complete yet ("send an email to Adam": no subject, no text).

Instead of forgetting it or starting over, Jarvis keeps ONE pending action, fills it in turn by turn, and runs it once
nothing required is missing. Generic: what's required comes from the capability's own parameter schema; a capability
can add (all optional, in its schema properties):

    "x-ask":          the question for that parameter ("What's the subject?")
    "x-cues":         regexes that name the parameter in a correction ("change the subject to ...", "send it to ...")
    "x-answer-cues":  regexes stripped from an answer to that parameter while it's being asked ("say ...")
    "x-text":         True for free text (kept word for word; "remove that last sentence" edits it)
    "format": "email" spoken addresses are normalized (room_agent/emails.py) and must come from the user
and at schema level "x-optional-when": {"to": "reply_to"} (to isn't required when reply_to is given).

Who decides what:
    code (here, before any model call)   cancel ("never mind"), answers to the question just asked, corrections that
                                         name a parameter, "remove that last sentence", "is that address right?"
    the model                            fuzzy edits ("no, say 3 PM"), unrelated questions in between, wording replies
    the executor                         merges what was collected into the call, runs the usual checks and the
                                         existing permission rules (confirmation before sending, deleting...)

rt.pending holds the PendingAction; it still answers to the old dict keys (p["tool"], p["args"], p["missing"],
p["turn"], p["at"], p.get("confirm")) so nothing that used them had to change.
"""

import logging
import random
import re
import time
from email.utils import getaddresses

from room_agent import emails
from room_agent import runtime as rt

log = logging.getLogger("room-agent")
COLLECT_TURNS, COLLECT_SECONDS = 3, 300   # a request being filled in lives this long after its last update
CONFIRM_TURNS, CONFIRM_SECONDS = 3, 180   # a yes/no question about one action
TRUSTED_EMAILS = set()                    # addresses the user said clearly or confirmed this session
_staged = {}                              # what the current call learned before its PendingAction exists

CANCEL = re.compile(r"^\W*(?:(?:no|nah|nope|oh|okay|ok|actually|wait|um+|uh+|hmm+|jarvis)[\s,.!]+)*"
                    r"(?:never ?mind|forget (?:it|that|about (?:it|that))|cancel(?: that| it| the \w+)?|scratch that|"
                    r"drop it|don'?t (?:bother|send it|do (?:it|that))|stop(?: that)?)"
                    r"(?:[\s,.!]+(?:please|jarvis|thanks|thank you))*\W*$", re.I)
REMOVE_LAST = re.compile(r"^\W*(?:(?:actually|no|okay|ok|and|also|oh)[\s,.!]+)*(?:remove|delete|drop|take out|cut|"
                         r"get rid of)\s+(?:that|the)\s+last\s+(?:sentence|line|part|bit)\W*$", re.I)
NEW_REQUEST = re.compile(r"^\W*(?:hey\s+)?jarvis\b|^\W*(?:what(?:'s| is) the (?:time|weather|date)|set (?:a|an|my) "
                         r"(?:timer|alarm)|open |close |play |pause|resume|turn (?:on|off|up|down|the)|volume|mute|unmute|"
                         r"remind me|wake me|good morning)", re.I)
CORRECTING = re.compile(r"^\W*(?:no|nope|nah|actually|wait|oh wait|sorry|instead|scratch that)\b(?![\s.!]*$)", re.I)
PREFIX = (r"^\W*(?:(?:actually|no|nope|oh|okay|ok|and|also|wait|sorry|um+|uh+|then|so|but|instead|hmm+)[\s,.!]+)*"
          r"(?:(?:change|make|set|update|switch|put|use)\s+(?:the\s+)?)?")
SEP = r"(?:\s+(?:to|is|as|should be|will be|would be)\s+|\s*[:=]\s*|\s+)"
FILLER = re.compile(r"^\W*(?:(?:it'?s|it is|that'?s|that is|make it|let'?s say|maybe|um+|uh+|ok(?:ay)?|so)[\s,]+)+", re.I)
CANCELLED = ["Okay, cancelled.", "Sure, forget it.", "Okay, dropped it.", "No problem, I won't."]
# A short no to a yes/no question about an action ("No.", "Nope, leave it", "not now")
NO_ANSWER = re.compile(r"^\W*(?:(?:oh|um+|uh+|well|jarvis)[\s,.!]+)*(?:no|nope|nah|don'?t|do not|not (?:now|yet)|"
                       r"no thanks?|never ?mind|leave it|forget it)\b", re.I)
HOLD = re.compile(r"\b(?:wait|hold on|hang on|one sec(?:ond)?|a sec(?:ond)?|let me think)\b", re.I)  # (not a no: later)


class Decision:
    """What code decided about an utterance: `reply` = say this, no model call; `note` = let the model answer, with
    this system note added to the user's message."""

    def __init__(self, reply=None, note=None):
        self.reply, self.note = reply, note


class PendingAction:
    _KEYS = {"tool": "capability", "args": "collected", "missing": "missing", "turn": "touched_turn",
             "at": "touched_at", "confirm": "confirm"}

    def __init__(self, capability, collected=None, missing=(), confirm=False):
        now = time.time()
        self.capability = capability
        self.collected = _nonempty(collected)
        self.missing = list(missing)
        self.resolved = {}          # param -> {"said", "value", "how"}: how a value was worked out (a name -> address)
        self.source_turn = rt.turn_no
        self.source_text = rt.turn_text or ""
        self.created_at = now
        self.touched_turn, self.touched_at = rt.turn_no, now
        self.confirm = confirm      # True: waiting for a yes/no about running it (the executor's rules)
        self.status = "confirming" if confirm else "collecting"  # collecting | confirming | done | cancelled
        self.asking = self.missing[0] if self.missing else None  # the parameter Jarvis is asking for now
        self.candidate = None       # {"param", "email", "confidence"}: an address read back, waiting for "yes"
        self.names = {}             # param -> a name they said ("Adam") whose address isn't known yet
        self.last_text = None       # the free-text parameter filled most recently

    @property
    def expires_at(self):
        return self.touched_at + (CONFIRM_SECONDS if self.confirm else COLLECT_SECONDS)

    def expired(self):
        turns = CONFIRM_TURNS if self.confirm else COLLECT_TURNS
        return rt.turn_no - self.touched_turn > turns or time.time() > self.expires_at

    def touch(self):
        self.touched_turn, self.touched_at = rt.turn_no, time.time()

    # (the old dict interface)
    def __getitem__(self, key):
        return getattr(self, self._KEYS[key])

    def __setitem__(self, key, value):
        setattr(self, self._KEYS[key], value)

    def get(self, key, default=None):
        return getattr(self, self._KEYS[key]) if key in self._KEYS else default

    def __repr__(self):
        return (f"PendingAction({self.capability}, {self.status}, have={sorted(self.collected)}, missing={self.missing}, "
                f"asking={self.asking})")


# ---------------------------------------------------------------- schema helpers
def _nonempty(args):
    return {k: v for k, v in (args or {}).items() if k != "confidence" and not _empty(v)}


def _empty(v):
    return v is None or (isinstance(v, str) and not v.strip())


def schema(name):
    from room_agent.actions import core

    cap = core.get(name)
    return cap.parameters if cap else {}


def props(name):
    return schema(name).get("properties", {})


def required(sch, args):
    """The schema's required parameters, minus those made optional by another one being given (x-optional-when)."""
    optional_when = sch.get("x-optional-when", {})
    return [k for k in sch.get("required", [])
            if not (optional_when.get(k) and not _empty((args or {}).get(optional_when[k])))]


def missing_of(name, args):
    return [k for k in required(schema(name), args) if _empty((args or {}).get(k))]


def collectable(name, missing=None):
    """Can this capability's missing parameters be collected turn by turn (each has an x-ask question)? Those don't wait
    in silence for the user to go on: the request is kept and the first missing thing is asked for."""
    p = props(name)
    keys = [k for k in (missing if missing is not None else required(schema(name), {})) if k in p]
    return bool(keys) and all(p[k].get("x-ask") for k in keys)


def collectable_tools():
    from room_agent.actions import core

    return [c.name for c in core.capabilities() if collectable(c.name)]


def call_hint(text):
    """A one-line note for the model when the user's words ask for a capability that collects its details (its own
    intent pattern matches) and nothing is pending: call it now with what was said, don't ask first. Else ''."""
    from room_agent.actions import core

    if current():
        return ""
    offered = {t["name"] for t in core.offered()}
    names = [c.name for c in core.capabilities() if c.name in offered and c.intent is not None and collectable(c.name)
             and c.intent.search(text or "")]
    if not names:
        return ""
    return (f" (System note from code: if they're asking for {' / '.join(names)}, call it now with only what they said; "
            "it keeps the request and tells you the one thing to ask. Don't ask for details or optional extras first.)")


def ask_for(p, param):
    spec = props(p.capability).get(param, {})
    if p.names.get(param):
        return f"What's {p.names[param]}'s email address?"
    return spec.get("x-ask") or f"What {param.replace('_', ' ')} should I use?"


# ---------------------------------------------------------------- lifecycle (called by validate / the executor)
def current():
    p = rt.pending
    if p is not None and isinstance(p, PendingAction) and (p.status in ("done", "cancelled") or p.expired()):
        if p.status not in ("done", "cancelled"):
            log.info("pending action expired: %s", p.capability)
        rt.pending = p = None
    return p


def collecting(name, collected, missing):
    """A request lacks required parameters: start a PendingAction, or update the one for this capability."""
    p = current()
    if p and p.capability == name and not p.confirm:
        p.collected.update(_nonempty(collected))
        p.missing = list(missing)
        p.touch()
    else:
        p = rt.pending = PendingAction(name, collected, missing)
        log.info("pending action: %s (have %s, missing %s)", name, sorted(p.collected), p.missing)
    for param, cand in list(_staged.pop("candidates", {}).items()):
        p.candidate = {"param": param, **cand}
    p.names.update(_staged.pop("names", {}))
    if p.candidate and p.candidate["param"] not in p.missing:
        p.missing.insert(0, p.candidate["param"])
    p.asking = p.missing[0] if p.missing else None
    return p


def confirming(name, args):
    """An action that waits for the user's own yes (the executor's risk and intent rules)."""
    rt.pending = PendingAction(name, args, confirm=True)
    return rt.pending


def finished(name):
    p = rt.pending
    if p is not None and p["tool"] == name:
        if isinstance(p, PendingAction):
            p.status = "done"
            log.info("pending action done: %s", name)
        rt.pending = None


def cancel(why=""):
    p = rt.pending
    if p is not None:
        if isinstance(p, PendingAction):
            p.status = "cancelled"
        log.info("pending action cancelled: %s (%s)", p["tool"], why)
    rt.pending = None
    _staged.clear()


def needs(param, message):
    """For a tool that finds out at run time that a parameter is missing or unusable (an unknown recipient):
    returns its NEEDS result and makes the executor keep the request pending with that parameter missing."""
    _staged["needs"] = param
    return f"NEEDS: {message}"


def take_needed():
    return _staged.pop("needs", None)


def take_rejected():
    """Addresses the last prepare() refused because they didn't come from the user (never used, never guessed)."""
    return _staged.pop("rejected", [])


def needs_message(p, what):
    """The tool result for a request that's still missing something: ask for exactly one thing."""
    if p.candidate:
        return (f"NEEDS: confirmation of the email address. Nothing was done yet. Ask exactly: \"I heard "
                f"{emails.spoken_form(p.candidate['email'])}. Is that right?\"")
    return (f"NEEDS: {what}. Nothing was done yet; what they already told you is kept. Ask only for {p.asking} now, in "
            f"one short question, like: \"{ask_for(p, p.asking)}\"")


def source_matches(name, pattern):
    """Did the request this pending action started from ask for it in the user's own words? (intent checks)"""
    p = current()
    return bool(p and p.capability == name and pattern.search(p.source_text or ""))


def private_now():
    from room_agent.actions import core

    p = rt.pending
    return bool(p and getattr(core.get(p["tool"]), "private", False))


# ---------------------------------------------------------------- merging a call with what was collected
def prepare(name, args):
    """Called by the executor before checks: fill in what was already collected for this capability (what the call
    says now wins), and accept email-format parameters only from the user's own words or a known contact."""
    _staged.clear()
    p = current()
    given = {k: v for k, v in args.items() if not _empty(v)}
    if p and p.capability == name and not p.confirm:
        changed = {k for k, v in given.items() if p.collected.get(k) != v}
        args = {**p.collected, **given}
    else:
        changed = set(given)
    for param, spec in props(name).items():
        if spec.get("format") == "email" and param in changed and not _empty(args.get(param)):
            value = _verified_addresses(param, str(args[param]))
            if value:
                args[param] = value
                if p and p.capability == name:
                    p.names.pop(param, None)
            else:
                args.pop(param, None)
                if p and p.capability == name:
                    p.collected.pop(param, None)
    return args


def _recent_user_text():
    texts = [rt.turn_text or ""] + [m.get("text", "") for m in list(rt.recent)[-6:] if m.get("role") == "user"]
    return [t for t in texts if t]


def _known(addr):
    from room_agent.actions.context import env

    return addr in TRUSTED_EMAILS or addr in env.known_addresses


def _verified_addresses(param, value):
    """'adam@gmail.com' / 'Adam <adam@gmail.com>' / 'Adam' from a model call -> the addresses that really came from
    the user (said clearly, confirmed) or from a known contact; '' if none did. Never a guess."""
    from room_agent.actions.context import env

    out = []
    for name, addr in getaddresses([value]):
        addr = (addr or "").strip().lower()
        if "@" in addr:
            if _known(addr):
                out.append(addr)
                continue
            heard = None
            for text in _recent_user_text():
                heard = next((c for c in emails.find(text, expecting=True) if c["email"] == addr), None)
                if heard:
                    break
            if heard and heard["confidence"] >= emails.ACCEPT:
                TRUSTED_EMAILS.add(addr)
                emails.log_resolution(text, heard, "accepted (they said it)")
                out.append(addr)
            elif heard and heard["confidence"] >= emails.CONFIRM:
                _staged.setdefault("candidates", {})[param] = {"email": addr, "confidence": heard["confidence"]}
                emails.log_resolution(text, heard, "needs confirmation")
            else:
                _staged.setdefault("rejected", []).append(addr)
                emails.log_resolution(rt.turn_text or "", {"email": addr, "confidence": 0.0},
                                      "rejected: not something they said, and not a known contact")
        else:
            said = (name or addr).strip()
            hits = sorted(a for a in env.known_addresses if emails.name_matches_address(said, a))
            if len(hits) == 1:
                out.append(hits[0])
                log.info("email: %r -> %s (a known contact)", said, hits[0])
            elif said:
                _staged.setdefault("names", {})[param] = said.split()[0].title()
                log.info("email: %r -> no known address (%d matches): will ask", said, len(hits))
    return ", ".join(out)


# ---------------------------------------------------------------- the user's next words, while something is pending
def on_utterance(text):
    """Decide in code what an utterance does to the pending action, before any model call. -> Decision or None."""
    p = current()
    if not p:
        return None
    t = text.strip()
    if p.confirm:  # a yes/no about running it: the executor's confirmation rules take a yes ("Cancel the mission." to
        from room_agent.actions import core  # "cancel it for good?" is one, not a "never mind")
        from room_agent.actions.executor import said_yes

        cap = core.get(p.capability)
        try:
            what = cap.describe(p.collected) if cap is not None and cap.describe else ""
        except Exception:  # noqa: BLE001
            what = ""
        if said_yes(t, cap, what):
            return None
        if CANCEL.match(t) or (len(t.split()) <= 6 and "?" not in t and NO_ANSWER.match(t) and not HOLD.search(t)):
            cancel(f"they said {t!r}")
            return Decision(reply="Okay, I won't.")  # (never "cancelled": nothing was done)
        return None
    if CANCEL.match(t):
        cancel(f"they said {t!r}")
        return Decision(reply=random.choice(CANCELLED))
    if p.candidate:
        return _answer_to_candidate(p, t)
    hit = _cue(p, t)
    if hit:
        return _accept(p, *hit) or _advance(p)
    if REMOVE_LAST.match(t):
        param = p.last_text or next((k for k, s in props(p.capability).items() if s.get("x-text") and p.collected.get(k)), None)
        if param and p.collected.get(param):
            sentences = re.split(r"(?<=[.!?])\s+", p.collected[param].strip())
            p.collected[param] = " ".join(sentences[:-1]).strip()
            if not p.collected[param]:
                p.collected.pop(param)
            log.info("pending action: removed the last sentence of %s", param)
            return _advance(p)
        return None
    if CORRECTING.match(t):
        return None  # "no, say 3 PM": a change to something already given, not the answer to the question (the model)
    if p.asking and not _new_request(p, t) and _fits(p, p.asking, t):
        return _accept(p, p.asking, _strip_answer(p, p.asking, t)) or _advance(p)
    return None  # something else (a question in between, a fuzzy edit): the model answers, with the pending context


def _new_request(p, t):
    # A question is never taken as the answer by code, even for free text ("What time is it?" while the email's text is
    # being asked for): the model decides, with the pending context, whether it's meant as the text or asked of Jarvis.
    return bool(NEW_REQUEST.search(t)) or t.rstrip().endswith("?")


NOT_NAMES = emails.COMMON | {"to", "with", "for", "of", "in", "on", "reply", "send", "email", "mail", "write", "draft",
                             "about", "please", "can", "could", "would", "what", "why", "how", "yes", "no", "yeah"}


def _name_like(value):
    """'Adam', 'Sarah Lee': a person's name (at most three words, none of them ordinary sentence words)."""
    words = re.findall(r"[A-Za-z']+", value)
    return (bool(words) and len(words) <= 3 and re.fullmatch(r"[A-Za-z][A-Za-z' .-]{0,40}", value.strip().rstrip("."))
            and not any(w.lower() in NOT_NAMES for w in words))


def _fits(p, param, t):
    """Could this be the answer to `param`? An address question answered with a whole unrelated sentence isn't one
    (the model gets it, with the pending context); a short or address-like answer is, even a garbled one (asked again)."""
    if props(p.capability).get(param, {}).get("format") != "email":
        return True
    return bool(emails.find(t, expecting=True)) or _name_like(t) or len(t.split()) <= 6 and " at " in f" {t.lower()} "


def _clean_value(p, param, value):
    value = value.strip().strip("\"'").strip()
    if not props(p.capability).get(param, {}).get("x-text"):
        value = value.rstrip(".!,;").strip()
    return value


def _strip_answer(p, param, t):
    spec = props(p.capability).get(param, {})
    for cue in spec.get("x-answer-cues", []) + spec.get("x-cues", []):
        m = re.match(PREFIX + rf"(?:{cue}){SEP}(?P<v>.+)$", t, re.I | re.S)
        if m:
            return _clean_value(p, param, m["v"])
    if not spec.get("x-text") and spec.get("format") != "email":
        t = FILLER.sub("", t)
    return _clean_value(p, param, t)


def _cue(p, t):
    """A correction or answer that names its parameter: 'change the subject to Friday', 'send it to sam at ...'."""
    for param, spec in props(p.capability).items():
        cues = list(spec.get("x-cues", [])) + (list(spec.get("x-answer-cues", [])) if param == p.asking else [])
        for cue in cues:
            m = re.match(PREFIX + rf"(?:{cue}){SEP}(?P<v>.+)$", t, re.I | re.S)
            if m and m["v"].strip():
                return param, _clean_value(p, param, m["v"])
    return None


def _accept(p, param, value):
    """Store an answer for `param`. -> a Decision when something has to be asked first, else None."""
    spec = props(p.capability).get(param, {})
    p.touch()
    if spec.get("format") == "email":
        cands = emails.find(value, expecting=True)
        if len({c["email"] for c in cands if c["confidence"] >= emails.CONFIRM}) > 1:
            emails.log_resolution(value, cands[0], "ambiguous: more than one address")
            p.asking = param
            return Decision(reply="I heard more than one address there. Which one should I use?")
        if cands and cands[0]["confidence"] >= emails.ACCEPT:
            c = cands[0]
            TRUSTED_EMAILS.add(c["email"])
            emails.log_resolution(value, c, "accepted")
            p.collected[param] = c["email"]
            p.resolved[param] = {"said": value, "value": c["email"], "how": "spoken address"}
            p.names.pop(param, None)
            return None
        if cands and cands[0]["confidence"] >= emails.CONFIRM:
            c = cands[0]
            emails.log_resolution(value, c, "needs confirmation")
            p.candidate = {"param": param, "email": c["email"], "confidence": c["confidence"]}
            p.asking = param
            return Decision(reply=f"I heard {emails.spoken_form(c['email'])}. Is that right?")
        found = _verified_addresses(param, value.strip().rstrip(".")) if _name_like(value) else ""
        if found:
            p.collected[param] = found
            p.resolved[param] = {"said": value, "value": found, "how": "known contact"}
            return None
        p.names.update(_staged.pop("names", {}))
        p.collected.pop(param, None)
        p.asking = param
        emails.log_resolution(value, None, "no usable address: asking again (never guessed)")
        if p.names.get(param):
            return Decision(reply=ask_for(p, param))
        return Decision(reply="Sorry, I didn't catch a full email address. Could you say it again? Like john dot smith "
                              "at gmail dot com.")
    p.collected[param] = value
    if spec.get("x-text"):
        p.last_text = param
    log.info("pending action %s: %s filled", p.capability, param)
    return None


def _answer_to_candidate(p, t):
    from room_agent.actions.executor import AFFIRM, NEGATE

    c, p.candidate = p.candidate, None
    if AFFIRM.search(t) and not NEGATE.search(t):
        TRUSTED_EMAILS.add(c["email"])
        p.collected[c["param"]] = c["email"]
        p.resolved[c["param"]] = {"said": t, "value": c["email"], "how": "read back and confirmed"}
        p.names.pop(c["param"], None)
        emails.log_resolution(t, c, "confirmed by the user")
        return _advance(p)
    emails.log_resolution(t, c, "rejected by the user")
    if emails.find(t, expecting=True):  # "no, it's adam dot azzouz at ..."
        return _accept(p, c["param"], t) or _advance(p)
    p.asking = c["param"]
    return Decision(reply="Okay, what's the address then? You can spell it out.")


def _advance(p):
    """After an update: ask for the next missing thing, or let the model run it now that everything is there."""
    p.missing = missing_of(p.capability, p.collected)
    p.touch()
    if p.missing:
        p.asking = p.missing[0]
        return Decision(reply=random.choice(["Got it. ", "Okay. ", ""]) + ask_for(p, p.asking))
    p.asking = None
    have = "; ".join(f"{k}={_short(v)}" for k, v in p.collected.items())
    return Decision(note=f" (System note from code, not from them: everything {p.capability} needs is collected now: "
                         f"{have}. Call {p.capability} now; you may leave its arguments out, the collected values are "
                         "filled in automatically. Then tell them in a few words what happened.)")


def _short(v, n=120):
    v = str(v)
    return v if len(v) <= n else v[:n] + "..."


# ---------------------------------------------------------------- what the model and the speech recognizer are told
def context_line():
    p = current()
    if not p:
        return None
    if p.confirm:
        return (f"- pending_request: {p.capability} with {p.collected or 'nothing yet'}, waiting for a yes/no confirmation. "
                "If their next message answers it, act on it; if it's about something else, forget this.")
    have = "; ".join(f"{k}={_short(v)}" for k, v in p.collected.items()) or "nothing yet"
    return (f"- pending_request (a pending action, kept by code): {p.capability}, started from {p.source_text[:100]!r}. "
            f"Collected: {have}. Still missing: {', '.join(p.missing) or 'nothing'}"
            + (f"; you are waiting for: {p.asking}" if p.asking else "")
            + f". Their message most likely answers or changes THIS request: never start it over or treat it as "
            f"unrelated. A correction (another recipient, 'no, say 3 PM', a new subject) means: call {p.capability} with "
            "only the changed parameter(s); everything collected is filled in automatically. Ask only for what's still "
            "missing, one thing at a time. If they clearly moved on to something else, just answer that.")


def stt_hint():
    """A hint for speech recognition while an email address is expected (Whisper's initial prompt), else None."""
    p = current()
    if not p or p.confirm:
        return None
    param = (p.candidate or {}).get("param") or p.asking
    if param and props(p.capability).get(param, {}).get("format") == "email":
        return "An email address, said the way people say it, like john dot smith at gmail dot com."
    return None
