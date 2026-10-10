"""Response policy: what KIND of turn this is, and so what kind of reply fits, decided in code before the model writes.

    direct command        "make the strip blue", "can you set a timer for 5"   -> do it now, confirm briefly, don't ask
    question              "what's the weather?", "is the door open"           -> answer first; ask back only if needed
    casual conversation   "lol that was funny", "I just got back"              -> react like a person; no offers of help
    emotional expression  "ugh this day sucks", "I'm so tired"                 -> acknowledge briefly; don't push fixes
    correction            "that's not what I said"                             -> (conversation/corrections.py)
    clarification         an answer to the question Jarvis just asked          -> use it and carry on; never re-ask
    task continuation     "do it again", "the other one", "turn it off"        -> continue what was just done
    conversation ending   "ok bye", "that's all", "good night"                 -> short goodbye, no question

Several signals are combined, not one keyword list: sentence structure (question form, imperative form), whether a
real tool matches (its fast-path pattern, or a verb taken from the tools' own examples), what Jarvis just asked, an
open request or goal, the last action (for "it" / "again"), and how the social layer reads their tone. The verdict is
one line in the runtime context plus a check on the reply: after a clear command, "Want me to...?" is held back
(llm/guard.py via truth.ClaimGuard) and the model is told to just do it.
"""

import re
import time
from dataclasses import dataclass, field

from room_agent import runtime as rt

COMMAND, QUESTION, CASUAL, EMOTIONAL, CORRECTION, CLARIFICATION, CONTINUATION, ENDING = (
    "direct command", "question", "casual conversation", "emotional expression", "correction", "clarification",
    "task continuation", "conversation ending")

_WH = re.compile(r"^\W*(?:(?:hey|so|and|but|ok(?:ay)?|jarvis)[\s,]+)*(what|what's|whats|who|who's|where|where's|when|why|how|"
                 r"which|whose|is|are|am|was|were|do|does|did|have|has|had|will|would|should|could|can|may|might|shall)\b", re.I)
_REQUEST = re.compile(r"^\W*(?:(?:hey|ok(?:ay)?|jarvis|please|so)[\s,]+)*(?:can|could|would|will)\s+you\s+(?:please\s+)?(\w+)", re.I)
_LEAD = re.compile(r"^\W*(?:(?:hey|ok(?:ay)?|jarvis|please|so|just|now|no|nah|wait|actually|um+|uh+|oh|well|alright)[\s,.!]+)*(\w+)", re.I)
_QUESTION_WORDS = {"what", "whats", "who", "where", "when", "why", "how", "which", "whose", "is", "are", "am", "was", "were",
                   "do", "does", "did", "have", "has", "had", "will", "would", "should", "could", "can", "may", "might"}
_REFERENCE = re.compile(r"\b(it|that|this|them|the other (one|light)|the same|again|do it again|one more time|undo|"
                        r"back to|put it back|more|less|a bit|a little|louder|quieter|brighter|dimmer)\b", re.I)
_ENDING = re.compile(r"^\W*(?:(?:ok(?:ay)?|alright|cool|thanks|thank you|great)[\s,!.]+)*(bye|goodbye|good ?night|night night|"
                     r"see (you|ya)|talk (to you )?later|later|that'?s (all|it)( for now)?|i'?m (done|good|out)|gotta go|peace|"
                     r"catch you later|nothing else)(?:[\s,]+(?:jarvis|man|dude|buddy))?\W*$", re.I)
_EMOTION_DIMS = ("frustration", "annoyed", "low", "excited", "positive", "joking")
# verbs every assistant command starts with, plus the first word of each tool's own examples (filled in on first use)
_BASE_VERBS = {"turn", "switch", "set", "make", "change", "dim", "open", "close", "play", "pause", "stop", "skip", "start",
               "remind", "cancel", "mute", "unmute", "move", "put", "show", "send", "check", "tell", "read", "call",
               "remember", "forget", "find", "search", "look", "add", "remove", "delete", "lower", "raise", "give"}
_verbs = set()


def _command_verbs():
    if not _verbs:
        from room_agent.actions import core

        _verbs.update(_BASE_VERBS)
        for cap in core.capabilities():
            for ex in cap.examples or []:
                first = re.findall(r"[a-z]+", ex.lower())[:1]
                _verbs.update(first)
            first = re.findall(r"[a-z]+", cap.name.lower())[:1]  # (set_light -> set, open_app -> open)
            _verbs.update(first)
        _verbs.difference_update(_QUESTION_WORDS)  # ("how warm is it" is a question, whatever a tool's example says)
    return _verbs


@dataclass
class TurnPolicy:
    kind: str = CASUAL
    why: list = field(default_factory=list)
    reference: str = ""  # what "it" / "again" most likely points at (the last thing acted on)

    def render(self):
        guide = {
            COMMAND: "a clear command: do it now with the right tool and confirm in a few words once it's done. Don't ask "
                     "'want me to...?', don't offer options or extras; ask only if a REQUIRED detail is missing.",
            QUESTION: "a question: answer it directly first, as short as the answer allows; a question back only if you "
                      "truly can't answer without it.",
            CASUAL: "casual talk, not a request: react like a person would (a short line, maybe a light remark). No offers "
                    "of help, no 'want me to...', no question unless it would come naturally.",
            EMOTIONAL: "they're expressing how they feel, not asking for anything: acknowledge it briefly and naturally; "
                       "don't rush to fix it or offer tools unless they ask. Never claim feelings of your own.",
            CLARIFICATION: "they're answering what you just asked: take the answer and carry on with the task; never ask the "
                           "same thing again.",
            CONTINUATION: "continuing what you were just doing" + (f" ('it' / 'that' / 'again' most likely means: "
                                                                     f"{self.reference})" if self.reference else "")
                          + ": just do it; don't re-confirm details you already have.",
            ENDING: "they're wrapping up: a short, natural goodbye; no question, no offer.",
            CORRECTION: "a correction (see the correction line): acknowledge in a few words and move on.",
        }[self.kind]
        return f"- turn_type: {self.kind}. {guide}"


def _last_reference():
    """The thing last acted on, for 'it' / 'that' / 'again': 'set_light on LED strip (color blue)'."""
    from room_agent.actions.context import env

    last = env.last_successful_action
    if last is None or time.time() - last.at > 900:
        return ""
    args = ", ".join(f"{k} {v}" for k, v in (last.parameters or {}).items() if k != "confidence" and v not in (None, ""))[:80]
    return f"{last.capability}" + (f" on {last.subject}" if last.subject else "") + (f" ({args})" if args else "")


def classify(text, info=None):
    """-> TurnPolicy for this utterance. `info`: cognition.begin_turn's result (reflex match, level), if known."""
    from room_agent.actions import pending
    from room_agent.conversation import corrections

    t = (text or "").strip()
    p = TurnPolicy()
    words = re.findall(r"[a-z']+", t.lower())
    if not words:
        return p
    if corrections.is_denial(t, corrections.misheard_text()) or getattr(rt.turn, "correction", ""):
        p.kind, p.why = CORRECTION, ["denies what Jarvis heard"]
        return p
    if _ENDING.match(t):
        p.kind, p.why = ENDING, ["a sign-off"]
        return p
    asked = (rt.last_reply or "").rstrip().endswith("?")
    if (asked or rt.pending is not None) and not _WH.match(t) and len(words) <= 8:
        p.kind, p.why = CLARIFICATION, ["answers the question Jarvis just asked" if asked else "fills in an open request"]
        return p
    reflexed = bool(info and info.get("reflex"))
    request = _REQUEST.match(t)
    lead = _LEAD.match(t)
    verbs = _command_verbs()
    imperative = bool(lead and lead.group(1).lower() in verbs and not t.endswith("?") or (request and request.group(1).lower() in verbs))
    ref = _last_reference()
    if _REFERENCE.search(t) and ref and (imperative or len(words) <= 4):
        p.kind, p.reference, p.why = CONTINUATION, ref, ["refers to what was just done"]
        return p
    if reflexed or imperative:
        p.kind, p.why = COMMAND, ["a tool's command pattern matched" if reflexed else "imperative / 'can you ...' request form"]
        return p
    if t.endswith("?") or _WH.match(t):
        p.kind, p.why = QUESTION, ["question form"]
        return p
    try:
        from room_agent import social

        snap = social.state.snapshot() if social.state.turns else None
        vals = (snap or {}).get("values", {})
        if snap and any(vals.get(d, 0) >= 0.35 for d in _EMOTION_DIMS) and snap.get("mood_signal") not in ("neutral", "unknown"):
            p.kind, p.why = EMOTIONAL, [f"tone: {snap['mood_signal']}"]
            return p
    except Exception:
        pass
    p.kind, p.why = CASUAL, ["a statement, no request in it"]
    return p


def on_user_turn(text, info=None):
    rt.turn.policy = classify(text, info)
    style_request(text)
    return rt.turn.policy


# How they asked Jarvis to talk: "don't talk a lot", "just say alright", "you don't have to repeat everything I say".
# A rule, not a mood: kept for the session and (unless it was "for now") as their response_style preference.
STYLE_ASK = re.compile(
    r"\b(?:don'?t|do not|stop)\s+(?:talk|speak)(?:ing)?\s+(?:a lot|so much|too much)\b|\btalk less\b|\bless talking\b"
    r"|(?<!\bi )(?<!\bwe )\b(?:just|only)\s+say\s+(?P<ack>ok(?:ay)?|alright|all right|done|got it)\b"
    r"|\b(?:you\s+)?don'?t\s+(?:have|need)\s+to\s+(?:say|repeat|tell me|explain|confirm)\b|\bstop repeating\b"
    r"|\bdon'?t repeat (?:everything|what i say|me|it back|after me)\b|\bkeep (?:it|your answers|replies) short\b"
    r"|\bbe brief\b", re.I)
ACKS = {"ok": "Okay", "okay": "Okay", "alright": "Alright", "all right": "Alright", "done": "Done", "got it": "Got it"}
STYLE_NORMAL = re.compile(r"\b(?:talk|speak) (?:normally|normal) again\b|\byou can (?:talk|explain) more\b|"
                          r"\bmore detail(?:s|ed)?\b(?: please)?$|\bback to (?:normal|how you talked)\b", re.I)


def style_request(text):
    """Their words about how Jarvis should talk -> rt.reply_style / rt.ack_word now, and a lasting preference."""
    t = str(text or "")
    m = STYLE_ASK.search(t)
    if m:
        rt.reply_style = "minimal"
        rt.ack_word = ACKS.get((m.group("ack") or "alright").lower(), "Alright")
        _remember_style("minimal", t)
    elif STYLE_NORMAL.search(t):
        rt.reply_style, rt.ack_word = None, ""
        _remember_style("normal", t)
    if getattr(rt.turn, "intent", None) is not None and (m or STYLE_NORMAL.search(t)):
        rt.turn.intent.style_pref = "minimal" if m else "normal"


def _remember_style(value, said):
    from room_agent.learning.adaptation import PERMANENT, TEMPORARY

    if TEMPORARY.search(said) and not PERMANENT.search(said):
        return  # ("for now, just say ok": this session only)
    try:
        from room_agent import learning

        learning.user_model().teach("response_style", value, because=f'you said: "{said[:80]}"')
    except Exception as e:  # noqa: BLE001 (the session rule still applies)
        import logging

        logging.getLogger("room-agent").debug("response style not saved: %s", e)


def minimal_replies():
    """Did they ask for minimal replies (this session, or as a lasting preference)? -> the acknowledgement word, or ''."""
    if rt.reply_style == "minimal":
        return rt.ack_word or "Alright"
    if rt.reply_style is None:
        try:
            from room_agent import learning

            p = learning.user_model().resolve("response_style")
            if p and p.get("value") == "minimal":
                return rt.ack_word or "Alright"
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _openers():
    """How recent replies started, when the same opener keeps coming back ("Got it", "Alright")."""
    starts = [" ".join(re.findall(r"[a-z']+", m["text"].lower())[:2]) for m in rt.recent[-12:] if m["role"] == "assistant"]
    seen = {}
    for s in starts:
        if s:
            seen[s] = seen.get(s, 0) + 1
    return [s for s, n in seen.items() if n >= 2]


def context_lines(user_text):
    p = getattr(rt.turn, "policy", None)
    if p is None:
        return []
    lines = [p.render()]
    ack = minimal_replies()
    if ack:
        lines.append(f"- reply_style: they asked you to keep it minimal: after an action that WORKED (a verified OK) say only "
                     f"\"{ack}.\"; never repeat their command back; no offers or follow-up questions. A failure, something "
                     "not done, or a problem they report (\"I don't see it\", \"it didn't work\") always gets a real answer: "
                     "what happened and what you're doing about it. Questions still get their answer, briefly.")
    worn = _openers()
    if worn:
        lines.append("- vary your wording: recent replies kept starting with " + ", ".join(repr(w) for w in worn[:3])
                     + "; don't open with those now.")
    return lines


def register():
    from room_agent.actions import core

    core.register_context(context_lines, order=24)
