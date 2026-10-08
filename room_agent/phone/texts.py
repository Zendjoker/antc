"""Texts to your own phone, and the people worth a call (VIPs), managed by voice.

    text_me      an SMS to MY_PHONE only (never anyone else), at most SMS_DAILY_LIMIT a day (each costs a little).
                 "Send that link to my phone" adds the page that's open in the browser (or a research source).
    VIPs         "always call me if Sarah emails": kept in settings.json, added to VIP_SENDERS from .env; the phone
                 watcher (phone/watcher.py) calls you about their unread emails while you drive.
"""

import datetime
import logging
import re

from room_agent import config

log = logging.getLogger("room-agent")


def _settings():
    from room_agent.audio import voices

    return voices


def vips():
    """Everyone whose email is worth a call: .env's VIP_SENDERS plus the ones added by voice (lowercase)."""
    added = _settings().saved("vip_senders", []) or []
    return sorted({v.lower() for v in config.VIP_SENDERS} | {str(v).lower() for v in added if str(v).strip()})


def add_vip(who):
    who = " ".join(str(who or "").split()).strip(" .").lower()
    if len(who) < 2:
        return "NEEDS: who (their name or email address)."
    current = _settings().saved("vip_senders", []) or []
    if who in vips():
        return f"OK: nothing needed: {who} is already someone I call you about."
    _settings().save_setting("vip_senders", current + [who])
    note = "" if config.PHONE_MODE else " (Phone mode is off right now, so calls start once it's set up: see phone.md.)"
    return f"OK: added {who}: while you're driving I'll call you about their unread emails.{note}"


def remove_vip(who):
    who = str(who or "").strip().lower()
    current = _settings().saved("vip_senders", []) or []
    keep = [v for v in current if who not in v.lower()]
    if len(keep) == len(current):
        if any(who in v for v in config.VIP_SENDERS):
            return f"FAILED: {who} is in VIP_SENDERS in .env; take them out there (I can't change .env by voice)."
        return f"FAILED: {who} isn't on the list. Nothing changed."
    _settings().save_setting("vip_senders", keep)
    return f"OK: removed {who}: no more calls about their emails."


def list_vips():
    v = vips()
    return "OK: I call you (while driving) about emails from: " + ", ".join(v) + "." if v else \
        "OK: nobody's on the call-me list yet (only emails Gmail marks important)."


def _today_count():
    s = _settings().saved("sms_count", {}) or {}
    return s.get("n", 0) if s.get("day") == datetime.date.today().isoformat() else 0


def _count():
    _settings().save_setting("sms_count", {"day": datetime.date.today().isoformat(), "n": _today_count() + 1})


def _client():
    from room_agent.phone.twilio import Twilio

    return Twilio(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)


def text_me(message, attach=""):
    """-> tool result. `attach`: 'page' (the open browser tab) or 'source N' (from the last research)."""
    if not (config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_NUMBER and config.MY_PHONE):
        return ("UNAVAILABLE: texting needs the phone line set up (Twilio account, Jarvis's number and MY_PHONE in .env: "
                "python main.py --setup-phone).")
    body = " ".join(str(message or "").split())
    link = _link(attach)
    if attach and not link:
        return f"FAILED: there's no {attach} to send (no open page / research source I can see). Nothing was sent."
    if link:
        body = (body + " " + link).strip()
    if not body:
        return "NEEDS: what to text them."
    if _today_count() >= config.SMS_DAILY_LIMIT:
        return f"FAILED: already sent {config.SMS_DAILY_LIMIT} texts today (the daily limit, SMS_DAILY_LIMIT). Nothing sent."
    try:
        sid = _client().sms(config.MY_PHONE, config.TWILIO_NUMBER, "Jarvis: " + body)
    except Exception as e:
        log.info("text_me failed: %s", e)
        return (f"FAILED: Twilio didn't accept the text ({e}). Nothing was sent. (US numbers may need the Twilio number "
                "registered for texting: A2P 10DLC.)")
    _count()
    return f"OK: texted it to your phone ({len(body)} characters{', with the link' if link else ''}). Twilio id ends {sid[-4:]}."


def _link(attach):
    a = str(attach or "").lower().strip()
    if not a:
        return ""
    if a in ("page", "this page", "link", "that link", "this link", "the page"):
        from room_agent.computer.context import desk

        p = desk.current_page()
        if p and p.get("url"):
            u = p["url"]
            return u if re.match(r"^https?://", u) else "https://" + u
        return ""
    m = re.search(r"(\d+)", a)
    if "source" in a and m:
        from room_agent.computer.context import desk

        r = desk.research
        s = next((s for s in (r.sources if r else []) if s.n == int(m.group(1))), None)
        return s.url if s else ""
    return ""
