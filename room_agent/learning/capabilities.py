"""The learning layer's capabilities, registered like any other module's (actions/core.py): teach a preference, see
what was learned, ask why, forget, stop applying something automatically, run a taught routine.

Also installs the executor hooks through which learned preferences are applied in code."""

import json
import re
import time

from room_agent import runtime as rt
from room_agent.actions import executor
from room_agent.actions.core import Capability, Group, Risk, register, register_group
from room_agent import learning
from room_agent.learning import model as um
from room_agent.learning.adaptation import PERMANENT, TEMPORARY
from room_agent.learning.privacy import looks_secret

KIND_LIST = list(um.KINDS)


class _Hints:
    """Static words, plus the trigger phrases of this user's routines ("I'm working")."""
    STATIC = re.compile(r"\b(learn|always|whenever|from now on|every time|prefer|usually|by default|forget|reset|why did you|"
                        r"what have you learned|what do you know about how|automatically|call (this|that|it|my)|when i say|"
                        r"routine|anymore|don'?t (do|ask)|stop (doing|asking))\b", re.I)

    def search(self, text):
        if self.STATIC.search(text):
            return True
        text = text.lower()
        return any(um.split_key(p["key"])[1] in text for p in learning.user_model().all()
                   if p["key"].startswith("routine:"))


def _recent():
    d = learning.learner().last_decision()
    return bool(d)


register_group(Group("learning", _Hints(), _recent, "learning your preferences",
                     "remembers how you like things done (when you say 'always...', and from your corrections over time), "
                     "can say what it learned and why it did something, forgets or stops applying a preference on request"))


# ---------------------------------------------------------------- handlers
def learn_preference(args):
    kind, value, subject = args["kind"], args["value"], str(args.get("subject") or "").strip()
    said = rt.turn_text or ""
    if looks_secret(value) or looks_secret(subject) or looks_secret(said):
        return "FAILED: that looks like a password, key or payment detail, and those are never saved as preferences."
    if TEMPORARY.search(said) and not PERMANENT.search(said):
        return ("FAILED: that sounds like a one-time instruction ('" + TEMPORARY.search(said)[0] + "'), so it wasn't saved "
                "as a lasting preference. Just do it this time; save it only if they say always / from now on.")
    if um.KINDS.get(kind, {}).get("subject") and not subject:
        return f"NEEDS: subject (what the {kind.replace('_', ' ')} preference is about). Ask in a few words."
    if kind == "routine" and isinstance(value, str) and value.strip().startswith("["):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    if kind == "monitor_alias" and str(value).lower() in ("this", "here", "this one", "this monitor", "current"):
        value = _monitor_here()
        if value is None:
            return "FAILED: couldn't tell which monitor 'this' is (no window in front)."
    if kind == "confirmation":
        from room_agent.actions import core

        cap = core.get(subject.replace(" ", "_"))
        if cap is None:
            return f"FAILED: there's no action called {subject}."
        if cap.risk == Risk.SENSITIVE and str(value).lower() == "dont_ask":
            return f"FAILED: {subject} always needs a yes first (it can't be taken back), so that can't be turned off."
    try:
        key, stored = learning.user_model().teach(kind, value, subject, because=f"you said: \"{said.strip()[:120]}\"")
    except ValueError as e:
        return f"FAILED: that preference doesn't fit ({e}). Nothing was saved."
    p = learning.store().preference(learning.user_model().user, key)
    return "OK: saved: " + learning.user_model().say(p) + "."


def _monitor_here():
    try:
        from room_agent.tools import window_control as wc

        hwnd = wc.front_window()
        m = wc._monitor_of(hwnd, wc.monitors()) if hwnd else None
        return str(m["num"]) if m else None
    except Exception:
        return None


def list_learned(args):
    m = learning.user_model()
    sure = m.all()
    guesses = [p for p in m.all(include_tentative=True) if p not in sure]
    if not sure and not guesses:
        return "OK: nothing learned about them yet. Preferences come from 'always...' instructions and repeated corrections."
    out = "OK: " + ("; ".join(m.say(p) for p in sure) if sure else "no firm preferences yet")
    if guesses:
        out += ". Still unsure (not used yet): " + "; ".join(m.say(p) for p in guesses[:4])
    return out + "."


def explain_last_action(args):
    d = learning.learner().last_decision(3600)
    from room_agent.actions.context import env

    if d:
        how = d["because"] if d["source"] == "explicit" else (
            f"I learned it ({round(d['confidence'] * 100)}% sure, from {d['evidence']} times)")
        return f"OK: I {d['what']} because of a preference: {learning.user_model().say({**d, 'auto': d.get('auto', True)})}. {how}."
    last = env.last_successful_action
    if last:
        return (f"OK: the last thing I did ({last.capability}) was simply what they asked for; no learned preference "
                "was involved.")
    return "OK: I haven't done anything on my own; there's nothing to explain."


def _target(what):
    """'that' -> the preference behind the last automatic decision; words -> the preferences matching them."""
    m = learning.user_model()
    if not what or re.fullmatch(r"\s*(that|it|this|the last one|what you just did)\s*", str(what), re.I):
        d = learning.learner().last_decision(3600)
        return [m.store.preference(m.user, d["key"])] if d else []
    return m.find(what)


def forget_preference(args):
    m = learning.user_model()
    hits = [p for p in _target(args.get("what")) if p]
    if not hits:
        return "FAILED: no learned preference matches that, so nothing was forgotten."
    if len(hits) > 3:
        return "NEEDS: which one: " + "; ".join(m.say(p) for p in hits[:5]) + ". Ask which."
    for p in hits:
        m.forget(p["key"])
    return "OK: forgot " + "; ".join(um.KINDS[um.split_key(p["key"])[0]]["say"](um.split_key(p["key"])[1], p["value"])
                                     for p in hits) + " (and the evidence behind it)."


def set_automation(args):
    m = learning.user_model()
    hits = [p for p in _target(args.get("what")) if p]
    if not hits:
        return "FAILED: no learned preference matches that."
    on = bool(args.get("enabled"))
    for p in hits:
        m.set_auto(p["key"], on)
    names = "; ".join(um.KINDS[um.split_key(p["key"])[0]]["say"](um.split_key(p["key"])[1], p["value"]) for p in hits)
    return (f"OK: will apply it automatically again: {names}." if on else
            f"OK: won't do that automatically anymore ({names}); the preference is kept, so they can still ask for it.")


def run_routine(args):
    m = learning.user_model()
    trigger = str(args.get("trigger") or "").strip().lower()
    p = m.store.preference(m.user, um.key_for("routine", trigger)) or next(
        (x for x in m.all() if x["key"].startswith("routine:") and um.split_key(x["key"])[1] in trigger), None)
    if not p:
        return f"FAILED: there's no routine for '{trigger}'. Offer to set one up."
    if not isinstance(p["value"], list):
        return f"NEEDS: what the '{um.split_key(p['key'])[1]}' routine should do ({p['value']}). Ask, then save it with learn_preference."
    plan = executor.Plan()
    results = [plan.run(s["action"], s["args"]) for s in p["value"]]
    m.store.touch(m.user, p["key"])
    done = [r for r in results if r.success]
    head = "OK" if done else "FAILED"
    return f"{head}: ran the '{um.split_key(p['key'])[1]}' routine: " + "; ".join(r.message for r in results)


# ---------------------------------------------------------------- register
register(Capability(
    name="learn_preference", group="learning", claim=["memory_save"], risk=Risk.SAFE,
    description="Save a LASTING preference or rule the user just stated: 'always put Spotify on monitor 2', 'when I say a "
                "little louder, use 5%', 'call this monitor my coding screen', 'when I say I'm working, open VS Code and "
                "Chrome', 'don't roast me when I'm working', 'keep answers short'. Never for one-time requests ('today', "
                "'for now', 'this time'): just do those.",
    examples=["always put Spotify on monitor 2", "when I say a little louder, increase it by 5%"],
    parameters={"type": "object", "properties": {
        "kind": {"type": "string", "enum": KIND_LIST, "description": "volume_step: % for louder/quieter; app_monitor: "
                 "subject app, value monitor; app_alias: subject = their word ('browser'), value = app; monitor_alias: "
                 "subject = their name ('coding screen'), value = monitor number or 'this'; routine: subject = trigger "
                 "phrase, value = JSON list of {action, args} using the available tools; response_style: short / normal / "
                 "detailed; humor: subject = when ('working' or 'always'), value = what they want; confirmation: subject = "
                 "action name, value ask / dont_ask; other kinds: free text."},
        "subject": {"type": "string"}, "value": {"type": "string"}},
        "required": ["kind", "value"]},
    execute=learn_preference))
register(Capability(name="list_learned", group="learning", changes_state=False,
                    description="What Jarvis has learned about how they like things done ('what have you learned about me?').",
                    parameters={"type": "object", "properties": {}}, execute=list_learned))
register(Capability(name="explain_last_action", group="learning", changes_state=False,
                    description="Why Jarvis did something on its own ('why did you do that?'): the preference behind it.",
                    parameters={"type": "object", "properties": {}}, execute=explain_last_action))
register(Capability(
    name="forget_preference", group="learning", claim=["memory_forget"], risk=Risk.CONFIRM, min_confidence=0.6,
    description="Forget a learned preference: 'forget my Spotify monitor preference', 'reset that preference'.",
    parameters={"type": "object", "properties": {
        "what": {"type": "string", "description": "Their words for it, or 'that' for the one just used."},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1}}, "required": ["what"]},
    execute=forget_preference))
register(Capability(
    name="set_automation", group="learning", claim=["memory_save"],
    description="Stop (or resume) applying a preference automatically: 'don't do that automatically anymore'.",
    parameters={"type": "object", "properties": {
        "what": {"type": "string", "description": "Their words for it, or 'that' for the one just used."},
        "enabled": {"type": "boolean"}}, "required": ["what", "enabled"]},
    execute=set_automation))
register(Capability(
    name="run_routine", group="learning",
    description="Run a routine they taught ('when I say I'm working, ...'): call it when they say the trigger.",
    parameters={"type": "object", "properties": {"trigger": {"type": "string"}}, "required": ["trigger"]},
    execute=run_routine))

def _context(user_text):
    from room_agent.config import USER_NAME

    learned = learning.context_lines(user_text)
    return [f"- learned about {USER_NAME} (relevant here; explicit rules beat guesses, and what they say now beats both): "
            + "; ".join(learned)] if learned else []


from room_agent.actions.core import register_context  # noqa: E402

register_context(_context, order=50)
executor.BEFORE_HOOKS.append(lambda cap, args: learning.learner().before(cap, args))
executor.AFTER_HOOKS.append(lambda cap, args, result: learning.learner().after(cap, args, result))
