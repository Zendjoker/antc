"""How much thinking a request deserves, decided in code from the words (no model call):

    REFLEX       a fully specified simple command a capability declared a reflex pattern for: no model call at all
    FAST         ordinary requests and questions: the normal model, as before
    DELIBERATE   goals with state, dependencies or investigation ("get my workspace ready", "why is X failing",
                 "is my backend running?", several actions in one request): a tracked Goal + the observe-first protocol
    DEEP         hard investigations or explicit "think hard": the stronger configured model (llm/router.py)

The level only says how much reasoning to spend. WHICH model handles it is the router's business (config), so this
works the same with OpenAI, Claude, a local model or anything added later.
"""

import re

from room_agent.config import LLM_SMART_TRIGGERS

REFLEX, FAST, DELIBERATE, DEEP = "REFLEX", "FAST", "DELIBERATE", "DEEP"

DEEP_CUES = re.compile(r"\b(investigate|deep dive|root cause|research|in depth|thorough(?:ly)?|step by step|compare .{3,60}\b"
                       r"(?:and|vs\.?|versus|with)\b)", re.I)
DELIBERATE_CUES = re.compile(
    r"\b(get (?:\w+ ){0,4}ready|set (?:me |it |things |everything |\w+ )?up\b|prepare|figure out|troubleshoot|diagnose|"
    r"why (?:is|isn'?t|does|doesn'?t|did|won'?t|can'?t|am|are)\b|what'?s wrong|not working|keeps? (?:crashing|failing|freezing)|"
    r"lagg?(?:ing|y)|so slow|make sure|fix (?:it|this|my|the)\b|is (?:my|the) [\w .'-]{1,30} (?:running|up|working|on|open)\b|"
    r"tell me when|let me know when|watch (?:this|the|for|my)\b|restore|get back to|restart|reboot|not responding|"
    r"unresponsive|stopped working|(?:is|it'?s|keeps?) (?:broken|down|stuck|hanging|frozen))", re.I)
ACTION_VERBS = re.compile(r"\b(open|close|start|launch|move|put|play|pause|send|reply|draft|schedule|set|turn|mute|minimi[sz]e|"
                          r"maximi[sz]e|find|search|check|read|delete|remind)\b", re.I)
WAITING_CUES = re.compile(r"\b(tell me when|let me know when|let me know if|ping me when|watch (?:this|the|for|my))\b", re.I)


def classify(text, reflex_hit=False, goal_open=False):
    """-> (level, why)."""
    t = " ".join(str(text or "").lower().split())
    if reflex_hit:
        return REFLEX, "a simple command a capability handles directly"
    if any(trigger in t for trigger in LLM_SMART_TRIGGERS) or (DEEP_CUES.search(t) and len(t.split()) >= 6):
        return DEEP, "asked for careful thinking / an investigation"
    if DELIBERATE_CUES.search(t):
        return DELIBERATE, "a goal, a state to check or a problem to work out"
    verbs = ACTION_VERBS.findall(t)
    if len(set(v.lower() for v in verbs)) >= 2 and re.search(r"\b(and|then|after that|also)\b", t):
        return DELIBERATE, "several actions that may depend on each other"
    if goal_open:
        return DELIBERATE, "continues the goal in progress"
    return FAST, "ordinary request"
