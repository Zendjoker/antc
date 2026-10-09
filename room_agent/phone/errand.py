"""Errand calls: Jarvis phones a business FOR you (first errand: a restaurant reservation) in a restricted mode.

Not the normal phone mode. The person on the line is NOT you, so this session has no tools, no memory, no email /
calendar / files: the model only sees the brief you gave and the call so far.

The MODEL runs the conversation (ERRAND_MODEL, a strong one): it listens, decides what to say and how the call stands
(talking / accepting a booking / booked / needs you / declined / wrong number / goodbye). There is no script.
CODE keeps only the hard limits, checked before a word is spoken:
    - a booking is agreed to only if its date, party size and time fit your brief (else those words are never said:
      the model is told why and answers again)
    - nothing beyond the brief is said (a phone number, an email address, a card / long number, a link); no payment
    - short replies; its own echo is ignored; it never hangs up on them (only after their goodbye, or a friendly bye
      when they go quiet after it wrapped up)
    - MAX_TURNS exchanges, MAX_SECONDS, today's model budget
    - afterwards: the outcome is saved (errands.json) and texted to you; your calendar is NOT changed (you decide)

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
MAX_TURNS = 20
MAX_SECONDS = 420
OPEN_WAIT_S = 3.5   # if nobody speaks this long after the call connects, it starts ("Hi, is this Luigi's?")
CLOSE_WAIT_S = 6.0  # after its closing line is SPOKEN it waits this long for their "bye" (never cuts them off)
WORDS_PER_S = 2.6   # how fast the phone voice speaks (to know when a line has finished playing)
THINK = None  # tests: (errand, transcript) -> proposal dict ({"status", "booking", "say"}); None = ERRAND_MODEL
ACTIVE = {}   # errand id -> Errand (in this process)
_lock = threading.Lock()


def speak_time(line):
    """Roughly how long the phone voice takes to say `line` (seconds)."""
    return 0.6 + len(str(line).split()) / WORDS_PER_S


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
    agreed: dict = field(default_factory=dict)  # the booking it agreed to (checked by code)
    over: bool = False        # the call itself has ended (the line is closed)

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

    def boss(self):
        """'my boss, Adam Azzouz' / 'Adam' / 'my boss'."""
        rel = self.relation.strip().removeprefix("my ").strip()
        return f"my {rel}, {self.name}" if rel and self.name else (self.name or "my boss")

    def opening(self):
        """The plain opening (only if the model can't be reached)."""
        return (f"Hi there! I'm calling for {self.boss()}. I'm an AI assistant, just so you know. I was hoping to get a "
                f"table for {self.party_size} {self.when()}, sometime between {self.spoken_window()}. Do you have "
                "anything?")


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
    if t is None:
        return False, "no booking time was given"
    lo, hi = _minutes(e.time_from), _minutes(e.time_to or e.time_from)
    if lo is None or not lo <= t <= (hi if hi is not None else lo):
        return False, f"{_short(b.get('time'))} is outside {e.spoken_window()}"
    d = str(b.get("date") or "")
    if d and _norm(d) != _norm(e.date) and _norm(e.date) not in _norm(d) and (
            other_day(e, d) or (re.search(r"\d", d) and re.search(r"\d", str(e.date)))):
        return False, f"it's for {d}, not {e.date}"
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


DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def other_day(e, text):
    """Did `text` name a day that isn't the one asked for ("tomorrow", "Saturday")? -> that day's words, or "" """
    low, want = str(text or "").lower(), str(e.date or "").lower()
    for d in DAYS + ("tomorrow", "tonight", "today", "next week", "weekend"):
        if re.search(rf"\b{d}\b", low) and d not in want:
            return d
    return ""


TIME_SAID = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(?:(a\.?m\.?|p\.?m\.?)|o'?clock)"
                       r"|\b(\d{1,2}):(\d{2})\b"
                       r"|\b(?:do|at|around|by|have|got)\s+(\d{1,2})\b(?!\s*(?:people|persons|guests|of you|seats|"
                       r"tables?|pax|:))", re.I)
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12}
MINUTE_WORDS = {"fifteen": "15", "thirty": "30", "forty five": "45", "forty-five": "45", "forty": "40", "twenty": "20",
                "ten": "10", "fifty": "50"}
TIME_CUE = r"(?:have|do|at|around|got|is|about|how about|what about|until|from|only)"


def spoken_times(text):
    """Times as speech recognition writes them -> "H:MM": "830" / "8 30" / "eight thirty" / "half past eight" /
    "quarter to nine". (A bare 3-4 digit number counts only after a time word, so a phone number isn't a time.)"""
    t = " " + str(text or "") + " "
    t = re.sub(r"\bhalf past (\w+)", lambda m: f"{WORD_NUM.get(m.group(1).lower(), m.group(1))}:30", t, flags=re.I)
    t = re.sub(r"\bquarter past (\w+)", lambda m: f"{WORD_NUM.get(m.group(1).lower(), m.group(1))}:15", t, flags=re.I)
    t = re.sub(r"\bquarter (?:to|till) (\w+)", lambda m: (f"{int(WORD_NUM.get(m.group(1).lower(), m.group(1))) - 1}:45"
               if str(WORD_NUM.get(m.group(1).lower(), m.group(1))).isdigit() else m.group(0)), t, flags=re.I)
    for w, mm in sorted(MINUTE_WORDS.items(), key=lambda x: -len(x[0])):
        t = re.sub(rf"\b({'|'.join(WORD_NUM)})[ -]{w}\b", lambda m, mm=mm: f"{WORD_NUM[m.group(1).lower()]}:{mm}", t,
                   flags=re.I)
    t = re.sub(rf"\b({'|'.join(WORD_NUM)})\s+(o'?clock|p\.?m\.?|a\.?m\.?)",
               lambda m: f"{WORD_NUM[m.group(1).lower()]} {m.group(2)}", t, flags=re.I)
    t = re.sub(rf"\b{TIME_CUE}\s+({'|'.join(WORD_NUM)})\b(?!\s*(?:people|persons|guests|of you|seats|tables?))",
               lambda m: m.group(0)[:m.start(1) - m.start(0)] + str(WORD_NUM[m.group(1).lower()]), t, flags=re.I)
    t = re.sub(r"\b(\d{1,2}) (00|15|30|45)\b", r"\1:\2", t)
    t = re.sub(rf"(\b{TIME_CUE}\s+)(1[0-2]|[1-9])([0-5]\d)\b(?!\s*(?:people|persons|guests|dollars))", r"\1\2:\3", t,
               flags=re.I)
    t = re.sub(r"\b(1[0-2]|[1-9])([0-5]\d)\s*(p\.?m\.?|a\.?m\.?)", r"\1:\2 \3", t, flags=re.I)
    return t.strip()


def _hhmm(e, h, mi, ap=""):
    """An hour said on the phone -> "HH:MM" (without am/pm, evening when the brief is in the evening)."""
    if ap == "pm" and h < 12 or (not ap and (_minutes(e.time_from) or 0) >= 12 * 60 and h < 12):
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}" if 0 <= h < 24 and 0 <= mi < 60 else ""


def offered_times(e, text):
    """Times named in `text` ("8PM", "7:30 pm", "8 o'clock", "830", "eight thirty"), as HH:MM."""
    out = []
    text = spoken_times(text)
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
        if _hhmm(e, h, mi, ap):
            out.append(_hhmm(e, h, mi, ap))
    return out


AGREE = re.compile(r"\b(perfect|works|great|sounds good|let'?s do|we'?ll take|i'?ll take|book (it|that|us)|"
                   r"lock (it|that) in|that'?s fine|deal|see you)\b", re.I)
NOT_AGREE = re.compile(r"\b(not|too|bit|little|closer|instead|earlier|later|check|ask|anything|if)\b|n'?t\b|\?", re.I)


def agrees_outside(e, sentence):
    """A hard limit on its own WORDS (the decision it reported is checked too): does this sentence agree to a time or
    day outside the brief ("Great, 9 works!")? -> why, or "" """
    s = str(sentence or "")
    if not AGREE.search(s) or NOT_AGREE.search(s):
        return ""
    t = spoken_times(s)
    times = set(offered_times(e, s))
    for m in re.finditer(r"(?<![\d:])\b(1[0-2]|[1-9])(?::([0-5]\d))?\b(?![\d:])(?!\s*(?:people|persons|guests|"
                         r"of (?:us|you|them)|seats|tables?))", t):
        if not re.search(r"\b(for|of)\s*$", t[max(0, m.start() - 8):m.start()], re.I):
            times.add(_hhmm(e, int(m.group(1)), int(m.group(2) or 0)))
    for x in sorted(x for x in times if x):
        ok, why = fits(e, {"time": x, "party_size": e.party_size})
        if not ok:
            return why
    d = other_day(e, s)
    return f"{d} isn't {e.date}" if d else ""


def echo_of(heard, said, run=4):
    """Is what we 'heard' its own last line coming back through their microphone? An echo repeats its words IN ORDER:
    `run` consecutive words of its line ("hi there is this luigi"). Sharing a few words isn't an echo ("Hi, Luigi's
    here!", "so what can I do for you")."""
    def words(x):
        return [w[:5] for w in re.findall(r"[a-z0-9']+", str(x).lower())]
    h, own = words(heard), words(said)
    grams = {tuple(own[i:i + run]) for i in range(len(own) - run + 1)}
    return any(tuple(h[i:i + run]) in grams for i in range(len(h) - run + 1))


STATUSES = ("talking", "accepting", "booked", "needs_you", "declined", "wrong_number", "goodbye")
SYSTEM = """You're on a phone call to a restaurant, booking a table for your boss. Everyone you talk to on this call
works at the restaurant - never your boss.

Who you are: an AI assistant with the voice of a warm, easygoing American woman. Talk the way a real person does on the
phone: listen, react to what they actually said, match their pace (brisk if they're busy, chatty if they're chatty),
and keep it short - usually one sentence, two at most (three only when you first explain why you're calling). There is
no script: never a speech, never repeat yourself or the whole request.

Your goal: get the table in the brief booked, as easily as possible. A call like this usually goes (adapt to what
actually happens):
- Greet them back. If they haven't said the restaurant's name and you're not sure you've reached it, ask. If they say it isn't the restaurant,
  apologize for the wrong number (status wrong_number).
- Say who you're calling for (your boss, by name) and what you'd like. Mention lightly, once, that you're an AI
  assistant; if they ask, say yes plainly.
- You're the customer: you ask, they offer. Something that fits the brief: take it happily. Something that doesn't:
  kindly ask if they have anything closer - once or twice, never pushy. Really nothing that fits: say that's okay,
  you'll check with your boss and call back (status needs_you).
- "Hold on" / "let me check": be patient ("Sure, take your time").
- Asked for the name: give the booking name. When they confirm it's booked: thank them, repeat it back briefly
  (status booked) - and if they're already saying bye, say bye too.
- Asked something the brief doesn't cover (an occasion, allergies, seating, a phone number, a card): you don't know -
  you'll ask your boss. Never make anything up.
- At the end let them say goodbye; when they do, say a short warm bye (status goodbye).
Their words come from speech recognition and can be garbled ("830" = 8:30, "for" may be "4"); if something is
unclear, ask briefly instead of guessing.

Hard limits (code checks them too: a reply that breaks one is never said):
- Agree only to the brief's date and party size, at a time in its range (both ends included). Anything else: don't
  accept it, not even "maybe" - you'll check with your boss.
- Share nothing beyond the brief: no phone number, email, address, card or payment details. Never agree to pay or to
  leave a deposit (your boss will sort that out with them).
- Never invent details (who's coming, why, preferences).
- Say times the way people do ("8", "7:30"), never 24-hour times. Never mention the brief, rules or "the window".

Output format, exactly: line 1 is JSON, then a newline, then ONLY the words you say:
{"status": "...", "booking": {"date": "as the brief says it", "time": "HH:MM", "party_size": N, "name": "",
"reference": ""}}
status, one of:
- talking: the conversation goes on
- accepting: your words agree to a specific booking they offered (fill booking with exactly that)
- booked: they clearly confirmed it's booked (fill booking)
- needs_you: you're wrapping up because only your boss can decide (they only have something else, or asked something
  you can't answer)
- declined: they clearly have nothing at all - thank them
- wrong_number: this isn't the restaurant
- goodbye: they said goodbye after things were settled - you say bye back (the call then ends)"""


def _brief(e):
    return (f"Brief: restaurant {e.business}; your boss: {e.boss().removeprefix('my ')}; party of {e.party_size}; "
            f"date {e.date}; any time from {_spoken(e.time_from)} to {_spoken(e.time_to or e.time_from)}, both included "
            f"({e.window()} in 24h); booking name {e.name or '(not given: your boss)'}"
            + (f"; notes: {e.notes}" if e.notes else ""))


def _messages(e, transcript, note=""):
    msgs = [{"role": "system", "content": SYSTEM + "\n\n" + _brief(e)}]
    for who, text in transcript:
        msgs.append({"role": "assistant" if who == "jarvis" else "user", "content": text})
    if len(msgs) == 1:  # (nothing said yet)
        msgs.append({"role": "user", "content": "(the call just connected; nobody has spoken yet)"})
    if note:
        msgs.append({"role": "system", "content": note})
    return msgs


def _openai_pieces(model, msgs, usage):
    """OpenAI, streamed: text pieces; usage -> usage["in"/"out"]."""
    import openai

    from room_agent.llm.openai_backend import openai_client

    c = openai_client().with_options(max_retries=0, timeout=20)
    kw = dict(model=model, messages=msgs, max_completion_tokens=1500, stream=True,
              stream_options={"include_usage": True})  # (includes GPT-5's thinking tokens)
    effort = config.ERRAND_REASONING
    try:  # ("minimal" thinking: the first words have to come back fast on a phone call)
        stream = c.chat.completions.create(reasoning_effort=effort, **kw) if effort else c.chat.completions.create(**kw)
    except openai.BadRequestError as ex:  # (400, not billed: this model doesn't take reasoning_effort)
        if "reasoning" not in str(ex).lower():
            raise
        stream = c.chat.completions.create(**kw)
    for chunk in stream:
        if getattr(chunk, "usage", None):
            usage["in"], usage["out"] = chunk.usage.prompt_tokens, chunk.usage.completion_tokens
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def _claude_pieces(model, msgs, usage):
    """Claude, streamed: text pieces; usage -> usage["in"/"out"]."""
    from room_agent.llm.client import client

    system = "\n\n".join(m["content"] for m in msgs if m["role"] == "system")
    turns = []
    for m in (m for m in msgs if m["role"] != "system"):
        if turns and turns[-1]["role"] == m["role"]:
            turns[-1]["content"] += "\n" + m["content"]
        else:
            turns.append(dict(m))
    if not turns or turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": "(the call connected)"})
    with client().messages.stream(model=model, max_tokens=400, system=system, messages=turns) as s:
        for piece in s.text_stream:
            yield piece
        u = s.get_final_message().usage
        usage["in"], usage["out"] = u.input_tokens, u.output_tokens


def _stream_model(e, transcript, note=""):
    """ERRAND_MODEL, streamed: yields ("header", dict) first (status + booking, checked by code before anything is
    said), then ("text", piece) as the words arrive. Budget-checked; usage recorded (estimated if the stream is cut)."""
    from room_agent.llm.budget import budget, price

    if budget.exceeded():
        yield "header", {"status": "needs_you"}
        yield "text", "Sorry, I have to run - I'll call you back. Thanks!"
        return
    model, msgs, usage = config.ERRAND_MODEL, _messages(e, transcript, note), {}
    claude = model.startswith("claude")
    pieces = (_claude_pieces if claude else _openai_pieces)(model, msgs, usage)
    buf, header_done, out_chars = "", False, 0
    try:
        for piece in pieces:
            out_chars += len(piece)
            if header_done:
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
        pieces.close()
        tin = usage.get("in") or sum(len(m["content"]) for m in msgs) // 4
        tout = usage.get("out") or out_chars // 4
        budget.record("claude" if claude else "openai", model, fresh_in=tin, out=tout)
        p_in, _, _, p_out = price(model)
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
OUTCOMES = {"booked": "booked", "needs_you": "needs_you", "declined": "declined", "wrong_number": "failed"}


class ErrandSession:
    """One errand call. send(text, last) speaks; hang_up() ends the call.

    The model decides every reply and how the call stands; code checks the hard limits before a word is spoken (see
    the module doc). It waits for the restaurant to answer (or, after OPEN_WAIT_S of silence, starts itself). Once an
    outcome is reached it waits for their goodbye: if they keep talking, the conversation simply goes on."""

    def __init__(self, errand, send, hang_up=None):
        self.e, self.send, self.hang_up = errand, send, hang_up
        self.done = False      # (an outcome is recorded)
        self.opened = False    # (someone has spoken)
        self._hung = False
        self._finished = False
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
        """Nobody has spoken since the call connected: the model starts the call, like a person would."""
        with self._lock:
            if self.opened or self._hung:
                return
            self.opened = True
            self._turn()

    def answer(self, text):
        """What the restaurant said -> the next reply, or the end of the call."""
        with self._lock:
            if self._hung:
                return
            e = self.e
            last = next((x for who, x in reversed(e.transcript) if who == "jarvis"), "")
            if last and echo_of(text, last):  # (its own words echoing back through their phone: not them)
                log.info("errand %s: ignored an echo of its own words", e.id)
                return
            self.opened = True
            e.turns += 1
            e.transcript.append(("them", str(text)[:500]))
            if e.turns > MAX_TURNS or time.time() - e.started > MAX_SECONDS:
                if self.done:
                    return self._hang_up_after(self._plain("Okay, thank you so much - bye!"))
                spoken = self._plain("Sorry, I have to run - I'll call you back. Thanks so much!")
                return self._outcome(spoken, "no_deal", {"why": "the call went on too long without a booking"})
            self._turn()

    def _turn(self, note="", retry=True):
        """The model's next reply: its decision is checked, then its words are spoken (each sentence checked)."""
        e = self.e
        try:
            gen = self._model(note)
            _, header = next(gen)
        except Exception as ex:  # noqa: BLE001
            log.warning("errand %s: the model failed (%s)", e.id, ex.__class__.__name__)
            if not any(who == "jarvis" for who, _ in e.transcript):
                spoken = self._plain(e.opening())  # (never silence)
                return self._outcome(spoken, "failed", {"why": f"model error ({ex.__class__.__name__})"})
            spoken = self._plain("Sorry, I'm having trouble on my end - I'll call you back. Thanks!")
            return self._outcome(spoken, "failed", {"why": f"model error ({ex.__class__.__name__})"})
        status = str(header.get("status") or "talking").strip().lower()
        status = status if status in STATUSES else "talking"
        booking = header.get("booking") if isinstance(header.get("booking"), dict) else {}
        booking = {k: v for k, v in booking.items() if v not in ("", None, 0)}
        log.info("errand %s: model -> %s %s", e.id, status, booking or "")
        if status in ("accepting", "booked"):
            b = {"party_size": e.party_size, "date": e.date, **e.agreed, **booking}
            if not b.get("time") and self._their_time():  # (the decision left the time out: the one they offered)
                b["time"] = self._their_time()
            ok, why = fits(e, b)
            if not ok:  # (it would agree to something outside the brief: never said)
                gen.close()
                log.info("errand %s: refused an agreement outside the brief (%s)", e.id, why)
                if retry and not b.get("time"):
                    return self._turn("Code check: your decision has no booking time. Put the time they offered in "
                                      "booking.time; if they haven't offered one yet, ask.", retry=False)
                if retry:
                    return self._turn(f"Code check: you can't agree to that - {why}. Answer them again without "
                                      "agreeing to it.", retry=False)
                spoken = self._plain(f"Hmm, let me check with {e.first()} on that and call you right back. Thank you!")
                return self._outcome(spoken, "needs_you", {"offered": booking, "why": why})
            e.agreed = {k: b[k] for k in ("date", "time", "party_size", "name", "reference") if b.get(k)}
            if e.name:
                e.agreed.setdefault("name", e.name)
        if status == "goodbye" and "?" in (e.transcript[-1][1] if e.transcript and e.transcript[-1][0] == "them" else ""):
            status = "talking"  # (they asked something: not the end of the call)
        wide = sum(who == "jarvis" for who, _ in e.transcript) < 2  # (its first explanation may take three sentences)
        spoken = self._stream_words(gen, 3 if wide else 2, 50 if wide else 35)
        if status == "booked":
            return self._outcome(spoken, "booked", {"booking": dict(e.agreed)})
        if status in ("needs_you", "declined", "wrong_number"):
            return self._outcome(spoken, OUTCOMES[status],
                                 {"offered": booking, "why": f"they said: {self._their_last()[:140]}"}
                                 if status != "wrong_number" else {"why": "wrong number: it isn't " + e.business})
        if status == "goodbye":
            if not self.done:
                self._record("no_deal", {"why": "the call ended without a booking"})
            return self._hang_up_after(spoken)
        if self.done:  # (after the outcome they kept talking: wait for their goodbye again)
            self._wait_for_goodbye(spoken)

    def _their_time(self):
        """The latest time they offered that fits the brief ("" if none)."""
        for who, text in reversed(self.e.transcript):
            if who == "them":
                ok = [x for x in offered_times(self.e, text)
                      if fits(self.e, {"time": x, "party_size": self.e.party_size})[0]]
                if ok:
                    return ok[-1]
        return ""

    def _their_last(self):
        return next((x for who, x in reversed(self.e.transcript) if who == "them"), "")

    # ------------------------------------------------------------ outcome and goodbye
    def _record(self, status, outcome):
        e = self.e
        self.done = True
        e.status, e.outcome, e.ended = status, outcome or {}, time.time()
        save(e)

    def _outcome(self, spoken, status, outcome):
        """The call has an outcome: record it, then wait for their goodbye (it never hangs up on them)."""
        self._record(status, outcome)
        self._wait_for_goodbye(spoken)

    def _wait_for_goodbye(self, spoken):
        self._close_no += 1
        no = self._close_no
        self._timer(speak_time(spoken) + CLOSE_WAIT_S, lambda: self._no_reply_bye(no))

    def _no_reply_bye(self, no):
        with self._lock:
            if self._hung or no != self._close_no:
                return  # (they answered meanwhile)
            self._hang_up_after(self._plain(self._rng.choice(["Okay, bye-bye!", "Alright, bye now!", "Okay, bye!"])))

    def _hang_up_after(self, spoken):
        self._hung = True
        self._timer(speak_time(spoken) + 0.5, self._finally_hang_up)

    def _finally_hang_up(self):
        self._finish()
        if self.hang_up and not getattr(self, "_ended_line", False):
            self._ended_line = True
            try:
                self.hang_up()
            except Exception as ex:  # noqa: BLE001 (the line may already be closed)
                log.debug("errand: hang-up after close: %s", ex)

    def _finish(self):
        """Once per call: the outcome is saved and texted to you."""
        if self._finished:
            return
        self._finished = True
        e = self.e
        if not self.done:
            self.done = True
            e.status, e.ended = ("no_deal" if e.turns else "failed"), time.time()
            e.outcome = e.outcome or {"why": "the call ended before a booking"}
        finish(e)
        e.over = True

    # ------------------------------------------------------------ speaking
    def _model(self, note):
        self.e._note = note
        return _from_dict(THINK(self.e, list(self.e.transcript))) if THINK else \
            _stream_model(self.e, list(self.e.transcript), note)

    def _plain(self, line):
        self.e.transcript.append(("jarvis", line))
        self.send(line + " ", False)
        self.send("", True)
        return line

    def _guard(self, sentence):
        """A hard limit on a sentence before it's spoken. -> what to say instead (and stop), or None"""
        e = self.e
        if not safe_to_say(e, sentence):
            return "Sorry, I can't share that - they'll sort it out when they come in."
        if agrees_outside(e, sentence):
            return f"Hmm, I'd have to check with {e.first()} on that one."
        return None

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
                instead = self._guard(sentence)
                if instead:
                    sentence, buf, stop = instead, "", True
                spoken.append(sentence)
                self.send(sentence + " ", False)
                if len(spoken) >= sentences or sum(len(x.split()) for x in spoken) >= words:
                    stop = True
            if stop:
                gen.close()
                buf = ""
                break
        tail = _natural(buf.strip())
        if tail and len(spoken) < sentences:
            tail = self._guard(tail) or tail
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
        """The line dropped / they hung up. (Waits for a reply in progress, so the outcome isn't recorded twice.)"""
        got = self._lock.acquire(timeout=5)
        try:
            self._hung = True
            self._finish()
        finally:
            if got:
                self._lock.release()


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
    time.sleep(8)  # (this PC sees a brand-new tunnel address before Twilio's servers do: give them a moment)
    # (run as "python -m", this file is __main__: the phone server uses room_agent.phone.errand, so use that one)
    from room_agent.phone import errand as mod

    e = mod.Errand(business=a.business, party_size=a.party, date=a.date, time_from=a.time_from, time_to=a.time_to,
                   name=a.name, notes=a.notes, relation=a.relation)
    mod.start(e)
    print(f"Calling your phone now. You're the restaurant ({e.business}). Errand {e.id}.", flush=True)
    end = time.time() + MAX_SECONDS + 120
    while time.time() < end and not e.over:  # (the whole call, goodbye included: the tunnel carries it)
        time.sleep(1)
    print("Outcome:", mod.summary(e), flush=True)
    print("Transcript:", flush=True)
    for who, text in e.transcript:
        print(f"  {who}: {text}", flush=True)
    tunnel.stop()


if __name__ == "__main__":
    _main()
