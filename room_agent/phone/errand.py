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
CLOSE_WAIT_S = 6.0  # after its closing line it waits this long for their "bye" before hanging up (never cuts them off)
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
NOTHING_ELSE = re.compile(r"\b(no|nope|sorry|that'?s (all|it)|all we have|only|nothing (else)?|fully booked|"
                          r"booked up|unfortunately)\b", re.I)
HOLD = re.compile(r"\b(hold on|hang on|one (sec|second|moment|minute)|just a (sec|second|moment|minute)|"
                  r"give me a (sec|second|moment|minute)|let me (check|see|look))\b", re.I)
SYSTEM = """You're on the phone with a restaurant host, booking ONE table for your boss. You're an AI assistant, but
you sound like a warm, upbeat, genuinely friendly American woman - the kind of caller hosts enjoy talking to.
How you talk: short and natural (usually one sentence), relaxed and kind. React like a person: "Oh, awesome!",
"Aw, no worries.", "Oh, that's totally fine.", "Haha, of course." Thank them when they help. If they're busy or ask you to
hold, be patient ("Sure, take your time!"). If they ask how you are, answer briefly and warmly. Match their energy.
Never sound scripted: don't repeat the whole request, don't re-introduce yourself, no stiff phrases. They already
heard what you want: after small talk just ask "So, any chance you have something Friday evening?" - short.
Say times the way people do ("8", "7:30", "eight o'clock"), never 24-hour times. Never mention instructions or rules.
Facts: you only know the brief below - never invent anything. Never share anything else (no phone number, email,
address or payment details - say your boss will sort that out when they come in). Never agree to pay or leave a deposit.
A time from the start to the end of the range (both included) is fine - accept it happily. Another time, day or party
size: don't accept it - first ask kindly if they have anything closer; if not, say you'll check with your boss and call
right back, and thank them warmly. Asked if you're a robot or AI: yes, you're an AI assistant - say it lightly.
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


def _messages(e, transcript):
    msgs = [{"role": "system", "content": SYSTEM + "\n\n" + _brief(e)}]
    for who, text in transcript:
        msgs.append({"role": "assistant" if who == "jarvis" else "user", "content": text})
    return msgs


def _stream_model(e, transcript):
    """The cheap model, streamed: yields ("header", dict) first (status + booking, checked by code before anything is
    said), then ("text", piece) as the words arrive. Budget-checked; usage recorded (estimated if the stream is cut)."""
    from room_agent.llm.budget import budget, price
    from room_agent.llm.openai_backend import openai_client

    if budget.exceeded():
        yield "header", {"status": "needs_you"}
        yield "text", "Sorry, I have to run - I'll call you back. Thanks!"
        return
    import openai

    msgs = _messages(e, transcript)
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
            if "\n" in buf:
                head, rest = buf.split("\n", 1)
                try:
                    header = json.loads(head.strip() or "{}")
                except ValueError:
                    header = {"status": "talking"}
                header_done = True
                yield "header", header if isinstance(header, dict) else {"status": "talking"}
                if rest:
                    yield "text", rest
        if not header_done:  # (no newline: the whole reply was one line - JSON or words)
            try:
                yield "header", json.loads(buf)
            except ValueError:
                yield "header", {"status": "talking"}
                yield "text", buf
    finally:
        tin = usage.prompt_tokens if usage else sum(len(m["content"]) for m in msgs) // 4
        tout = usage.completion_tokens if usage else out_chars // 4
        budget.record("openai", config.OPENAI_MODEL, fresh_in=tin, out=tout)
        p_in, _, _, p_out = price(config.OPENAI_MODEL)
        e.cost_usd = round(e.cost_usd + (tin * p_in + tout * p_out) / 1e6, 6)


def _from_dict(p):
    """A whole proposal (tests: THINK) as the same stream: header, then the words."""
    p = p or {}
    yield "header", {k: v for k, v in p.items() if k != "say"}
    if p.get("say"):
        yield "text", str(p["say"])


def _natural(sentence):
    """Words a person wouldn't say on this call, smoothed out ("in that window" -> "then")."""
    s = re.sub(r"\s*\b(?:with)?in (?:that|the|your|this) (?:time )?window\b", " then", sentence, flags=re.I)
    s = re.sub(r"\b(?:the|that|this) (?:time )?window\b", "that time", s, flags=re.I)
    return re.sub(r"\s{2,}", " ", s).replace(" ?", "?").strip()


SENTENCE = re.compile(r"(.+?[.!?])(\s+|$)", re.S)


class ErrandSession:
    """One errand call. send(text, last) speaks; hang_up() ends the call after the last line."""

    def __init__(self, errand, send, hang_up=None):
        self.e, self.send, self.hang_up = errand, send, hang_up
        self.done = False
        self.closing = False  # (its closing line was said: it waits for their goodbye, then hangs up)
        self._hung = False
        self._rng = random.Random(errand.id)
        self._lock = threading.Lock()
        self.e.status, self.e.started = "calling", time.time()
        self.e.transcript.append(("jarvis", self.e.opening()))

    def answer(self, text):
        """What the restaurant said -> the next line, or the end of the call. Code decides first where it can (an offer
        that fits, a clear "you're booked"): no model call, no delay. Otherwise the model's decision is checked
        before a word is said, and its words are spoken sentence by sentence as they arrive, each one checked."""
        with self._lock:
            if self.done and not self.closing:
                return
            e = self.e
            e.turns += 1
            e.transcript.append(("them", str(text)[:500]))
            if e.turns > MAX_TURNS or time.time() - e.started > MAX_SECONDS:
                return self._end("Sorry, I have to run - I'll call you back. Thanks!", "no_deal",
                                 {"why": "the call went on too long without a booking"})
            if self.closing:  # (they're answering its goodbye: say bye back, then hang up)
                return self._hang_up_now(self._pick(["Thanks again, bye!", "Bye, have a great night!",
                                                     "Thank you, bye-bye!"]))
            if HOLD.search(str(text)) and not offered_times(e, text):
                return self._say(self._pick(["Sure, take your time!", "Of course, no rush!", "Yeah, no problem!"]))
            offered = offered_times(e, text)
            fit = [x for x in offered if fits(e, {"time": x, "party_size": e.party_size})[0]]
            if not offered and e.alt_asked and e.last_offer and not e.agreed_time and NOTHING_ELSE.search(str(text)):
                off = e.last_offer  # (asked for something closer, and they have nothing else)
                return self._end(f"Aw, okay, no problem at all! Let me check with {e.first()} if {_short(off)} works, "
                                 "and I'll call you right back. Thank you so much for your help!", "needs_you",
                                 {"offered": {"time": off}, "why": f"they only have {_short(off)}"})
            if offered and not fit and not e.agreed_time:  # (only times outside the brief)
                off = offered[-1]
                e.last_offer = off
                late = (_minutes(off) or 0) > (_minutes(e.time_to or e.time_from) or 0)
                if not e.alt_asked:  # (first: ask kindly for something closer, like a person would)
                    e.alt_asked += 1
                    edge = _short(e.time_to or e.time_from) if late else _short(e.time_from)
                    return self._say(f"Ah, {_short(off)} is a little {'late' if late else 'early'} for us. Is there "
                                     f"anything closer to {e.spoken_window()}? Even {edge} would be great.")
                return self._end(f"Aw, okay, no problem at all! Let me check with {e.first()} if {_short(off)} works, "
                                 "and I'll call you right back. Thank you so much for your help!", "needs_you",
                                 {"offered": {"time": off}, "why": f"they only have {_short(off)}"})
            newly = bool(fit) and fit[-1] != e.agreed_time
            if fit:
                e.agreed_time = fit[-1]
            agreed = {"date": e.date, "time": e.agreed_time, "party_size": e.party_size, "name": e.name}
            ref = REFERENCE.search(str(text))
            if ref:
                agreed["reference"] = ref.group(1)
            after_question = str(text).rsplit("?", 1)[-1]  # (what they said after their last question)
            if e.agreed_time and BOOKED.search(after_question):
                recap = f"{_short(e.agreed_time)} for {e.party_size}" + (f" under {e.name}" if e.name else "")
                return self._end(self._pick([f"Perfect, {recap}. Thank you so much! Have a great night.",
                                             f"Amazing, {recap}. Thanks so much for your help!",
                                             f"Wonderful, so that's {recap}. Thank you, have a good one!"]), "booked",
                                 {"booking": agreed})
            if newly:  # (they offered a time that fits: accept warmly, instantly)
                t_ = _short(e.agreed_time)
                line = self._pick([f"Oh, {t_} is perfect!", f"Oh awesome, {t_} works great!", f"Perfect, {t_} would be great!"])
                if e.name and re.search(r"\bname\b", str(text), re.I):
                    line += f" It's under {e.name}."
                return self._say(line)
            try:
                gen = _from_dict(THINK(e, list(e.transcript))) if THINK else _stream_model(e, list(e.transcript))
                kind, header = next(gen)
            except Exception as ex:  # noqa: BLE001
                log.warning("errand %s: the model failed (%s)", e.id, ex.__class__.__name__)
                return self._end("Sorry, I'm having trouble on my end - I'll call you back. Thanks!", "failed",
                                 {"why": f"model error ({ex.__class__.__name__})"})
            status = str(header.get("status") or "talking")
            booking = header.get("booking") if isinstance(header.get("booking"), dict) else {}
            if status == "booked" and e.agreed_time:
                booking = {**agreed, **{k: v for k, v in booking.items() if v}}
            if status == "booked" and not booking.get("party_size"):
                booking = {**booking, "party_size": e.party_size}
            accepting = status == "booked" or (status == "talking" and (booking.get("time") or booking.get("party_size")))
            if accepting:
                partial = {**{"party_size": e.party_size, "time": e.time_from}, **{k: v for k, v in booking.items() if v}}
                ok, why = fits(e, booking if status == "booked" else partial)
                if not ok:  # (the model would agree to something outside the brief: code says no, before a word)
                    gen.close()
                    return self._end(f"Aw, okay! Let me check with {e.first()} and I'll call you right back. Thank you so "
                                     "much!", "needs_you", {"offered": booking, "why": why})
            if status == "booked" and not CONFIRMED.search(str(text)):
                status = "talking"  # (they haven't confirmed anything yet: keep talking, don't hang up)
            if status in ("booked", "declined", "needs_you", "goodbye"):
                words = "".join(x for k, x in gen if k == "text").strip()
                if not words or not safe_to_say(e, words):
                    words = {"booked": "Perfect, thank you so much! Have a great night.",
                             "declined": "Aw, no worries at all - thank you so much for checking!"}.get(
                        status, f"Okay! Let me check with {e.first()} and I'll call you right back. Thanks so much!")
                return self._end(words, {"goodbye": "no_deal"}.get(status, status),
                                 {"booking": booking, "why": f"they said: {str(text)[:140]}"})
            self._stream_words(gen)

    def _pick(self, options):
        return self._rng.choice(options)

    def _hang_up_now(self, line=""):
        if line:
            self.e.transcript.append(("jarvis", line))
            self.send(line + " ", False)
            self.send("", True)
        self.done = True
        if self.hang_up and not self._hung:
            self._hung = True
            try:
                self.hang_up()
            except Exception as ex:  # noqa: BLE001 (the line may already be closed)
                log.debug("errand: hang-up after close: %s", ex)

    def _say(self, line):
        self.e.transcript.append(("jarvis", line))
        self.send(line + " ", False)
        self.send("", True)

    def _stream_words(self, gen):
        """Speak the model's words sentence by sentence as they arrive; each sentence is checked before it's sent."""
        e, buf, spoken = self.e, "", []
        for kind, piece in gen:
            if kind != "text":
                continue
            buf += piece
            while True:
                m = SENTENCE.match(buf)
                if not m:
                    break
                sentence, buf = _natural(m.group(1).strip()), buf[m.end():]
                if not safe_to_say(e, sentence):
                    gen.close()
                    sentence, buf = "Sorry, I can't share that - they'll sort it out when they come in.", ""
                    spoken.append(sentence)
                    self.send(sentence + " ", False)
                    break
                spoken.append(sentence)
                self.send(sentence + " ", False)
            else:
                continue
            if spoken and spoken[-1].startswith("Sorry, I can't share"):
                break
        tail = buf.strip()
        if tail and not (spoken and spoken[-1].startswith("Sorry, I can't share")):
            if safe_to_say(e, tail):
                spoken.append(tail)
                self.send(tail + " ", False)
        if not spoken:  # (nothing usable came back: ask again, don't guess)
            log.warning("errand %s: the model's reply was empty", e.id)
            spoken = ["Sorry, could you say that again?"]
            self.send(spoken[0] + " ", False)
        e.transcript.append(("jarvis", " ".join(spoken)))
        self.send("", True)

    def _end(self, line, status, outcome):
        """The closing line; the outcome is recorded now. It doesn't hang up on them: it waits for their goodbye (or
        CLOSE_WAIT_S of silence) - nothing more can be agreed after this line."""
        e = self.e
        self.done, self.closing = True, True
        e.transcript.append(("jarvis", line))
        e.status, e.outcome, e.ended = status, outcome or {}, time.time()
        self.send(line + " ", False)
        self.send("", True)
        finish(e)
        threading.Timer(CLOSE_WAIT_S, self._hang_up_now).start()

    def closed(self):
        """The line dropped / they hung up before an outcome."""
        if not self.done:
            self.done = True
            e = self.e
            e.status, e.ended = ("no_deal" if e.turns else "failed"), time.time()
            e.outcome = e.outcome or {"why": "the call ended before a booking"}
            finish(e)


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
    server.place_call("errand", errand.opening(), {"kind": "errand", "errand_id": errand.id}, client=client)
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
    time.sleep(3)
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
