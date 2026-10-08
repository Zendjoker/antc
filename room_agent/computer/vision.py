"""Understanding a screenshot with a vision-capable model (only when they asked about the screen, with permission).

Provider (VISION_PROVIDER): claude (the configured Claude model reads images; default when a Claude key is set),
openai (gpt-5 family models take images through chat completions), or off. Cost is counted in the daily budget like
every other call; if the budget is used up, nothing is sent.

The model returns JSON: a short summary, any error / dialog text it was asked to read, and the visible interactive
elements with boxes in image pixels (mapped to screen pixels by computer/screen.py). It's told never to transcribe
passwords, codes or private message contents.
"""

import base64
import json
import logging
import re

from room_agent import config

log = logging.getLogger("room-agent")
PROMPT = """You are looking at a screenshot of the user's computer ({width}x{height} pixels), window "{title}".
Their question: "{question}"

Reply with JSON only:
{{"summary": "2-3 plain sentences answering their question about what is on screen",
  "text": "any error message or dialog text they asked to read, verbatim (else empty)",
  "elements": [{{"name": "visible label", "kind": "button|link|field|tab|menu|icon|text",
                "box": [x1, y1, x2, y2]}}]}}
Rules: describe only what is visible; if something is unreadable say so. List at most 25 elements, the ones relevant to
the question first, boxes in image pixels. Never write out passwords, one-time codes, card numbers or the contents of
private messages: say "[hidden]" instead."""


def provider():
    p = config.VISION_PROVIDER
    if p in ("off", "none", ""):
        return None
    if p == "auto":
        return "claude" if config.ANTHROPIC_KEY else "openai" if config.OPENAI_KEY else None
    return p


def _parse(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {"summary": (text or "").strip()[:600], "text": "", "elements": []}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {"summary": (text or "").strip()[:600], "text": "", "elements": []}
    els = []
    for e in data.get("elements") or []:
        box = e.get("box") if isinstance(e, dict) else None
        if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            els.append({"name": str(e.get("name", ""))[:80], "kind": str(e.get("kind", ""))[:20], "box": [float(v) for v in box]})
    return {"summary": str(data.get("summary", ""))[:800], "text": str(data.get("text", ""))[:1500], "elements": els[:25]}


def _claude(shot, prompt):
    from room_agent.llm.budget import budget
    from room_agent.llm.client import client

    model = config.VISION_MODEL or config.MODEL
    r = client().messages.create(model=model, max_tokens=900, messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(shot.png).decode()}},
        {"type": "text", "text": prompt}]}])
    u = r.usage
    budget.record("claude", model, fresh_in=u.input_tokens or 0, cached_in=getattr(u, "cache_read_input_tokens", 0) or 0,
                  out=u.output_tokens or 0)
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


def _openai(shot, prompt):
    from room_agent.llm.openai_backend import create, record_openai_usage

    model = config.VISION_MODEL or config.OPENAI_MODEL
    url = "data:image/png;base64," + base64.b64encode(shot.png).decode()
    r = create(model, [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                                    {"type": "text", "text": prompt}]}])
    record_openai_usage(r.usage, model)
    return r.choices[0].message.content or ""


def analyze(shot, question):
    """-> {"summary", "text", "elements"} or raises RuntimeError with what to tell them."""
    from room_agent.llm.budget import budget

    p = provider()
    if p is None:
        raise RuntimeError("screen understanding is switched off (VISION_PROVIDER)")
    if budget.exceeded():
        raise RuntimeError("today's API budget is used up, so no screenshot was sent")
    prompt = PROMPT.format(width=shot.width, height=shot.height, title=shot.title[:80], question=str(question)[:300])
    try:
        text = _claude(shot, prompt) if p == "claude" else _openai(shot, prompt)
    except Exception as e:
        log.warning("vision call failed: %s", e)
        raise RuntimeError(f"the vision model didn't answer ({e.__class__.__name__})") from e
    return _parse(text)
