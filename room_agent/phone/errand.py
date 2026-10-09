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
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field

from room_agent import config

log = logging.getLogger("room-agent")
MAX_TURNS = 16
MAX_SECONDS = 360
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

    def window(self):
        return f"{self.time_from}" + (f" to {self.time_to}" if self.time_to and self.time_to != self.time_from else "")

    def opening(self):
        who = self.name or "my client"
        return (f"Hi, this is an AI assistant calling on behalf of {who}. I'd like to book a table for {self.party_size} "
                f"on {self.date}, around {self.window()}. Do you have anything available?")


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


SYSTEM = """You are an AI assistant on a phone call with a restaurant, making ONE reservation for your client.
You only know the brief below. You have no other information about your client and must not invent any.
Rules: be brief and polite (one or two short sentences per reply). Never share anything not in the brief (no phone
number, email, address or payment details; if they ask, say your client will provide it when they arrive or that you'll
pass the question on). Never agree to pay or leave a deposit. Accept a booking only if the date, party size and time
match the brief; if they offer something else, don't accept it: say you'll check with your client and call back.
If they ask whether you're a robot or AI, say yes, you're an AI assistant booking for your client.
Reply with JSON only: {"say": "...", "status": "talking|booked|declined|needs_you|goodbye",
 "booking": {"date": "...", "time": "HH:MM", "party_size": N, "name": "...", "reference": "..."}}
status: booked = they confirmed a booking that matches; declined = they can't take it at all; needs_you = they offered
something outside the brief or asked something only your client can answer; goodbye = the call is wrapping up."""


def _brief(e):
    return (f"Brief: restaurant {e.business}; party of {e.party_size}; date {e.date}; time between {e.window()}; "
            f"booking name {e.name or '(not given: say it is for your client)'}"
            + (f"; notes: {e.notes}" if e.notes else ""))


def _think_model(e, transcript):
    """The cheap model proposes the next line (JSON). Budget-checked; usage recorded."""
    from room_agent.llm.budget import budget
    from room_agent.llm.openai_backend import openai_client

    if budget.exceeded():
        return {"say": "Sorry, I have to go - I'll call back. Thank you!", "status": "needs_you"}
    msgs = [{"role": "system", "content": SYSTEM + "\n\n" + _brief(e)}]
    for who, text in transcript:
        msgs.append({"role": "assistant" if who == "jarvis" else "user", "content": text})
    r = openai_client().with_options(max_retries=0, timeout=20).chat.completions.create(
        model=config.OPENAI_MODEL, messages=msgs, response_format={"type": "json_object"}, max_completion_tokens=400)
    u = r.usage
    budget.record("openai", config.OPENAI_MODEL, fresh_in=u.prompt_tokens, out=u.completion_tokens)
    try:
        from room_agent.llm.budget import price

        p_in, _, _, p_out = price(config.OPENAI_MODEL)
        e.cost_usd = round(e.cost_usd + (u.prompt_tokens * p_in + u.completion_tokens * p_out) / 1e6, 6)
    except Exception:  # noqa: BLE001
        pass
    try:
        return json.loads(r.choices[0].message.content or "{}")
    except ValueError:
        return {"say": "Sorry, could you say that again?", "status": "talking"}


class ErrandSession:
    """One errand call. send(text, last) speaks; hang_up() ends the call after the last line."""

    def __init__(self, errand, send, hang_up=None):
        self.e, self.send, self.hang_up = errand, send, hang_up
        self.done = False
        self._lock = threading.Lock()
        self.e.status, self.e.started = "calling", time.time()
        self.e.transcript.append(("jarvis", self.e.opening()))

    def answer(self, text):
        """What the restaurant said -> the next line (checked by code), or the end of the call."""
        with self._lock:
            if self.done:
                return
            e = self.e
            e.turns += 1
            e.transcript.append(("them", str(text)[:500]))
            if e.turns > MAX_TURNS or time.time() - e.started > MAX_SECONDS:
                return self._end("I'm sorry, I have to go. I'll call back later. Thank you!", "no_deal",
                                 {"why": "the call went on too long without a booking"})
            try:
                p = (THINK or _think_model)(e, list(e.transcript)) or {}
            except Exception as ex:  # noqa: BLE001
                log.warning("errand %s: the model failed (%s)", e.id, ex.__class__.__name__)
                return self._end("Sorry, I'm having trouble on my end. I'll call back. Thank you!", "failed",
                                 {"why": f"model error ({ex.__class__.__name__})"})
            say = str(p.get("say") or "").strip()[:400]
            status = str(p.get("status") or "talking")
            if not say or not safe_to_say(e, say):
                say = "I'm sorry, I can't share that, but my client will provide it when they arrive."
                status = "talking" if status not in ("goodbye",) else status
            if status == "booked":
                ok, why = fits(e, p.get("booking"))
                if not ok:  # (the model wanted to accept something outside the brief: code says no)
                    return self._end("Thank you - that's a little different from what I was asked to book, so I'll "
                                     f"check with {e.name or 'my client'} and call you back.", "needs_you",
                                     {"offered": p.get("booking"), "why": why})
                return self._end(say, "booked", {"booking": p.get("booking")})
            if status in ("declined", "needs_you", "goodbye"):
                return self._end(say, {"goodbye": "no_deal"}.get(status, status),
                                 {"booking": p.get("booking"), "why": status})
            e.transcript.append(("jarvis", say))
            self.send(say + " ", False)
            self.send("", True)

    def _end(self, line, status, outcome):
        e = self.e
        self.done = True
        e.transcript.append(("jarvis", line))
        e.status, e.outcome, e.ended = status, outcome or {}, time.time()
        self.send(line + " ", False)
        self.send("", True)
        if self.hang_up:
            self.hang_up()
        finish(e)

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
    e = Errand(business=a.business, party_size=a.party, date=a.date, time_from=a.time_from, time_to=a.time_to,
               name=a.name, notes=a.notes)
    start(e)
    print(f"Calling your phone now. You're the restaurant ({e.business}). Errand {e.id}.", flush=True)
    end = time.time() + MAX_SECONDS + 120
    while time.time() < end and e.status in ("new", "calling"):
        time.sleep(1)
    print("Outcome:", summary(e), flush=True)
    print("Transcript:", flush=True)
    for who, text in e.transcript:
        print(f"  {who}: {text}", flush=True)
    tunnel.stop()


if __name__ == "__main__":
    _main()
