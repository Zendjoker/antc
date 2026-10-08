"""
Keeping the agent honest about itself.

The LLM is the conversational layer, not the source of truth about the agent. This module holds:

  capabilities()            what the agent can and can't do right now: built from the capability registry (each area
                            in room_agent/abilities/ says what it can do and whether it works now). Put in front of
                            the model on every request; tools for unavailable areas aren't even offered.
  ClaimGuard                a gate in front of the speaker. A sentence that claims an action or a state
                            ("saved it", "the lights are off", "timer's set", "I opened Spotify", "I don't
                            have anything stored about you") is only spoken if a tool result in the same turn
                            confirms it (or the stored state backs it). Otherwise it's held back, never spoken,
                            and the model is made to answer again truthfully. Each area registers the claims it can
                            confirm (actions/core.register_claim); the generic ones are below.

Global rule: REQUEST -> CHECK CAPABILITY -> EXECUTE TOOL -> VERIFY RESULT -> RESPOND.
Tool results start with OK:, FAILED: or UNAVAILABLE:, so success is never ambiguous.
"""

import re
from dataclasses import dataclass


@dataclass
class Capability:
    name: str
    available: bool
    detail: str


NOT_BUILT = [Capability("texting or calling other people", False, "not built"), Capability("seeing (camera)", False, "not built")]


def capabilities():
    """Everything the agent might be asked about, available or not, from the registry (never the model's opinion)."""
    from room_agent.actions import core

    return core.summaries(Capability) + NOT_BUILT


def render_registry(caps):
    yes = [c for c in caps if c.available]
    no = [c for c in caps if not c.available]
    return ("- available_capabilities: " + "; ".join(f"{c.name} ({c.detail})" for c in yes) + "\n"
            "- NOT available (if asked, say plainly you can't do that; never pretend): "
            + "; ".join(f"{c.name} ({c.detail})" for c in no))


ACTION_TOOLS = set()  # capabilities that change something (filled in by actions/core.register)
NEGATION = re.compile(r"\b(not|n't|never|couldn'?t|can'?t|cannot|unable|wasn'?t|isn'?t|didn'?t|won'?t be able|haven'?t|"
                      r"hasn'?t|no longer|failed|without)\b", re.I)
# A result that says nothing changed ("OK: nothing set matched", "OK: it was already muted") confirms no action claim.
NO_CHANGE = re.compile(r"^OK:\s*(nothing\b|no (timers|app|windows)\b|.*\bnothing (to|was|set|matched)\b|.*\bis already at\b)",
                       re.I)

# The model once invented a timer minimum ('can't do five seconds, a minute or longer'); the tool has none.
TIMER_LIMIT = re.compile(
    r"\b(can'?t|cannot|can not|won'?t|unable to|not able to|isn'?t possible)\b.{0,40}\b(seconds|1 second)\b"
    r"|\b(timers?|alarms?|reminders?)\b.{0,40}\b(at least|minimum|no (shorter|less) than)\b"
    r"|\b(at least|minimum of|no less than)\s+(a|one|1)\s+minute\b"
    r"|\b(a|one|1) minute or (longer|more)\b", re.I)

# Generic claims (the areas add their own: "timer", "app", "media", "memory_save", "home", "message"...)
CLAIMS = [
    ("timer_limit", TIMER_LIMIT.pattern),
    ("future_action", r"\bi'?ll (open|launch|play|start|close|shut down|restart|turn (it |the volume )?(up|down)|mute|put on|queue)\b"),
    ("access", r"\bi (can|could) (see|access|check|read|look at|get into|pull up)\b.{0,25}\b(your\s+)?(calendar|email|emails|inbox|"
               r"messages|texts|files|screen|camera|phone|contacts|photos|browser|history|bank)\b"
               r"|\bi have access to\b.{0,25}\b(calendar|email|messages|files|screen|camera|phone|contacts)\b"),
    ("done", r"^(okay,?\s+|ok,?\s+|alright,?\s+|sure,?\s+|no problem,?\s+|not a problem,?\s+)?(done|all set|all done|taken care "
             r"of|consider it done)\b"),
    # claims about what's stored, checked against the real store
    ("memory_empty", r"\b(don'?t|do not)\s+(have|know)\s+(anything|much|any(thing)? (saved|stored))\b.{0,30}\b(about you|on you|yet|saved|stored)\b"
                     r"|\bnothing (saved|stored)\b|\bno (memories|saved memories)\b|\bmy memory is empty\b"),
    ("no_location", r"\b(don'?t|do not)\s+(know|have)\s+(where you live|your (location|city|address|home))\b"),
    # "it's running / it's ready / works now": only checked when an action failed this turn and nothing made it good
    ("outcome", r"\b(?:it|that|this|everything|the \w+|your \w+)(?:'s| is| are)\s+(?:now\s+)?(?:up and running|running|up|working|"
                r"ready|fixed|started|set up|good to go|done)\b|\bworks now\b|\b(?:fixed|started|got) it\b(?! to)|^(?:fixed|started)\b"),
]
CLAIMS = [(kind, re.compile(rx, re.I)) for kind, rx in CLAIMS]
# nothing can confirm these (no tool does them), so such a claim is always false; "done" is checked specially
VERIFIED_BY = {"timer_limit": set(), "future_action": set(), "access": set(), "done": set()}


def add_claim(kind, pattern, verified_by=()):
    if kind not in {k for k, _ in CLAIMS}:
        CLAIMS.append((kind, re.compile(pattern, re.I)))
    VERIFIED_BY.setdefault(kind, set()).update(verified_by)


def _claim_part(sentence):
    """The part of a sentence that can make a claim. "Timer's set, want another?" -> "Timer's set"; a sentence that's
    only a question ("Want me to set it?") claims nothing."""
    s = sentence.strip()
    if not s.endswith("?"):
        return s
    cut = max(s.rfind(sep, 0, len(s) - 1) for sep in (",", ";", ":", " - ", ". ", "! "))
    return s[:cut].strip() if cut > 0 else ""


OFFER = re.compile(r"\b(can|could|would|might|if|want me to|should i|shall i|happy to|able to|offer to|ready to|let me know)\b",
                   re.I)
NOT_OFFERS = {"access", "timer_limit", "memory_empty", "no_location", "future_action"}  # (here "I can..." IS the claim)


def _offered(s, m):
    """'If you tell me how long, I can set a timer' offers to do it: it doesn't claim it was done."""
    start = max(0, m.start() - 40)
    before = s[start:m.start()]
    clause_start = max(before.rfind(sep) for sep in (";", ".", "!", " - ", " but "))
    return bool(OFFER.search(before[clause_start + 1:] + s[m.start():m.end()]))


def _negated(s, m):
    """'I couldn't turn it off' is honest. Only a negation in the same clause, right before or inside the claim counts:
    in "Not a problem, I've saved it" the "not" belongs to another clause."""
    start = max(0, m.start() - 25)
    before = s[start:m.start()]
    clause_start = max(before.rfind(sep) for sep in (",", ";", ".", "!", " - "))
    return bool(NEGATION.search(before[clause_start + 1:] + s[m.start():m.end()]))


# Offers and promises to reach their phone: no tool does that (phone mode only calls by itself while they drive), so
# "want me to ring your phone?" is false even as a question. "I can't call you" (negated) and the real feature (a call
# while they're driving) are fine.
PHONE_OFFER = re.compile(
    r"\b(i'?ll|i will|i can|i could|let me|want me to|should i|shall i|how about i|i'?d be happy to|i'?m able to|happy to)"
    r"\s+(?:\w+\s+){0,3}?(call|ring|text|phone|buzz|ping|notify|message)\s+(you|your)\b"
    r"|\b(alarm|reminder|notification|alert|push notification)s?\b.{0,25}\bon your (phone|iphone|cell|mobile)\b"
    r"|\b(send|push)\b.{0,25}\b(to|on) your (phone|iphone|cell|mobile)\b", re.I)
DRIVING_CALL = re.compile(r"\b(driv|on the road|in the car)", re.I)

# Offers to operate a kind of device: only true if such a device is actually connected (registered checks below).
_OFFER_LEAD = r"\b(i'?ll|i will|i can|i could|let me|want me to|should i|shall i|how about i|i'?d be happy to|happy to)\s+(?:\w+\s+){0,3}?"
DEVICE_OFFERS = {
    "window": re.compile(_OFFER_LEAD + r"(open|close|crack|shut)\s+(?:up\s+)?(?:the\s+|a\s+|your\s+)?(window|windows|blinds|curtains|shades)\b", re.I),
    "climate": re.compile(_OFFER_LEAD + r"(?:(cool|heat|warm)\s+(?:it|things|the room|the place|you)?\s*(down|up)\b|"
                          r"(turn|switch|crank|put)\s+(on|up|down|off)?\s*(?:the\s+)?(ac|a/?c|air ?con\w*|heat(er|ing)?|fan|thermostat)\b|"
                          r"(lower|raise|set)\s+(?:the\s+)?(temperature|thermostat))", re.I),
    "lock": re.compile(_OFFER_LEAD + r"(lock|unlock)\s+(?:the\s+|your\s+)?(door|front door|doors)\b", re.I),
}
DEVICE_AVAILABLE = {}  # kind -> callable: is a device of this kind connected? (filled in by the areas that control one)


# After a clear command, asking permission for it ("Want me to turn it off?") instead of doing it
PERMISSION_Q = re.compile(r"\b(want me to|do you want me to|should i|shall i|would you like me to|you want me to|"
                          r"want me to go ahead|should i go ahead)\b[^?]*\?", re.I)


def _asked_permission_instead(sentence, tools_called):
    from room_agent import runtime as rt

    policy = getattr(rt.turn, "policy", None)
    return (getattr(policy, "kind", "") == "direct command" and not (tools_called & ACTION_TOOLS)
            and bool(PERMISSION_Q.search(sentence)))


def _device_available(kind):
    try:
        return any(bool(fn()) for fn in DEVICE_AVAILABLE.get(kind, []))
    except Exception:
        return False


class ClaimGuard:
    """Per-turn gate between the model's words and the speaker."""

    def __init__(self, memory_count, home_known):
        self.memory_count = memory_count  # callables, so the check sees the store as it is right now
        self.home_known = home_known
        self.ok_tools = set()
        self.tools_called = set()
        self.failed_actions = set()  # actions that failed this turn and haven't succeeded since
        self.held = []

    def tool_result(self, name, result):
        self.tools_called.add(name)
        if str(result).startswith("OK") and not NO_CHANGE.match(str(result)):
            self.ok_tools.add(name)
            self.failed_actions.discard(name)
        elif name in ACTION_TOOLS and str(result).startswith("FAILED") and "they said" not in str(result):
            self.failed_actions.add(name)  # (a refusal because they said not to isn't a failure)

    def _done_confirmed(self):
        """"Done" / "all set" only when every action tried this turn worked (one failed means it isn't all done)."""
        tried = self.tools_called & ACTION_TOOLS
        return bool(tried) and tried <= self.ok_tools

    def unverified(self, sentence):
        """The kinds of claims in this sentence that nothing confirms. [] = fine to say."""
        m = PHONE_OFFER.search(sentence)
        if m and not _negated(sentence, m) and not DRIVING_CALL.search(sentence):
            return ["phone_offer"]  # (checked on the whole sentence: an offer phrased as a question is still false)
        if _asked_permission_instead(sentence, self.tools_called):
            return ["permission_question"]  # (they already asked: the answer is to do it, not to ask again)
        for kind, rx in DEVICE_OFFERS.items():
            m = rx.search(sentence)
            if m and not _negated(sentence, m) and not _device_available(kind):
                return [f"no_{kind}_device"]  # (e.g. "want me to open the window?" with no window controller)
        s = _claim_part(sentence)
        if not s:
            return []  # a question isn't a claim
        out = []
        for kind, rx in CLAIMS:
            m = rx.search(s)
            if not m:
                continue
            if kind == "memory_empty":
                if self.memory_count() > 0 and "recall" not in self.tools_called:
                    out.append(kind)  # says it knows nothing, but things are stored
                continue
            if kind == "no_location":
                if self.home_known():
                    out.append(kind)
                continue
            if kind == "timer_limit":
                out.append(kind)  # no tool result can make a made-up limit true
                continue
            if kind == "outcome":
                if self.failed_actions and not _negated(s, m):
                    out.append(kind)  # something failed and nothing fixed it: "it's running" can't be true
                continue
            if _negated(s, m) or (kind not in NOT_OFFERS and _offered(s, m)):
                continue
            if kind == "done":
                if not self._done_confirmed():
                    out.append(kind)
                continue
            if not (VERIFIED_BY.get(kind, set()) & self.ok_tools):
                out.append(kind)
        return out

    def admit(self, sentence):
        """True: speak it now. False: held (it, or something before it, isn't verified yet)."""
        if self.held or self.unverified(sentence):
            self.held.append(sentence)
            return False
        return True

    def release(self):
        """Held sentences that are now all confirmed (e.g. after the tool ran), in order."""
        if self.held and not any(self.unverified(s) for s in self.held):
            out, self.held = self.held, []
            return out
        return []

    def unresolved(self):
        return [s for s in self.held if self.unverified(s)]

    def drop_held(self):
        held, self.held = self.held, []
        return held
