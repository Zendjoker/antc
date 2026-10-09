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
import time
from pathlib import Path

from room_agent import config
from room_agent.missions import llm, meter, runctx

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


_rejected = {}  # mission id -> how many model rewrites failed the fact check (llm.tier_for escalation)


def polish(lead, subject, body, mission_id=""):
    """Optional: a light model rewrites the wording (same facts). Falls back to the template if anything is off.
    After MISSION_ESCALATE_AFTER rewrites in a mission failed the fact check, the strong tier is used (if configured)."""
    if not config.MISSION_LLM_COPY:
        return subject, body, ""
    tier = llm.tier_for("wording", _rejected.get(mission_id, 0))
    prompt = ("Rewrite this cold email to be warm, short and natural. Keep every fact, the opt-out line and the signature "
              "exactly; add no new facts, numbers, links, prices, reviews or promises. Output the email body only.\n\n" + body)
    try:
        text = llm.complete(prompt, tier=tier, max_tokens=500, what="outreach wording").strip()
    except (llm.ModelUnavailable, meter.BudgetExceeded) as e:
        return subject, body, f"template used ({e.__class__.__name__})"
    except Exception as e:  # noqa: BLE001
        return subject, body, f"template used (model call failed: {e.__class__.__name__})"
    if not text or "no thanks" not in text.lower() or not _facts_ok(text, lead):
        _rejected[mission_id] = _rejected.get(mission_id, 0) + 1
        return subject, body, "template used (the model's version added or dropped facts)"
    return subject, text, "wording polished by a model, facts checked"


def _write(path, text):
    """Atomic: a crash leaves the old file or the new one, never half of one."""
    runctx.check()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def prepare(lead, mission_id, workspace, has_demo, s=None):
    """Write the drafts to the mission folder and the database. Safe to repeat (a resumed step): it updates the same
    draft and never queues a second approval for it. -> (dict of paths / ids, [problems])"""
    from room_agent.missions.sitegen import slug
    from room_agent.missions.store import store

    s = s or store()
    folder = Path(workspace) / "outreach" / slug(lead["name"], lead["id"])
    folder.mkdir(parents=True, exist_ok=True)
    problems = sender_problems()
    subject, body = email_draft(lead, has_demo)
    subject, body, note = polish(lead, subject, body, mission_id)
    from room_agent.missions.websites import valid_email

    recipient = valid_email(lead.get("email"))
    if lead.get("email") and not recipient:
        problems.append("the email address found isn't one valid address, so it isn't used (call notes only)")
    elif not recipient:
        problems.append("no public email address was found for this business (call notes only)")
    _write(folder / "email.txt", f"To: {recipient or '[no public email found]'}\nSubject: {subject}\n\n{body}\n")
    _write(folder / "call-notes.md", talking_points(lead, has_demo))
    _write(folder / "proposal.md", proposal(lead, has_demo))
    existing = [o for o in s.outreach(lead_id=lead["id"]) if o["mission_id"] == mission_id and o["kind"] == "email"]
    if existing:
        oid = existing[-1]["id"]
        if existing[-1]["status"] == "draft":  # (never rewrite one that's already in Gmail or being sent there)
            s.update_outreach(oid, subject=subject, body=body, recipient=recipient, problems=problems)
    else:
        oid = s.add_outreach(lead["id"], mission_id, "email", body, subject, recipient, problems)
    approval = None
    if recipient:
        approval = s.add_approval(mission_id, lead["id"], "gmail_draft",
                                  f"Create a Gmail draft (not sent) to {lead['name']} <{recipient}>: \"{subject}\"",
                                  {"outreach_id": oid})
    return {"folder": str(folder), "outreach_id": oid, "approval_id": approval, "note": note}, problems


# Gmail errors that prove no draft was created (it never got that far): the approval can simply be tried again
_NOT_CREATED = ("NotConfigured", "NotConnected", "AuthCanceled", "AuthExpired", "MissingScope", "RateLimited", "NotFound")


def _local_mime_problem(to, subject, body):
    """Build the message locally first (the same library Gmail drafting uses): a header that can't be written
    (line breaks...) is a local error, found before anything is claimed or sent. -> problem text or """""
    from email.message import EmailMessage

    try:
        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject or "(no subject)"
        msg.set_content(body or "")
        msg.as_bytes()
    except (ValueError, TypeError) as e:
        return f"{e.__class__.__name__}: {str(e)[:120]}"
    return ""


def approve(aid, via):
    """Carry out one approval. Only 'gmail_draft' exists: it creates a draft in your Gmail, never sends.
    Two-phase, so a crash or a double click can't create two drafts:
        pending -> (checks) -> executing (atomic claim) -> Gmail -> done
        a failure that proves nothing was created -> back to pending (try again)
        any other failure (network error, a draft that didn't read back as written) -> UNKNOWN: it may exist in Gmail;
        it is never retried automatically ("retry" after you've looked in your Drafts)
    -> (ok, message)"""
    from room_agent.missions.store import store

    s = store()
    a = s.approval(aid)
    if a is None:
        return False, "there's no approval with that number"
    if a["status"] != "pending":
        return False, {"executing": "it's being done right now", "done": "it's already done",
                       "unknown": "its last attempt may or may not have created the draft: look in Gmail's Drafts, then "
                                  "say 'retry' if it isn't there", "rejected": "it was rejected"}.get(a["status"], a["status"])
    if a["action"] != "gmail_draft":
        return False, f"unknown action '{a['action']}'"
    o = next((x for x in s.outreach(a["mission_id"]) if x["id"] == (a["payload"] or {}).get("outreach_id")), None)
    if o is None:
        return False, "the draft it refers to is gone"
    if o["status"] != "draft":
        return False, f"that draft is already {o['status']}"
    blocking = sender_problems()  # (checked now, not when the draft was written: you may have fixed them since)
    if blocking:
        return False, "fix these first (in .env, then restart Jarvis): " + "; ".join(blocking)
    from room_agent.missions.websites import valid_email

    rcpt = valid_email(o["recipient"])
    if not rcpt:  # (checked here, locally: nothing is claimed or sent)
        return False, "its recipient isn't exactly one valid email address, so nothing was done (reject it)"
    problem = _local_mime_problem(rcpt, o["subject"], o["body"])
    if problem:
        return False, f"the draft can't be built ({problem}), so nothing was done"
    try:
        from room_agent.integrations import provider

        g = provider("google")
        if not g.can("gmail", "compose"):
            return False, "Gmail isn't connected with permission to write drafts"
    except Exception as e:  # noqa: BLE001
        return False, f"Gmail isn't available ({e.__class__.__name__})"
    # Write-ahead: the operation and the Message-ID the draft will carry are recorded BEFORE Gmail is called, so if the
    # answer is lost (crash, network) the draft can be looked up by that id instead of guessed about (reconcile).
    import uuid

    op, existed = s.op_start(a["mission_id"], "gmail_draft", f"Gmail draft for approval #{aid}", idem_key=f"gmail:{aid}",
                             detail={"message_id": f"<jarvis-{aid}-{uuid.uuid4().hex[:12]}@jarvis.invalid>"})
    if existed:
        if op["state"] == "completed":  # (Gmail already has it - found by reconciliation: record it, never a 2nd one)
            s._exec("UPDATE approvals SET status='done', decided=?, result=? WHERE id=? AND status='pending'",
                    (time.time(), "an earlier attempt's draft is in Gmail (recorded); no second draft", aid))
            s._exec("UPDATE outreach SET status='gmail_draft', external_id=? WHERE id=? AND status='draft'",
                    (str((op.get("detail") or {}).get("draft_id") or ""), o["id"]))
            return False, "an earlier attempt already created this draft in Gmail: recorded as done, nothing created"
        return False, "an earlier attempt of this draft is unresolved: use retry (it checks Gmail first)"
    if not s.claim_approval(aid, via):
        s.op_finish(op["id"], "failed", error="not claimed (handled elsewhere)")
        return False, "it's already being handled"
    s.update_outreach(o["id"], status="sending_to_gmail")
    try:
        from room_agent.integrations.google.gmail import GmailService

        draft = GmailService(g).create_draft(rcpt, o["subject"], o["body"], message_id=op["detail"]["message_id"],
                                             marker=op["id"])
        from room_agent.missions import crashpoints

        crashpoints.hit("after_gmail_draft")
    except Exception as e:  # noqa: BLE001
        name = e.__class__.__name__
        if name in _NOT_CREATED:
            s.op_finish(op["id"], "failed", error=f"{name}: nothing was created")
            s.update_outreach(o["id"], status="draft")
            s.finish_approval(aid, "failed", f"{name}: nothing was created")
            s._exec("UPDATE approvals SET status='pending' WHERE id=? AND status='failed'", (aid,))
            return False, f"Gmail draft not created ({name}); nothing was created, so it can be approved again later"
        s.op_finish(op["id"], "uncertain", error=f"{name}: {str(e)[:150]}")
        s.update_outreach(o["id"], status="unknown")
        s.finish_approval(aid, "unknown", f"{name}: {str(e)[:150]}")
        return False, (f"not confirmed ({name}): the draft may or may not be in Gmail. Look in your Drafts; if it isn't "
                       "there, say 'retry approval " + str(aid) + "'.")
    s.op_finish(op["id"], "completed", detail={"draft_id": str(draft.get("id", ""))})
    s.update_outreach(o["id"], status="gmail_draft", external_id=str(draft.get("id", "")))
    s.finish_approval(aid, "done", f"Gmail draft {draft.get('id', '?')} created and read back")
    lead = s.lead(o["lead_id"])
    if lead and lead["contact_status"] == "not contacted":
        s.update_lead(lead["id"], contact_status="draft in Gmail (not sent)")
    return True, "Gmail draft created and read back; it is NOT sent: open Gmail to review and send it yourself"


# "not found" only counts as "absent" once the draft could have been indexed / listed: a draft created moments before
# the answer was lost may not show up yet (search indexing, list consistency).
RECONCILE_MIN_AGE_S = 600


def reconcile(aid):
    """Resolve an UNKNOWN Gmail draft from Gmail itself, read-only (nothing is created or changed in Gmail).
    -> (verdict, info):
        "created"   the draft exists (found by its X-Jarvis-Operation marker or Message-ID): recorded as done
        "absent"    a COMPLETE scan of the drafts found nothing AND the attempt is old enough to have shown up
        "wait"      not found yet, but too recent (or the scan was incomplete) to conclude anything
        "unknown"   Gmail couldn't be asked, or there's no record of the attempt (made by an older version)"""
    from room_agent.missions.store import store

    s = store()
    op = s.op_by_key(f"gmail:{aid}")
    msgid = ((op or {}).get("detail") or {}).get("message_id")
    if op is None or not msgid:
        return "unknown", "no record of what was sent (made by an older version): resolve it by hand"
    if op["state"] == "completed":
        return "created", ((op.get("detail") or {}).get("draft_id") or "")
    if op["state"] == "failed":
        return "absent", ""
    try:
        from room_agent.integrations import provider
        from room_agent.integrations.google.gmail import GmailService

        res = GmailService(provider("google")).find_draft(message_id=msgid, marker=op["id"])
    except Exception as e:  # noqa: BLE001
        return "unknown", f"Gmail couldn't be checked ({e.__class__.__name__})"
    if res.get("found"):
        s.op_finish(op["id"], "completed", detail={"draft_id": str(res["found"]), "reconciled": res.get("how", "")})
        return "created", str(res["found"])
    age = time.time() - op["created"]
    if not res.get("complete"):
        return "wait", f"only {res.get('scanned', 0)} drafts could be checked; not enough to say it's absent"
    if age < RECONCILE_MIN_AGE_S:
        return "wait", (f"not found yet, but the attempt is only {int(age)}s old; Gmail may not list it yet. Check again "
                        f"in {int(RECONCILE_MIN_AGE_S - age) // 60 + 1} min")
    s.op_finish(op["id"], "failed", error=f"reconciled: a complete scan of {res.get('scanned', 0)} drafts found no draft "
                                          "with its marker / Message-ID")
    return "absent", ""


def _mark_done(s, aid, a, info, how):
    oid = (a["payload"] or {}).get("outreach_id")
    s._exec("UPDATE approvals SET status='done', decided=?, result=? WHERE id=? AND status='unknown'",
            (time.time(), f"{how} ({info})" if info else how, aid))
    s._exec("UPDATE outreach SET status='gmail_draft', external_id=? WHERE id=? AND status IN ('unknown', "
            "'sending_to_gmail')", (info or "", oid))


def _mark_absent(s, aid, a, how):
    oid = (a["payload"] or {}).get("outreach_id")
    s._exec("UPDATE approvals SET status='pending', result=? WHERE id=? AND status='unknown'", (how, aid))
    s._exec("UPDATE outreach SET status='draft' WHERE id=? AND status IN ('unknown', 'sending_to_gmail')", (oid,))


def retry(aid, via):
    """For an UNKNOWN draft: check Gmail (read-only) - it NEVER creates a draft by itself.
        found        -> recorded as done (no second draft)
        absent       -> back to "waiting for approval": creating it needs a new, explicit approve
        wait/unknown -> nothing changes; say why (try again later, or resolve by hand)"""
    from room_agent.missions.store import store

    s = store()
    a = s.approval(aid)
    if a is None or a["status"] != "unknown":
        return False, "only an approval whose outcome is unknown can be checked"
    verdict, info = reconcile(aid)
    if verdict == "created":
        _mark_done(s, aid, a, info, f"reconciled by {via}: the draft exists in Gmail")
        return True, "the draft is in Gmail (found by its id): recorded as done, no second draft created"
    if verdict == "absent":
        _mark_absent(s, aid, a, f"reconciled by {via}: Gmail has no such draft")
        return True, ("Gmail confirms the draft was never created. It's waiting for approval again: approve it to "
                      "create it (once)")
    return False, f"still unresolved: {info}. Nothing was done"


def resolve(aid, verdict, via):
    """Manual resolution of an UNKNOWN draft, after you looked in Gmail yourself (when the automatic check can't
    decide). verdict 'created' (it's there) -> done; 'not_created' (it isn't) -> waiting for approval again (creating it
    still needs an explicit approve)."""
    from room_agent.missions.store import store

    s = store()
    a = s.approval(aid)
    if a is None or a["status"] != "unknown":
        return False, "only an approval whose outcome is unknown can be resolved"
    op = s.op_by_key(f"gmail:{aid}")
    if verdict == "created":
        if op is not None and op["state"] not in ("completed", "failed"):
            s.op_finish(op["id"], "completed", detail={"resolved_by": via, "manual": True})
        _mark_done(s, aid, a, "", f"resolved by {via}: they saw the draft in Gmail")
        return True, "recorded as done (you confirmed the draft is in Gmail)"
    if verdict == "not_created":
        if op is not None and op["state"] == "completed":  # (the record says Gmail has it: evidence beats a guess)
            return False, ("Gmail's own record shows this draft was created (draft "
                           f"{(op.get('detail') or {}).get('draft_id') or '?'}); look again, or mark it created")
        if op is not None and op["state"] not in ("completed", "failed"):
            s.op_finish(op["id"], "failed", error=f"resolved by {via}: not in Gmail (checked by hand)")
        _mark_absent(s, aid, a, f"resolved by {via}: not in Gmail")
        return True, "recorded as not created; it's waiting for approval again (approve it to create it, once)"
    return False, f"unknown verdict '{verdict}'"


def recover_approvals():
    """At startup: an approval left 'executing' by a crash may or may not have created its draft -> unknown."""
    from room_agent.missions.store import store

    s = store()
    rows = s._rows("SELECT * FROM approvals WHERE status='executing'")
    for a in rows:
        s.finish_approval(a["id"], "unknown", "Jarvis stopped while creating the draft: check Gmail's Drafts")
        s._exec("UPDATE outreach SET status='unknown' WHERE id=? AND status='sending_to_gmail'",
                ((a["payload"] or {}).get("outreach_id"),))
    return len(rows)
