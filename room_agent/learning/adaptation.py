"""Learning from what happens: structured interaction records, implicit signals, corrections, and applying what was
learned (through the action executor's hooks, so no feature had to change).

Signals are EVIDENCE, not truth: each adds a weight for (or against) a value, and the UserModel turns the evidence into
a confidence. Only successful, verified actions count; a one-time instruction ("today", "for now") never counts.
"""

import logging
import re
import time

from room_agent import runtime as rt
from room_agent.learning import model as um
from room_agent.learning.privacy import redact

log = logging.getLogger("room-agent")

# signal names (what was observed, not how the user felt)
EXPLICIT_POSITIVE, EXPLICIT_NEGATIVE, CORRECTION = "EXPLICIT_POSITIVE", "EXPLICIT_NEGATIVE", "CORRECTION"
REPEATED_REQUEST, ACTION_REVERSED, ACTION_ACCEPTED = "REPEATED_REQUEST", "ACTION_REVERSED", "ACTION_ACCEPTED"
PLAN_COMPLETED, PLAN_FAILED, USER_INTERRUPTED = "PLAN_COMPLETED", "PLAN_FAILED", "USER_INTERRUPTED_RESPONSE"
WEIGHT = {CORRECTION: 1.5, ACTION_ACCEPTED: 0.5, EXPLICIT_POSITIVE: 0.5, EXPLICIT_NEGATIVE: -1.0, ACTION_REVERSED: -2.0}

TEMPORARY = re.compile(r"\b(today|tonight|for now|right now|this time|just this once|once|for the moment|temporarily|"
                       r"for this session|for a bit|for a while)\b", re.I)
PERMANENT = re.compile(r"\b(always|from now on|every time|whenever|by default|in general|usually|never|any ?time|"
                       r"when i say|call (this|that|it|my)|going forward)\b", re.I)
CORRECTING = re.compile(r"^\W*(no\b|nope|nah|not that|wrong|i meant|i mean|actually|that'?s not|not the|too (much|loud|"
                        r"quiet|far|little|high|low)|not (enough|that much)|way too|a bit less|a bit more|less|more)", re.I)
POSITIVE = re.compile(r"^\W*(thanks|thank you|perfect|great|awesome|nice|good job|exactly|that'?s (it|right|perfect))\b", re.I)
NEGATIVE = re.compile(r"\b(that'?s wrong|not what i (asked|wanted|said)|stop doing that|i didn'?t ask|ugh|annoying)\b", re.I)
COMMAND = re.compile(r"^\W*(hey jarvis,?\s*)?((can|could|would) you |please |just |go )*(open|launch|start|run|bring up|"
                     r"pull up|fire up|switch to|go to|show me|put on|play)\s+(up\s+)?(my|the|a|an)?\s*", re.I)
ACTIONS_THAT_COUNT = {"volume_up", "volume_down", "set_volume", "open_app", "close_app", "move_window_to_monitor",
                      "focus_app", "focus_window", "maximize_window", "minimize_window", "restore_window", "undo_last_action"}


class Learner:
    def __init__(self, model_for, store, telemetry=True):
        self.model_for = model_for  # () -> the current user's UserModel
        self.store = store
        self.telemetry = telemetry
        self.last = None            # the previous meaningful interaction (in memory, for correction detection)
        self.decisions = []         # preferences applied in code, newest last (for "why did you do that?")

    @property
    def model(self):
        return self.model_for()

    # ---------------------------------------------------------------- applying preferences (executor hooks)
    def before(self, cap, args):
        """Fill in what the user's preferences decide, unless the request itself says otherwise."""
        m, name = self.model, cap.name
        out = dict(args)
        for field in ("app_name", "app"):
            said = str(out.get(field) or "").strip()
            term = COMMAND.sub("", said.lower()).strip(" .!?") or said.lower()
            if said and term:
                d = m.resolve("app_alias", term)
                if d and str(d["value"]).lower() != said.lower():
                    out[field] = d["value"]
                    self._decided(d, f"{name}: '{said}' -> {d['value']}")
        if name == "move_window_to_monitor" and out.get("monitor"):
            term = re.sub(r"\b(my|the|on|to)\b", " ", str(out["monitor"]).lower()).strip()
            d = m.resolve("monitor_alias", re.sub(r"\s+", " ", term))
            if d:
                out["monitor"] = str(d["value"])
                self._decided(d, f"'{args['monitor']}' -> monitor {d['value']}")
        if name in ("volume_up", "volume_down") and not out.get("amount"):
            d = m.resolve("volume_step")
            if d:
                out["amount"] = int(d["value"])
                self._decided(d, f"{name} by {d['value']}%")
        return out

    def after(self, cap, args, result):
        """After an action: things the preferences say should follow (an app's usual monitor). Returns extra text
        for the tool result, or None."""
        if cap.name != "open_app" or not result.success or not result.subject:
            return None
        if re.search(r"\b(monitor|screen|display)\b", rt.turn_text or "", re.I):
            return None  # they're saying where it goes this time: the current request wins
        d = self.model.automatic("app_monitor", result.subject)
        if not d:
            return None
        from room_agent.actions.executor import execute

        moved = execute("move_window_to_monitor", {"app": result.subject, "monitor": str(d["value"])})
        if not moved.success:
            return f"(Tried to put it on monitor {d['value']}, their usual, but: {moved.message})"
        self._decided(d, f"put {result.subject} on monitor {d['value']}", action=moved)
        return f"Also put it on monitor {d['value']}, as they prefer ({d['because'] or d['source']})."

    def _decided(self, decision, what, action=None):
        self.store.touch(self.model.user, decision["key"])
        self.decisions = (self.decisions + [{**decision, "what": what, "at": time.time(),
                                             "action": action.capability if action else None}])[-20:]

    def last_decision(self, within=900):
        d = self.decisions[-1] if self.decisions else None
        return d if d and time.time() - d["at"] < within else None

    # ---------------------------------------------------------------- after each turn
    def on_turn_end(self, request, plan, latency_s=None):
        """Record the turn (if it did or corrected something) and turn what happened into evidence."""
        steps = [s for s in (plan.steps if plan else [])]
        text = str(request or "")
        signals = []
        correcting = bool(CORRECTING.search(text))
        prev = self.last if self.last and time.time() - self.last["at"] < 180 else None
        if not steps and not (correcting or POSITIVE.search(text) or NEGATIVE.search(text)):
            if prev and prev.get("steps"):  # they moved on without correcting it: weak evidence it was right
                self._accepted(prev, None)
                self.last = {**prev, "steps": []}  # (counted once)
            return None
        m = self.model
        record = {"at": time.time(), "request": redact(text), "context": self._context(), "intent": [s.capability for s in steps],
                  "entities": self._entities(steps), "plan": [self._step(s) for s in steps],
                  "outcome": _outcome(steps), "latency_ms": int(latency_s * 1000) if latency_s else None}
        if steps:
            signals.append(PLAN_COMPLETED if record["outcome"] == "completed" else PLAN_FAILED if record["outcome"] == "failed"
                           else "PLAN_PARTIAL")
        if getattr(rt, "turn_interrupted", False):
            signals.append(USER_INTERRUPTED)
        if prev and _norm(text) == _norm(prev["request_raw"]):
            signals.append(REPEATED_REQUEST)
        if prev and correcting:
            signals.append(CORRECTION)
            record["correction_of"] = prev.get("id")
            record["correction"] = {"original_request": prev["request"], "original_actions": prev["plan"],
                                    "corrected_actions": record["plan"]}
        if prev and POSITIVE.search(text):
            signals.append(EXPLICIT_POSITIVE)
        if prev and NEGATIVE.search(text):
            signals.append(EXPLICIT_NEGATIVE)
        record["signals"] = signals
        rid = self.store.add_interaction(m.user, record) if self.telemetry else None
        self._learn(text, steps, prev, correcting, signals, rid)
        self.last = {**record, "id": rid, "request_raw": text, "steps": steps}
        return record

    def _learn(self, text, steps, prev, correcting, signals, rid):
        m = self.model
        ok = [s for s in steps if s.success and s.verified]
        one_time = bool(TEMPORARY.search(text)) and not PERMANENT.search(text)
        # 1. relative volume: "a little louder" (+10) -> "too much, down 5" => they wanted 5
        if prev:
            before = _volume_step(prev["steps"])
            now = _volume_delta(ok)
            if before and now is not None and correcting:
                step, direction = before
                wanted = step - abs(now) if (now > 0) != (direction > 0) else step + abs(now)
                if 1 <= wanted <= 50:
                    m.observe("volume_step", "", wanted, CORRECTION, WEIGHT[CORRECTION], rid,
                              note=f"{step}% was corrected by {now:+d}%")
            elif not correcting and now is None:
                self._accepted(prev, rid)
        # 2. the wrong app: "open my browser" -> Edge; "no, I mean Chrome" -> Chrome  => 'browser' means Chrome
        if prev and correcting:
            opened = [s for s in prev["steps"] if s.capability == "open_app" and s.success]
            fixed = [s for s in ok if s.capability == "open_app"]
            term = _term(prev["request_raw"])
            if opened and fixed and term and fixed[-1].subject and fixed[-1].subject != opened[-1].subject:
                m.observe("app_alias", term, fixed[-1].subject, CORRECTION, WEIGHT[CORRECTION], rid,
                          note=f"'{term}' opened {opened[-1].subject}; they corrected it to {fixed[-1].subject}")
                m.observe("app_alias", term, opened[-1].subject, EXPLICIT_NEGATIVE, WEIGHT[EXPLICIT_NEGATIVE], rid)
        # 3. where an app goes: opening it and moving it (not "for now") is weak evidence for its monitor
        if not one_time:
            for s in ok:
                if s.capability == "move_window_to_monitor" and s.subject:
                    mon = (s.state_after or {}).get("monitor") or {}
                    auto = self.last_decision(60)
                    if auto and auto.get("action") == "move_window_to_monitor" and auto["key"] == um.key_for("app_monitor", s.subject):
                        # they moved it somewhere else right after Jarvis put it on its "usual" monitor
                        if str(mon.get("num")) != str(auto["value"]):
                            m.observe("app_monitor", s.subject, auto["value"], ACTION_REVERSED, WEIGHT[ACTION_REVERSED], rid)
                    if mon.get("num"):
                        m.observe("app_monitor", s.subject, str(mon["num"]), ACTION_ACCEPTED, WEIGHT[ACTION_ACCEPTED], rid,
                                  note="they put it there")
        # 4. undoing what a learned preference did counts against it
        for s in ok:
            if s.capability == "undo_last_action":
                auto = self.last_decision(300)
                if auto and auto.get("source") == "inferred":
                    kind, subject = um.split_key(auto["key"])
                    m.observe(kind, subject, auto["value"], ACTION_REVERSED, WEIGHT[ACTION_REVERSED], rid)
        # 5. "no / that's wrong" right after Jarvis applied an inferred preference: evidence against it
        if prev and (EXPLICIT_NEGATIVE in signals) and self.last_decision(120):
            d = self.last_decision(120)
            if d.get("source") == "inferred":
                kind, subject = um.split_key(d["key"])
                m.observe(kind, subject, d["value"], EXPLICIT_NEGATIVE, WEIGHT[EXPLICIT_NEGATIVE], rid)

    def _accepted(self, prev, rid):
        """The previous turn's relative volume step wasn't corrected (and they didn't give a number): weak evidence."""
        before = _volume_step(prev["steps"])
        if before and not re.search(r"\d", prev["request_raw"]):
            self.model.observe("volume_step", "", before[0], ACTION_ACCEPTED, WEIGHT[ACTION_ACCEPTED], rid,
                               note=f"a {before[0]}% step wasn't corrected")

    # ---------------------------------------------------------------- record helpers
    @staticmethod
    def _context():
        from room_agent.actions.context import env

        return {"active_app": env.active_app, "previous_app": env.previous_app,
                "active_monitor": (env.active_monitor or {}).get("num") if env.active_monitor else None,
                "media": (env.current_media or {}).get("status") if env.current_media else None,
                "ringing": bool(rt.ringing)}

    @staticmethod
    def _entities(steps):
        apps_, numbers = set(), set()
        for s in steps:
            if s.subject:
                apps_.add(s.subject)
            for k, v in (s.parameters or {}).items():
                if k in ("percent", "amount", "monitor", "seconds"):
                    numbers.add(f"{k}={v}")
        return {"apps": sorted(apps_), "values": sorted(numbers)}

    @staticmethod
    def _step(s):
        from room_agent.actions import core

        if getattr(core.get(s.capability), "private", False):  # email / calendar content never goes into telemetry
            return {"capability": s.capability, "params": sorted(k for k in (s.parameters or {}) if k != "confidence"),
                    "success": s.success, "verified": s.verified, "error_code": s.error_code, "message": "[personal data]"}
        return {"capability": s.capability, "params": {k: v for k, v in (s.parameters or {}).items() if k != "confidence"},
                "success": s.success, "verified": s.verified, "error_code": s.error_code, "message": redact(s.message)[:200],
                "before": _small(s.state_before), "after": _small(s.state_after)}


def _small(state):
    if not isinstance(state, dict):
        return None
    return {k: v for k, v in state.items() if k not in ("hwnd", "rect")}


def _outcome(steps):
    if not steps:
        return "no_action"
    good = sum(1 for s in steps if s.success)
    return "completed" if good == len(steps) else "failed" if good == 0 else "partial"


def _norm(text):
    return re.sub(r"[^a-z0-9 ]", "", str(text).lower()).strip()


def _term(request):
    """What they called the thing they wanted opened ("open my browser" -> "browser")."""
    t = COMMAND.sub("", str(request or "").lower()).strip(" .!?")
    t = re.sub(r"\b(please|for me|now|real quick)\b", "", t).strip(" ,.")
    return t if t and len(t.split()) <= 3 else None


def _volume_step(steps):
    """(step, direction) of the last relative volume change in these steps, or None."""
    for s in reversed(steps):
        if s.capability in ("volume_up", "volume_down") and s.success and s.state_before and s.state_after:
            delta = s.state_after["volume"] - s.state_before["volume"]
            if delta:
                return abs(delta), (1 if delta > 0 else -1)
    return None


def _volume_delta(steps):
    for s in reversed(steps):
        if s.capability in ("volume_up", "volume_down", "set_volume") and s.state_before and s.state_after:
            return s.state_after["volume"] - s.state_before["volume"]
    return None
