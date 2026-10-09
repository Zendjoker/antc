"""Errand calls: Jarvis phones a business FOR you (first errand: a restaurant reservation) in a restricted mode.

Not the normal phone mode. The person on the line is NOT you, so this session has:
    - no tools, no memory, no email / calendar / files: the model only sees the brief you gave and the call so far
    - a fixed opening that says it's an AI assistant calling on your behalf
    - code-enforced limits: a booking is accepted only if the date, party size and time fit your brief (else "I'll
      check with them and call back"); nothing is said that isn't in the brief (a phone number, an email address, a card
      number, any long number that you didn't put there is refused before it's spoken); no payment, ever
    - hard limits: MAX_TURNS exchanges, MAX_SECONDS, today's model budget
    - afterwards: the outcome is saved (errands.json) and texted to you; your calendar is NOT changed (you decide)

The model only PROPOSES what to say next and how the call stands (JSON). Code checks it before anything is spoken.

TEST MODE (the only mode for now): the call goes to YOUR OWN phone (MY_PHONE) and you play the restaurant. Calling any
other number isn't built: that needs its own explicit setup later (an allowlist of numbers, per-call approval).

    .venv\\Scripts\\python -m room_agent.phone.errand --business "Luigi's" --party 4 --date "Friday" --from 19:00 \\
        --to 20:00 --name "Adam"          a real test call to your phone (its own server + tunnel; doesn't touch Jarvis)
"""

import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field

from room_agent import config

log = logging.getLogger("room-agent")
MAX_TURNS = 16
MAX_SECONDS = 360
OPEN_WAIT_S = 3.5   # if nobody speaks this long after the call connects, it starts ("Hi, is this Luigi's?")
CLOSE_WAIT_S = 6.0  # after its closing line is SPOKEN it waits this long for their "bye" (never cuts them off)
WORDS_PER_S = 2.6   # how fast the phone voice speaks (to know when a line has finished playing)


def speak_time(line):
    """Roughly how long the phone voice takes to say `line` (seconds)."""
    return 0.6 + len(str(line).split()) / WORDS_PER_S
THINK = None  # tests: (errand, transcript) -> proposal dict; None = the cheap model (OPENAI_MODEL)
ACTIVE = {}   # errand id -> Errand (in this process)
_lock = threading.Lock()


@dataclass
class Errand:
    business: str
    party_size: int
    date: str
    time_from: str            # "19:00"
    time_to: str = ""         # "20:00" (blank = only time_from)
    name: str = ""            # the name the booking is under
    relation: str = ""        # who they are to Jarvis: "boss" -> "I'm calling for my boss, Adam Azzouz"
    notes: str = ""           # e.g. "a high chair", "outdoor if possible"
    kind: str = "restaurant_reservation"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "new"       # new | calling | booked | needs_you | declined | no_deal | failed
    outcome: dict = field(default_factory=dict)
    transcript: list = field(default_factory=list)
    started: float = 0.0
    ended: float = 0.0
    turns: int = 0
    cost_usd: float = 0.0
    agreed_time: str = ""     # a time they offered that fits the brief, accepted by code
    alt_asked: int = 0        # times it asked for something closer after an offer outside the brief
    last_offer: str = ""      # their last offer outside the brief (for "I'll check with Adam if 9 works")

    def window(self):
        return f"{self.time_from}" + (f" to {self.time_to}" if self.time_to and self.time_to != self.time_from else "")

    def spoken_window(self):
        """'7 and 8 pm' / '7:30 pm' (how a person says it on the phone, after "between")."""
        a, b = _spoken(self.time_from), _spoken(self.time_to) if self.time_to and self.time_to != self.time_from else ""
        if b and a.split()[-1] == b.split()[-1]:
            a = a.rsplit(" ", 1)[0]
        return f"{a} and {b}" if b else a

    def when(self):
        return f"on {self.date}" if re.search(r"\d", str(self.date)) else f"this {self.date}"

    def first(self):
        return (self.name or "my boss").split()[0] if self.name else "my boss"

    def opening(self):
        """Warm and natural, with a light AI disclosure (asked again, it says yes plainly)."""
        rel = self.relation.strip().removeprefix("my ").strip()
        who = f"my {rel}, {self.name}" if rel and self.name else (self.name or "my boss")
        return (f"Hi there! I'm calling for {who}. I'm an AI assistant, just so you know. I was hoping to get a table "
                f"for {self.party_size} {self.when()}, sometime between {self.spoken_window()}. Do you have anything?")


def _short(hhmm):
    """'8', '7:30' - how a time is said once it's clear it's the evening."""
    return _spoken(hhmm).replace(" pm", "").replace(" am", "")


def _spoken(hhmm):
    m = _minutes(hhmm)
    if m is None:
        return str(hhmm or "")
    h, mi = divmod(m, 60)
    return f"{(h % 12) or 12}{f':{mi:02d}' if mi else ''} {'pm' if h >= 12 else 'am'}"


def _minutes(hhmm):
    m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$", str(hhmm or "").lower())
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return h * 60 + mi if 0 <= h < 24 and 0 <= mi < 60 else None


def fits(e, booking):
    """Code's check of a proposed booking against the brief. -> (ok, why not)"""
    b = booking or {}
    try:
        party = int(b.get("party_size") or 0)
    except (TypeError, ValueError):
        party = 0
    if party != int(e.party_size):
        return False, f"it's for {party or 'an unclear number of'} people, not {e.party_size}"
    t = _minutes(b.get("time"))
    lo, hi = _minutes(e.time_from), _minutes(e.time_to or e.time_from)
    if t is None or lo is None or not lo <= t <= (hi if hi is not None else lo):
        return False, f"{b.get('time') or 'that time'} is outside {e.window()}"
    if b.get("date") and _norm(b["date"]) not in (_norm(e.date), "") and _norm(e.date) not in _norm(b["date"]):
        return False, f"it's for {b['date']}, not {e.date}"
    return True, ""


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def safe_to_say(e, text):
    """Nothing beyond the brief: no phone number, email, card / long number, or link that you didn't give."""
    brief = " ".join(str(v) for v in (e.business, e.party_size, e.date, e.time_from, e.time_to, e.name, e.notes))
    if re.search(r"[\w.+-]+@[\w-]+\.\w+|https?://|www\.", text, re.I):
        return False
    for num in re.findall(r"\d[\d \-().]{5,}\d", text):
        digits = re.sub(r"\D", "", num)
        if len(digits) >= 6 and digits not in re.sub(r"\D", "", brief):
            return False
    return not re.search(r"\b(credit card|card number|cvv|deposit|pay(ment)?|social security|password|address is)\b",
                         text, re.I)


CONFIRMED = re.compile(r"\b(booked|confirmed|reserved|all set|you'?re set|see you|got you down|have you down|"
                       r"put you down|done|that'?s fine|perfect|great|no problem|sure thing)\b", re.I)
# (a clear "it's booked": what lets CODE close the call on a time already agreed, without asking the model)
BOOKED = re.compile(r"\b(booked|confirmed|reserved|all set|you'?re set|see you (then|friday|on|at|tonight|tomorrow)|"
                    r"got you down|have you down|put you down|done|that'?s it)\b", re.I)
REFERENCE = re.compile(r"\b(?:ref(?:erence)?|confirmation)(?:\s+(?:number|code|no\.?))?(?:\s+is)?\s*[:#]?\s*"
                       r"([A-Z]*\d[A-Z0-9-]{0,11})\b", re.I)
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def other_day(e, text):
    """Did they name a day that isn't the one asked for ("tomorrow", "Saturday")? -> that day's words, or "" """
    low, want = str(text or "").lower(), str(e.date or "").lower()
    for d in DAYS + ("tomorrow", "tonight", "today", "next week", "weekend"):
        if re.search(rf"\b{d}\b", low) and d not in want:
            return d
    return ""


ASK_BOSS = re.compile(r"\b((call|ask|check with|talk to|text) (your|the) (boss|client|employer)|let (him|her|them) know|"
                      r"get back to (me|us)|call (me|us) back|check and call)\b", re.I)
NOTHING_ELSE = re.compile(r"\b(no|nope|sorry|that'?s (all|it)|all we have|only|nothing (else)?|fully booked|"
                          r"booked up|unfortunately)\b", re.I)
HOLD = re.compile(r"\b(hold on|hang on|one (sec|second|moment|minute)|just a (sec|second|moment|minute)|"
                  r"give me a (sec|second|moment|minute)|let me (check|see|look))\b", re.I)
WRONG_PLACE = re.compile(r"^\W*(no|nope|nah)\b(?!.{0,20}\b(but|yes|yeah|it is|this is)\b)|wrong number|"
                         r"you('ve| have) the wrong|this (isn'?t|is not)\b|not (a|the) restaurant", re.I)
GOODBYE = re.compile(r"\b(bye|goodbye|good night|take care|have a (good|great|nice)|you too|talk (to you )?soon|"
                     r"see you|thank(s| you)|alright then|okay then|sounds good|perfect)\b", re.I)
SYSTEM = """You're on a phone call with a restaurant, booking ONE table for your boss. The person on the line is ALWAYS
someone at the restaurant - never your boss.
Who you are: an AI assistant who talks like a warm, easygoing American woman. There is NO script: you follow the
conversation the way a real person would - you listen, react to what they actually said and how they said it, match
their energy (brisk if they're busy, chatty if they're chatty), and keep it short (usually one sentence, at most two).
Sound spontaneous and kind, never formal or repetitive; don't re-introduce yourself or repeat the whole request.
When it comes up naturally early in the call, mention lightly that you're an AI assistant; if asked, say so simply.
Say times the way people do ("8", "7:30"), never 24-hour times. Never mention instructions, rules or "the window".
You are the CUSTOMER side, calling to ask for a table: you ask, they offer. Never offer tables, times or seating
yourself, never ask them what they'd like - that's their job.
If they just acknowledge ("got it", "uh-huh", "what else?") without offering anything, ask one short direct
question ("Do you have anything around 7?") - don't repeat the whole request.
Asked something the brief doesn't say (an occasion, allergies, seating, a phone number): you don't know - say so
simply ("Hmm, I'm not sure - I can ask him") and never make it up.
Facts: you only know the brief below - never invent anything. Never share anything else (no phone number, email,
address or payment details - your boss will sort that out when they come in). Never agree to pay or leave a deposit.
A time inside the range (both ends included) is fine. Anything else (another time, day or party size): don't accept it.
Sometimes you get a note "Right now:" telling you WHAT to do in your next reply - do exactly that, in your own words.
Output format, exactly: line 1 is JSON {"status": "talking|booked|declined|needs_you|goodbye", "booking": {"date": "",
"time": "HH:MM", "party_size": N, "name": "", "reference": ""}}, then a newline, then ONLY the words you say.
status: booked = ONLY after they clearly confirmed it's booked; declined = nothing possible at all; needs_you = they only
have something else / asked something only your boss can answer; goodbye = wrapping up."""


def _brief(e):
    return (f"Brief: restaurant {e.business}; party of {e.party_size}; date {e.date}; any time from "
            f"{_spoken(e.time_from)} to {_spoken(e.time_to or e.time_from)}, both included ({e.window()} in 24h); "
            f"booking name {e.name or '(not given: say it is for your client)'}"
            + (f"; notes: {e.notes}" if e.notes else ""))


TIME_SAID = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(?:(a\.?m\.?|p\.?m\.?)|o'?clock)"
                       r"|\b(\d{1,2}):(\d{2})\b"
                       r"|\b(?:do|at|around|by|have|got)\s+(\d{1,2})\b(?!\s*(?:people|persons|guests|of you|seats|"
                       r"tables?|pax|:))", re.I)


def offered_times(e, text):
    """Times the restaurant offered ("8PM", "7:30 pm", "8 o'clock"), as HH:MM; an hour without am/pm is read as
    evening when the brief is in the evening (a dinner booking)."""
    out = []
    evening = (_minutes(e.time_from) or 0) >= 12 * 60
    for m in TIME_SAID.finditer(text or ""):
        if re.search(r"\b(can'?t|cannot|not|no|don'?t|isn'?t|unavailable|booked up|full)\b[^.,;]{0,15}$",
                     (text or "")[max(0, m.start() - 25):m.start()], re.I):
            continue  # ("we can't do 7 PM" isn't an offer of 7 PM)
        if m.group(1):
            h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower().replace(".", "")
        elif m.group(4):
            h, mi, ap = int(m.group(4)), int(m.group(5)), ""
        else:
            h, mi, ap = int(m.group(6)), 0, ""
            if not 1 <= h <= 12:
                continue
        if ap == "pm" and h < 12 or (not ap and evening and h < 12):
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if 0 <= h < 24 and 0 <= mi < 60:
            out.append(f"{h:02d}:{mi:02d}")
    return out


def _messages(e, transcript, directive=""):
    msgs = [{"role": "system", "content": SYSTEM + "\n\n" + _brief(e)}]
    for who, text in transcript:
        msgs.append({"role": "assistant" if who == "jarvis" else "user", "content": text})
    if len(msgs) == 1:  # (nothing said yet)
        msgs.append({"role": "user", "content": "(the call has just connected)"})
    if directive:
        msgs.append({"role": "system", "content": "Right now: " + directive})
    return msgs


def _stream_model(e, transcript, directive=""):
    """The cheap model, streamed: yields ("header", dict) first (status + booking, checked by code before anything is
    said), then ("text", piece) as the words arrive. Budget-checked; usage recorded (estimated if the stream is cut)."""
    from room_agent.llm.budget import budget, price
    from room_agent.llm.openai_backend import openai_client

    if budget.exceeded():
        yield "header", {"status": "needs_you"}
        yield "text", "Sorry, I have to run - I'll call you back. Thanks!"
        return
    import openai

    msgs = _messages(e, transcript, directive)
    c = openai_client().with_options(max_retries=0, timeout=20)
    kw = dict(model=config.OPENAI_MODEL, messages=msgs, max_completion_tokens=1500, stream=True,
              stream_options={"include_usage": True})  # (includes GPT-5's thinking tokens)
    try:  # ("minimal" thinking: the first words have to come back fast on a phone call)
        stream = c.chat.completions.create(reasoning_effort=config.OPENAI_REASONING, **kw) if config.OPENAI_REASONING \
            else c.chat.completions.create(**kw)
    except openai.BadRequestError as ex:  # (400, not billed: this model doesn't take reasoning_effort)
        if "reasoning" not in str(ex).lower():
            raise
        stream = c.chat.completions.create(**kw)
    buf, header_done, out_chars, usage = "", False, 0, None
    try:
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            piece = chunk.choices[0].delta.content or ""
            out_chars += len(piece)
            if header_done:
                if piece:
                    yield "text", piece
                continue
            buf += piece
            header, rest, complete = split_header(buf)
            if complete:
                header_done = True
                yield "header", header
                if rest:
                    yield "text", rest
        if not header_done:  # (the reply ended before a complete decision: whatever it is, words only)
            header, rest, _ = split_header(buf, final=True)
            yield "header", header
            if rest:
                yield "text", rest
    finally:
        tin = usage.prompt_tokens if usage else sum(len(m["content"]) for m in msgs) // 4
        tout = usage.completion_tokens if usage else out_chars // 4
        budget.record("openai", config.OPENAI_MODEL, fresh_in=tin, out=tout)
        p_in, _, _, p_out = price(config.OPENAI_MODEL)
        e.cost_usd = round(e.cost_usd + (tin * p_in + tout * p_out) / 1e6, 6)


def split_header(buf, final=False):
    """The model's decision (a JSON object) at the start of its reply, however it's laid out (own line, same line as
    the words, or missing). -> (header dict, the words after it, complete?)"""
    text = buf.lstrip()
    if not text:
        return {"status": "talking"}, "", final
    if not text.startswith("{"):
        return {"status": "talking"}, text, True  # (no decision: all words)
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text):
        if in_str:
            esc = (ch == "\\" and not esc)
            if ch == '"' and not esc:
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    header = json.loads(text[:i + 1])
                except ValueError:
                    header = {}
                return (header if isinstance(header, dict) else {}) or {"status": "talking"}, text[i + 1:].lstrip(), True
    if final:  # (an unfinished decision: never spoken)
        return {"status": "talking"}, "", True
    return {"status": "talking"}, "", False


def _from_dict(p):
    """A whole proposal (tests: THINK) as the same stream: header, then the words."""
    p = p or {}
    yield "header", {k: v for k, v in p.items() if k != "say"}
    if p.get("say"):
        yield "text", str(p["say"])


def _natural(sentence):
    """Words a person wouldn't say on this call, smoothed out ("in that window" -> "then"); anything that looks like
    data (a {...} fragment, "status":) is removed - it is never read out."""
    sentence = str(sentence)
    while re.search(r"\{[^{}]*\}", sentence):
        sentence = re.sub(r"\{[^{}]*\}", " ", sentence)
    sentence = re.sub(r'[{}]|"?\b(status|booking|party_size|reference)"?\s*:\s*"?[\w:-]*"?,?', " ", sentence)
    s = re.sub(r"\s*\b(?:with)?in (?:that|the|your|this) (?:time )?window\b", " then", sentence, flags=re.I)
    s = re.sub(r"\b(?:the|that|this) (?:time )?window\b", "that time", s, flags=re.I)
    return re.sub(r"\s{2,}", " ", s).replace(" ?", "?").strip()


SENTENCE = re.compile(r"(.+?[.!?])(\s+|$)", re.S)


class ErrandSession:
    """One errand call. send(text, last) speaks; hang_up() ends the call.

    There's no script: it waits for the restaurant to answer (or, after OPEN_WAIT_S of silence, starts itself), and the
    model words every reply from the conversation so far. CODE still makes every decision that matters - accept a time
    (only inside the brief), ask for something closer, never another day, close the call with an outcome - and tells
    the model WHAT to do ("Right now: ..."); the model decides HOW to say it. Every sentence is checked before it's
    spoken. It never hangs up on them: only a goodbye ends the call (anything else reopens it)."""

    def __init__(self, errand, send, hang_up=None):
        self.e, self.send, self.hang_up = errand, send, hang_up
        self.done = False
        self.closing = False   # (its closing words were said: a goodbye from them ends the call; anything else reopens)
        self.opened = False    # (it has said something)
        self.introduced = False  # (it has said who it's calling for and what it wants)
        self.asked_identity = False  # (it asked "is this Luigi's?" first)
        self._hung = False
        self._close_no = 0
        self._rng = random.Random(errand.id)
        self._lock = threading.Lock()
        self.e.status, self.e.started = "calling", time.time()
        self._timer(OPEN_WAIT_S, self._kick)

    @staticmethod
    def _timer(seconds, fn):
        """A background timer that never keeps the process alive on its own (daemon)."""
        tm = threading.Timer(seconds, fn)
        tm.daemon = True
        tm.start()
        return tm

    # ------------------------------------------------------------ the conversation
    def _kick(self):
        """Nobody has spoken since the call connected: start the call, like a person would."""
        with self._lock:
            if self.opened or self._hung:
                return
            self.opened = self.asked_identity = True
            self._speak(f"The call just connected and nobody has said anything yet. Start it: a friendly hello and ask "
                        f"if this is {self.e.business}.", f"Hi there! Is this {self.e.business}?")

    def _intro(self):
        e = self.e
        rel = e.relation.strip().removeprefix("my ").strip()
        who = f"your {rel}, {e.name}" if rel and e.name else (e.name or "your boss")
        return (f"This is the start of the conversation: react naturally to how they answered (greet them back; if they "
                f"asked how you are, answer briefly), then say you're calling for {who}, hoping to get a table for "
                f"{e.party_size} {e.when()} sometime between {e.spoken_window()}, and mention casually that you're an AI "
                "assistant. Two or three short sentences, relaxed - not a speech.")

    def _intro_fallback(self):
        e = self.e
        return e.opening()

    def answer(self, text):
        """What the restaurant said -> the next reply, or the end of the call."""
        with self._lock:
            if self._hung:
                return
            e = self.e
            e.turns += 1
            e.transcript.append(("them", str(text)[:500]))
            if self.closing:
                if GOODBYE.search(str(text)) and not offered_times(e, text) and "?" not in str(text):
                    return self._goodbye()
                self.closing = self.done = False  # (they kept talking: the conversation isn't over)
                self._close_no += 1
            if e.turns > MAX_TURNS or time.time() - e.started > MAX_SECONDS:
                return self._close("You need to wrap up now: say sorry, you have to run, you'll call back. Thank them.",
                                   "Sorry, I have to run - I'll call you back. Thanks so much!", "no_deal",
                                   {"why": "the call went on too long without a booking"})
            if self.asked_identity and not self.introduced and WRONG_PLACE.search(str(text)):
                self.introduced = True
                return self._close("", "Oh, I'm so sorry - wrong number! Have a good night.", "failed",
                                   {"why": "wrong number: they said it isn't " + e.business}, use_model=False)
            self.opened = True
            directive, fallback, close = self._decide(text)
            if not self.introduced:
                self.introduced = True
                if close is None:  # (the first thing they say: say hello back and what it's about)
                    intro = self._intro() + (f" Also: {directive}" if directive else "")
                    return self._speak(intro, self._intro_fallback(), sentences=3, words=55)
            if close is not None:
                return self._close(directive, fallback, *close)
            if directive:
                return self._speak(directive, fallback)
            return self._free_turn(text)

    def _decide(self, text):
        """CODE decides what happens next. -> (directive for the model, fallback words, (status, outcome) to close
        the call with or None)."""
        e, t = self.e, str(text)
        first = e.first()
        if ASK_BOSS.search(t) and not e.agreed_time:
            return (f"They asked you to check with your boss. Agree warmly - you'll check with {first} and call them "
                    "right back - and thank them.",
                    f"Of course! I'll check with {first} and call you right back. Thank you so much!",
                    ("needs_you", {"offered": {"time": e.last_offer} if e.last_offer else {},
                                   "why": f"they asked to check with {first}: {t[:120]}"}))
        offered = offered_times(e, t)
        if HOLD.search(t) and not offered:
            return ("They asked you to hold on a moment. Say something short and patient.", "Sure, take your time!",
                    None)
        wrong_day = other_day(e, t)
        if wrong_day and not e.agreed_time:
            if not e.alt_asked:
                e.alt_asked += 1
                e.last_offer = offered[-1] if offered else ""
                return (f"They mentioned {wrong_day}, but you need {e.date}. Kindly ask if they have anything on "
                        f"{e.date}, sometime between {e.spoken_window()}.",
                        f"Ah, we're actually hoping for {e.date} - do you have anything then?", None)
            return (f"They don't have anything on {e.date}. Say that's totally okay, you'll check with {first} and call "
                    "them back, and thank them warmly.",
                    f"Aw, okay, no problem at all! Let me check with {first} and I'll call you right back. Thank you!",
                    ("needs_you", {"why": f"they offered {wrong_day} instead of {e.date}"}))
        fit = [x for x in offered if fits(e, {"time": x, "party_size": e.party_size})[0]]
        if not offered and e.alt_asked and e.last_offer and not e.agreed_time and NOTHING_ELSE.search(t):
            off = _short(e.last_offer)
            return (f"They only have {off}. Say that's okay, you'll check with {first} whether {off} works and call "
                    "right back, and thank them warmly.",
                    f"Aw, okay, no problem! Let me check with {first} if {off} works and I'll call you right back. Thanks!",
                    ("needs_you", {"offered": {"time": e.last_offer}, "why": f"they only have {off}"}))
        if offered and not fit and not e.agreed_time:
            off = offered[-1]
            e.last_offer = off
            late = (_minutes(off) or 0) > (_minutes(e.time_to or e.time_from) or 0)
            if not e.alt_asked:
                e.alt_asked += 1
                edge = _short(e.time_to or e.time_from) if late else _short(e.time_from)
                return (f"They offered {_short(off)}, which is a bit {'late' if late else 'early'} for you. Don't accept "
                        f"it. Kindly ask if they have anything closer to {e.spoken_window().replace(' and ', ' or ')} - "
                        f"even {edge} would be great.",
                        f"Ah, {_short(off)} is a little {'late' if late else 'early'} for us - anything closer to "
                        f"{e.spoken_window().replace(' and ', ' or ')}?", None)
            return (f"They only have {_short(off)}. Don't accept it. Say that's okay, you'll check with {first} whether "
                    f"{_short(off)} works and call right back, and thank them warmly.",
                    f"Aw, okay, no problem! Let me check with {first} if {_short(off)} works and I'll call you right "
                    "back. Thanks!",
                    ("needs_you", {"offered": {"time": off}, "why": f"they only have {_short(off)}"}))
        newly = bool(fit) and fit[-1] != e.agreed_time
        if fit:
            e.agreed_time = fit[-1]
        agreed = {"date": e.date, "time": e.agreed_time, "party_size": e.party_size, "name": e.name}
        ref = REFERENCE.search(t)
        if ref:
            agreed["reference"] = ref.group(1)
        if e.agreed_time and BOOKED.search(t.rsplit("?", 1)[-1]):
            recap = f"{_short(e.agreed_time)} for {e.party_size}" + (f" under {e.name}" if e.name else "")
            return (f"They just confirmed the booking. Thank them warmly and briefly repeat it back: {recap}.",
                    f"Perfect, {recap}. Thank you so much!", ("booked", {"booking": agreed}))
        if newly:
            asked_name = e.name and re.search(r"\bname\b", t, re.I)
            return (f"They offered {_short(e.agreed_time)}, which works perfectly. Accept it happily"
                    + (f" and tell them the name is {e.name}" if asked_name else "") + ".",
                    f"Oh, {_short(e.agreed_time)} is perfect!" + (f" It's under {e.name}." if asked_name else ""), None)
        return "", "", None

    def _free_turn(self, text):
        """No decision by code: the model decides (its decision is checked before a word is said)."""
        e = self.e
        try:
            gen = self._model("")
            kind, header = next(gen)
        except Exception as ex:  # noqa: BLE001
            log.warning("errand %s: the model failed (%s)", e.id, ex.__class__.__name__)
            return self._close("", "Sorry, I'm having trouble on my end - I'll call you back. Thanks!", "failed",
                               {"why": f"model error ({ex.__class__.__name__})"}, use_model=False)
        status = str(header.get("status") or "talking")
        booking = header.get("booking") if isinstance(header.get("booking"), dict) else {}
        accepting = status == "booked" or (status == "talking" and (booking.get("time") or booking.get("party_size")))
        if accepting:
            partial = {**{"party_size": e.party_size, "time": e.time_from}, **{k: v for k, v in booking.items() if v}}
            ok, why = fits(e, {**{"party_size": e.party_size}, **booking} if status == "booked" else partial)
            if not ok:  # (it would agree to something outside the brief: code says no, before a word)
                gen.close()
                return self._close(f"Don't accept that. Say it's okay, you'll check with {e.first()} and call right "
                                   "back, and thank them warmly.",
                                   f"Aw, okay! Let me check with {e.first()} and I'll call you right back. Thank you!",
                                   "needs_you", {"offered": booking, "why": why})
        if status == "booked" and not (CONFIRMED.search(str(text)) and e.agreed_time):
            status = "talking"  # (nothing confirmed by them on a time code accepted: keep talking)
        if status in ("declined", "needs_you", "goodbye"):
            words = self._stream_words(gen)
            return self._closed_with(words, {"goodbye": "no_deal"}.get(status, status),
                                     {"booking": booking, "why": f"they said: {str(text)[:140]}"})
        self._stream_words(gen)

    # ------------------------------------------------------------ speaking
    def _model(self, directive):
        self.e._directive = directive
        return _from_dict(THINK(self.e, list(self.e.transcript))) if THINK else \
            _stream_model(self.e, list(self.e.transcript), directive)

    def _speak(self, directive, fallback, sentences=2, words=35):
        """The model says `directive` in its own words (checked sentence by sentence); `fallback` if it can't."""
        try:
            gen = self._model(directive)
            next(gen)  # (the header: code already decided, its status isn't used here)
            spoken = self._stream_words(gen, sentences, words, fallback=fallback)
        except Exception as ex:  # noqa: BLE001
            log.warning("errand %s: the model failed (%s); using a plain line", self.e.id, ex.__class__.__name__)
            spoken = self._plain(fallback)
        return spoken

    def _plain(self, line):
        self.e.transcript.append(("jarvis", line))
        self.send(line + " ", False)
        self.send("", True)
        return line

    def _close(self, directive, fallback, status, outcome, use_model=True):
        """The outcome is decided (by code): record it, say the closing words, then wait for their goodbye."""
        spoken = self._speak(directive, fallback) if use_model and directive else self._plain(fallback)
        return self._closed_with(spoken, status, outcome)

    def _closed_with(self, spoken, status, outcome):
        e = self.e
        self.done, self.closing = True, True
        e.status, e.outcome, e.ended = status, outcome or {}, time.time()
        finish(e)
        self._close_no += 1
        no = self._close_no
        self._timer(speak_time(spoken) + CLOSE_WAIT_S, lambda: self._no_reply_bye(no))

    def _goodbye(self):
        """They said goodbye: a short warm goodbye back, heard in full, then hang up."""
        spoken = self._speak("They're saying goodbye. Say a short, warm goodbye back (a few words).", "Thanks, bye!",
                             sentences=1, words=12)
        self._hang_up_after(spoken)

    def _no_reply_bye(self, no):
        with self._lock:
            if self._hung or not self.closing or no != self._close_no:
                return  # (they answered meanwhile, or the conversation reopened)
            self._hang_up_after(self._plain(self._rng.choice(["Okay, bye-bye!", "Alright, bye now!", "Okay, bye!"])))

    def _hang_up_after(self, spoken):
        self.done = self._hung = True
        self._timer(speak_time(spoken) + 0.5, self._finally_hang_up)

    def _finally_hang_up(self):
        if self.hang_up and not getattr(self, "_ended_line", False):
            self._ended_line = True
            try:
                self.hang_up()
            except Exception as ex:  # noqa: BLE001 (the line may already be closed)
                log.debug("errand: hang-up after close: %s", ex)

    def _stream_words(self, gen, sentences=2, words=35, fallback="Sorry, could you say that again?"):
        """Speak the model's words sentence by sentence as they arrive; each one is checked before it's sent. -> what
        was said"""
        e, buf, spoken, stop = self.e, "", [], False
        for kind, piece in gen:
            if kind != "text":
                continue
            buf += piece
            while not stop:
                m = SENTENCE.match(buf)
                if not m:
                    break
                sentence, buf = _natural(m.group(1).strip()), buf[m.end():]
                if not safe_to_say(e, sentence):
                    sentence, buf, stop = "Sorry, I can't share that - they'll sort it out when they come in.", "", True
                spoken.append(sentence)
                self.send(sentence + " ", False)
                if len(spoken) >= sentences or sum(len(x.split()) for x in spoken) >= words:
                    stop = True
            if stop:
                gen.close()
                buf = ""
                break
        tail = _natural(buf.strip())
        if tail and safe_to_say(e, tail) and len(spoken) < sentences:
            spoken.append(tail)
            self.send(tail + " ", False)
        if not spoken:  # (nothing usable came back)
            log.warning("errand %s: the model's reply was empty", e.id)
            spoken = [fallback]
            self.send(fallback + " ", False)
        said = " ".join(spoken)
        e.transcript.append(("jarvis", said))
        self.send("", True)
        return said

    def closed(self):
        """The line dropped / they hung up before an outcome."""
        self._hung = True
        if not self.done:
            self.done = True
            e = self.e
            e.status, e.ended = ("no_deal" if e.turns else "failed"), time.time()
            e.outcome = e.outcome or {"why": "the call ended before a booking"}
            finish(e)


def hints(e):
    """Words and phrases this call is likely to contain, for speech recognition (Twilio's `hints`)."""
    words = [e.business, e.name, *(e.name or "").split(), e.date, "reservation", "table", "party of", "people",
             "booked", "all set", "available", "o'clock", "seven thirty", "eight", "under the name", "how many",
             "what time", "hold on", "wrong number"]
    return ",".join(dict.fromkeys(w.replace(",", " ") for w in words if w))[:500]


def summary(e):
    b = (e.outcome or {}).get("booking") or {}
    if e.status == "booked":
        return (f"Booked at {e.business}: {b.get('date') or e.date} {b.get('time')}, party of {b.get('party_size')}"
                + (f", under {b.get('name') or e.name}" if (b.get('name') or e.name) else "")
                + (f", ref {b['reference']}" if b.get("reference") else "") + ". Not added to your calendar yet.")
    if e.status == "needs_you":
        why = (e.outcome or {}).get("why") or "they asked something only you can answer"
        return f"{e.business} needs your decision: {why}. Nothing was booked."
    return f"No booking at {e.business} ({e.status}: {(e.outcome or {}).get('why', '')}). Nothing was booked."


def finish(e):
    """Save the errand and text you the outcome (your own phone only)."""
    save(e)
    log.info("errand %s: %s", e.id, summary(e))
    try:
        from room_agent.phone import texts

        texts.text_me("Jarvis errand: " + summary(e))
    except Exception as ex:  # noqa: BLE001
        log.info("errand %s: the summary text wasn't sent (%s)", e.id, ex.__class__.__name__)


def save(e):
    path = config.ERRANDS_FILE
    with _lock:
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except (OSError, ValueError):
            data = []
        data = [x for x in data if x.get("id") != e.id] + [asdict(e)]
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data[-50:], indent=1), encoding="utf-8")
        tmp.replace(path)


def start(errand, client=None):
    """Place the errand call (TEST MODE: to your own phone). -> the errand. The phone server must be running."""
    from room_agent.phone import server

    missing = server.ready()
    if missing:
        raise RuntimeError("phone mode isn't set up: " + ", ".join(missing))
    ACTIVE[errand.id] = errand
    server.place_call("errand", "", {"kind": "errand", "errand_id": errand.id}, client=client)
    errand.status = "calling"
    return errand


def _main():
    """A real test call to your own phone: its own local server + tunnel (the running Jarvis isn't touched)."""
    import argparse
    import asyncio
    import socket

    ap = argparse.ArgumentParser()
    ap.add_argument("--business", default="Luigi's Trattoria")
    ap.add_argument("--party", type=int, default=4)
    ap.add_argument("--date", default="Friday")
    ap.add_argument("--from", dest="time_from", default="19:00")
    ap.add_argument("--to", dest="time_to", default="20:00")
    ap.add_argument("--name", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--relation", default="", help="who they are to Jarvis, e.g. boss")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    from aiohttp import web

    from room_agent.phone import server, tunnel

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    url = tunnel.start(port)
    if not url:
        raise SystemExit("couldn't open a public tunnel for the test call")
    config.PUBLIC_URL = url  # (this process only; the number's settings and the running Jarvis are untouched)

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        runner = web.AppRunner(server.make_app(), access_log=None)
        loop.run_until_complete(runner.setup())
        loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", port).start())
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    # A new tunnel address can take a while to be reachable: never dial before it answers (else Twilio can't fetch the
    # call's instructions and the person hears "an application error has occurred").
    import requests

    deadline = time.time() + 45
    while True:
        try:
            if requests.get(f"{url}/phone/health", timeout=5).status_code == 200:
                break
        except requests.RequestException:
            pass
        if time.time() > deadline:
            tunnel.stop()
            raise SystemExit("the public tunnel never became reachable; no call was placed")
        time.sleep(1.5)
    # (run as "python -m", this file is __main__: the phone server uses room_agent.phone.errand, so use that one)
    from room_agent.phone import errand as mod

    e = mod.Errand(business=a.business, party_size=a.party, date=a.date, time_from=a.time_from, time_to=a.time_to,
                   name=a.name, notes=a.notes, relation=a.relation)
    mod.start(e)
    print(f"Calling your phone now. You're the restaurant ({e.business}). Errand {e.id}.", flush=True)
    end = time.time() + MAX_SECONDS + 120
    while time.time() < end and e.status in ("new", "calling"):
        time.sleep(1)
    print("Outcome:", mod.summary(e), flush=True)
    print("Transcript:", flush=True)
    for who, text in e.transcript:
        print(f"  {who}: {text}", flush=True)
    tunnel.stop()


if __name__ == "__main__":
    _main()
