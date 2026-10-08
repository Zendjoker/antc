"""Outreach material for a lead: an email draft, call talking points and a one-page proposal. Nothing is ever sent.

    - drafts are honest: they say who you are, that the demo is an unsolicited design preview (not their site, not live),
      mention only observations backed by evidence, and include an opt-out line and your postal address (CAN-SPAM)
    - prices are never invented: the proposal leaves them for you to fill in
    - "send" doesn't exist here. An approved email becomes a Gmail DRAFT in your account (read back to confirm); you send
      it yourself from Gmail. Calls are talking points for you; Jarvis never phones a business.
    - with MISSION_LLM_COPY=1 a light model may smooth the wording; its text is then checked against the facts (every
      phone number / address / link / number in it must come from the lead) and the template is used if it fails
"""

import logging
import re
from pathlib import Path

from room_agent import config
from room_agent.missions import llm, meter

log = logging.getLogger("room-agent")


def sender_problems():
    out = []
    if not config.MISSION_SENDER_NAME:
        out.append("your name isn't set (MISSION_SENDER_NAME)")
    if not (config.MISSION_SENDER_EMAIL or config.MISSION_SENDER_PHONE):
        out.append("your contact email or phone isn't set (MISSION_SENDER_EMAIL / MISSION_SENDER_PHONE)")
    if not config.MISSION_SENDER_ADDRESS:
        out.append("your postal address isn't set (MISSION_SENDER_ADDRESS): commercial email must include one (CAN-SPAM)")
    return out


def observations(lead):
    """What we can honestly say about their web presence, from the stored evidence."""
    st = lead.get("website_status")
    a = lead.get("analysis") or {}
    if st == "none_found":
        return [f"I couldn't find a website for {lead['name']} when I searched online"]
    if st == "social_only":
        return [f"I could only find {lead['name']} on social media, not on a website of its own"]
    if st == "directory_only":
        return [f"I could only find {lead['name']} on listing sites, not on a website of its own"]
    if st == "broken":
        return [f"the website listed for {lead['name']} ({lead.get('website', '')}) didn't load when I tried it"]
    out = []
    names = {c["name"] for c in a.get("checks", []) if not c["ok"]}
    if "mobile_viewport" in names:
        out.append("your website doesn't seem to adjust to phone screens")
    if "https" in names:
        out.append("your website isn't served over a secure (https) connection, so browsers may label it 'not secure'")
    if "copyright_recent" in names:
        out.append("your website looks like it hasn't been updated in a few years")
    if "phone_on_page" in names:
        out.append("your phone number isn't on your website's home page")
    if "menu_or_hours" in names:
        out.append("your home page doesn't show your menu or opening hours")
    return out[:3]


def _signature():
    lines = [x for x in (config.MISSION_SENDER_NAME, config.MISSION_SENDER_BUSINESS, config.MISSION_SENDER_PHONE,
                         config.MISSION_SENDER_EMAIL, config.MISSION_SENDER_ADDRESS) if x]
    return "\n".join(lines) or "[your name, business, phone, email and postal address]"


def email_draft(lead, has_demo):
    obs = observations(lead)
    name = lead["name"]
    first = config.MISSION_SENDER_NAME.split()[0] if config.MISSION_SENDER_NAME else "[your name]"
    subject = f"A website idea for {name}"
    body = [f"Hello {name} team,", "",
            f"My name is {first}{' from ' + config.MISSION_SENDER_BUSINESS if config.MISSION_SENDER_BUSINESS else ''}. "
            f"I build websites for local businesses{' in ' + lead['city'] if lead.get('city') else ''}."]
    if obs:
        body += ["", "While looking at local businesses, I noticed that " + obs[0] + "."
                 + (" Also, " + "; ".join(obs[1:]) + "." if len(obs) > 1 else "")]
    if has_demo:
        body += ["", f"I put together a free design preview of what a simple website for {name} could look like, using "
                     "the public details I could find (address, phone, hours). It isn't live anywhere and isn't your "
                     "official site; the menu and story sections are placeholders for your own content. I'd be happy to "
                     "show it to you or send screenshots."]
    body += ["", "If it's of interest, I'd be glad to talk for ten minutes at a time that suits you. No obligation.", "",
             "If you'd rather not hear from me, just reply \"no thanks\" and I won't contact you again.", "", "Best regards,",
             _signature()]
    return subject, "\n".join(body)


def talking_points(lead, has_demo):
    obs = observations(lead)
    ex = lead.get("extra") or {}
    lines = [f"# Call notes: {lead['name']}", "",
             "Jarvis doesn't place calls. These are notes for you if you choose to call.", "",
             "## Facts (from public sources)",
             f"- Phone: {lead.get('phone') or 'unknown'}", f"- Address: {lead.get('address') or 'unknown'}",
             f"- Website status: {lead.get('website_status')} ({lead.get('website') or 'none on record'})"]
    if ex.get("opening_hours"):
        lines.append(f"- Hours listed: {ex['opening_hours']} (call outside busy hours)")
    lines += ["", "## Opening", f"- Introduce yourself and that you build websites for local businesses.",
              "- Ask for the owner or manager; offer to call back at a better time."]
    if obs:
        lines += ["", "## What you noticed (evidence-based)", *(f"- {o}" for o in obs)]
    if has_demo:
        lines += ["", "## The preview", "- Say it's an unsolicited design preview made from public info, not live, not their site.",
                  "- Placeholders (menu, story) are for their own content; nothing was invented."]
    lines += ["", "## Questions", "- How do customers find you today?", "- Do you take orders / bookings online?",
              "- Who would update the site?", "", "## Don't",
              "- Don't claim the preview is their site or that it's live.", "- Don't quote reviews or ratings.",
              "- If they say no, thank them, mark the lead 'not interested' and don't contact them again."]
    if (lead.get("analysis") or {}).get("evidence"):
        lines += ["", "## Evidence", *(f"- {e}" for e in lead["analysis"]["evidence"][:5])]
    return "\n".join(lines) + "\n"


def proposal(lead, has_demo):
    return "\n".join([
        f"# Website proposal for {lead['name']}", "",
        "## What you get",
        "- A fast, mobile-friendly website with your menu / services, hours, location and contact details",
        "- Click-to-call and directions buttons", "- Search-engine basics (titles, descriptions, Google Business link)",
        "- Content written with you: nothing published without your approval", "",
        "## Options (fill in your prices)",
        "| Package | Includes | Price |", "|---|---|---|",
        "| Starter | one-page site, hosting setup | [your price] |",
        "| Standard | multi-page site, menu / services, basic SEO | [your price] |",
        "| Care plan | monthly updates and hosting | [your price] / month |", "",
        "## Timeline", "- [your estimate] after receiving your content and photos", "",
        "## Next step", "- A short call to agree on content and package." +
        (" The design preview can be the starting point." if has_demo else ""), "",
        "_Prepared by " + (config.MISSION_SENDER_NAME or "[your name]") + ". Prices and timeline to be confirmed._", ""])


def _facts_ok(text, lead):
    """Every phone number, link and figure in model-written text must come from the lead or the sender's details."""
    known = " ".join(str(lead.get(k) or "") for k in ("name", "phone", "address", "website", "email", "city")) + " " + \
        " ".join((config.MISSION_SENDER_NAME, config.MISSION_SENDER_BUSINESS, config.MISSION_SENDER_PHONE,
                  config.MISSION_SENDER_EMAIL, config.MISSION_SENDER_ADDRESS))
    known_digits = re.sub(r"\D", " ", known)
    for n in re.findall(r"\d{3,}", re.sub(r"[()\-\s.]", "", text)):
        if n not in re.sub(r"\s", "", known_digits):
            return False
    for u in re.findall(r"https?://\S+|www\.\S+|\S+@\S+\.\w+", text):
        if u.rstrip(".,)") not in known:
            return False
    return not re.search(r"\$\s?\d|\breviews?\b|\bstars?\b|award", text, re.I)


def polish(lead, subject, body):
    """Optional: a light model rewrites the wording (same facts). Falls back to the template if anything is off."""
    if not config.MISSION_LLM_COPY:
        return subject, body, ""
    prompt = ("Rewrite this cold email to be warm, short and natural. Keep every fact, the opt-out line and the signature "
              "exactly; add no new facts, numbers, links, prices, reviews or promises. Output the email body only.\n\n" + body)
    try:
        text = llm.complete(prompt, tier="light", max_tokens=500, what="outreach wording").strip()
    except (llm.ModelUnavailable, meter.BudgetExceeded) as e:
        return subject, body, f"template used ({e.__class__.__name__})"
    except Exception as e:  # noqa: BLE001
        return subject, body, f"template used (model call failed: {e.__class__.__name__})"
    if not text or "no thanks" not in text.lower() or not _facts_ok(text, lead):
        return subject, body, "template used (the model's version added or dropped facts)"
    return subject, text, "wording polished by a model, facts checked"


def prepare(lead, mission_id, workspace, has_demo):
    """Write the drafts to the mission folder and the database. -> (dict of paths / ids, [problems])"""
    from room_agent.missions.store import store
    from room_agent.missions.sitegen import slug

    s = store()
    folder = Path(workspace) / "outreach" / slug(lead["name"], lead["id"])
    folder.mkdir(parents=True, exist_ok=True)
    problems = sender_problems()
    subject, body = email_draft(lead, has_demo)
    subject, body, note = polish(lead, subject, body)
    recipient = lead.get("email") or ""
    if not recipient:
        problems.append("no public email address was found for this business (call notes only)")
    (folder / "email.txt").write_text(f"To: {recipient or '[no public email found]'}\nSubject: {subject}\n\n{body}\n",
                                      encoding="utf-8")
    (folder / "call-notes.md").write_text(talking_points(lead, has_demo), encoding="utf-8")
    (folder / "proposal.md").write_text(proposal(lead, has_demo), encoding="utf-8")
    existing = [o for o in s.outreach(lead_id=lead["id"]) if o["mission_id"] == mission_id and o["kind"] == "email"]
    if existing:
        oid = existing[-1]["id"]
        if existing[-1]["status"] == "draft":
            s.update_outreach(oid, subject=subject, body=body, recipient=recipient, problems=problems)
    else:
        oid = s.add_outreach(lead["id"], mission_id, "email", body, subject, recipient, problems)
    approval = None
    if recipient:
        approval = s.add_approval(mission_id, lead["id"], "gmail_draft",
                                  f"Create a Gmail draft (not sent) to {lead['name']} <{recipient}>: \"{subject}\"",
                                  {"outreach_id": oid})
    return {"folder": str(folder), "outreach_id": oid, "approval_id": approval, "note": note}, problems


def execute_approval(approval):
    """Carry out an approved action. Only 'gmail_draft' exists: it creates a draft in your Gmail, never sends."""
    from room_agent.missions.store import store

    s = store()
    if approval["action"] != "gmail_draft":
        return False, f"unknown action '{approval['action']}'"
    o = next((x for x in s.outreach(approval["mission_id"]) if x["id"] == approval["payload"].get("outreach_id")), None)
    if o is None:
        return False, "the draft it refers to is gone"
    if o["status"] != "draft":
        return False, f"already done ({o['status']})"
    blocking = [p for p in (o.get("problems") or []) if "isn't set" in p]
    if blocking:
        return False, "fix these first: " + "; ".join(blocking)
    if not o["recipient"]:
        return False, "no recipient"
    try:
        from room_agent.integrations import provider
        from room_agent.integrations.google.gmail import GmailService

        g = provider("google")
        if not g.can("gmail", "compose"):
            return False, "Gmail isn't connected with permission to write drafts"
        draft = GmailService(g).create_draft(o["recipient"], o["subject"], o["body"])
    except Exception as e:  # noqa: BLE001
        return False, f"Gmail draft not created ({e.__class__.__name__}: {str(e)[:150]})"
    s.update_outreach(o["id"], status="gmail_draft", external_id=str(draft.get("id", "")))
    lead = s.lead(o["lead_id"])
    if lead and lead["contact_status"] == "not contacted":
        s.update_lead(lead["id"], contact_status="draft in Gmail (not sent)")
    return True, f"Gmail draft created (id {draft.get('id', '?')}); it is NOT sent: open Gmail to review and send it yourself"
