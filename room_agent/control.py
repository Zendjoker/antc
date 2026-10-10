"""Jarvis's live link for the dashboard (UI/server.py): what it's doing right now, typed commands, and the dashboard's
buttons. Local only (127.0.0.1:CONTROL_PORT), and every request needs the control token (room_agent/localauth.py):
only the dashboard's server has it.

    GET  /live       state, the conversation, timers, where you are, what's playing, volume, activity, spend, timing
    GET  /knowledge  what Jarvis remembers about you (memory) and how you like things done (learned preferences)
    POST /command    {"text": "..."} -> Jarvis answers (out loud in the room, like a spoken request) -> {"reply": "..."}
    POST /action     {"do": "...", ...} -> a button: media, volume, timers, undo, quiet mode, forget, call me...

Buttons that change something on the PC go through the executor (actions/executor.py), exactly like a spoken request:
the same checks, the same verification, and they can be undone the same way.
"""

import asyncio
import json
import logging
import os
import threading
import time

from aiohttp import web

from room_agent import config
from room_agent import runtime as rt

log = logging.getLogger("room-agent")
_history = []  # typed commands are one ongoing conversation of their own (the transcript shows everything)
_media = {"at": 0.0, "text": None}
_volume = {"at": 0.0, "level": None, "muted": False}
_last_timing = {}
_activity = []  # [{"at", "text", "kind"}], newest last: what Jarvis did (from the event bus; no private details)

# event -> (kind, how it reads). Email and calendar events never show their content here.
_SAY = {
    "app.opened": ("app", "Opened {app}"), "app.closed": ("app", "Closed {app}"), "app.focused": ("app", "Switched to {app}"),
    "window.moved": ("window", "Moved {app} to another monitor"), "window.minimized": ("window", "Minimized {app}"),
    "window.maximized": ("window", "Maximized {app}"), "window.restored": ("window", "Restored {app}"),
    "volume.changed": ("volume", "Changed the volume"), "volume.muted": ("volume", "Muted the sound"),
    "volume.unmuted": ("volume", "Unmuted the sound"), "media.changed": ("media", "Changed playback"),
    "timer.set": ("timer", "Set a timer"), "alarm.set": ("timer", "Set an alarm"),
    "timer.cancelled": ("timer", "Cancelled a timer"), "timer.finished": ("timer", "Timer finished: {label}"),
    "alarm.ringing": ("timer", "Alarm went off: {label}"), "email.drafted": ("mail", "Drafted an email"),
    "email.sent": ("mail", "Sent an email"), "calendar.created": ("calendar", "Added a calendar event"),
    "calendar.updated": ("calendar", "Changed a calendar event"), "calendar.deleted": ("calendar", "Deleted a calendar event"),
    "driving.started": ("phone", "You started driving"), "driving.stopped": ("phone", "You stopped driving"),
    "door.opened": ("door", "{app} opened"), "door.closed": ("door", "{app} closed"),
    "vibration.detected": ("bed", "Movement: {app}"), "presence.detected": ("presence", "Someone's there ({app})"),
    "presence.cleared": ("presence", "Nobody there anymore ({app})"), "light.changed": ("light", "Changed the {app}"),
}


def _on_event(e):
    kind, text = _SAY.get(e["name"], (None, None))
    if not text:
        return
    label = e.get("label") or (e.get("args") or {}).get("label") or ""
    text = text.format(app=e.get("app") or "an app", label=label or "unnamed")
    if e["name"] in ("timer.set", "alarm.set") and label:
        text += f": {label}"
    _activity.append({"at": e["at"], "text": text, "kind": kind})
    del _activity[:-40]


# ---------------------------------------------------------------- reading
def _now_playing():
    """What's playing (checked at most every 4 s: it's a Windows call)."""
    if time.time() - _media["at"] > 4:
        _media["at"] = time.time()
        try:
            from room_agent.tools import media

            snap = media._winrt(media._snapshot)
            _media["text"] = (None if snap is None else
                              {"title": snap["title"], "artist": snap["artist"], "app": snap["app"],
                               "playing": snap["status"] == media.PLAYING})
        except Exception:
            _media["text"] = None
    return _media["text"]


def _read_volume(force=False):
    if force or time.time() - _volume["at"] > 3:
        _volume["at"] = time.time()
        try:
            from room_agent.tools import media

            ev = media._endpoint()
            _volume.update(level=media._level(ev), muted=bool(ev.GetMute()))
        except Exception:
            _volume.update(level=None)
    return {"level": _volume["level"], "muted": _volume["muted"]}


def _timers():
    from room_agent.tools import timers

    now = time.time()
    with timers._lock:
        items = sorted(timers._items.items(), key=lambda kv: kv[1]["due"])
    return [{"id": tid, "label": i["label"], "kind": i["kind"], "in_s": max(0, int(i["due"] - now)),
             "total_s": int(i["due"] - i["set_at"]) if i.get("set_at") else None, "daily": bool(i.get("daily")),
             "at": time.strftime("%I:%M %p", time.localtime(i["due"])).lstrip("0")} for tid, i in items]


def _phone():
    from room_agent.phone import state as phone

    missing = []
    try:
        from room_agent.phone.server import ready

        missing = ready()
    except Exception:
        missing = ["phone"]
    number = config.MY_PHONE
    return {"mode": bool(config.PHONE_MODE), "ready": bool(config.PHONE_MODE) and not missing,
            "driving": phone.is_driving(), "driving_source": (phone.driving or {}).get("source"),
            "in_call": bool(phone.in_call), "number": f"••• {number[-4:]}" if number else None}


def _home():
    try:
        from room_agent.tools.zigbee import hub

        return hub.snapshot()
    except Exception as e:
        log.debug("dashboard: zigbee unavailable: %s", e)
        return {"online": False, "devices": []}


def _undo_hint():
    from room_agent.actions.context import env

    r = env.undoable()
    return r.undo_hint.replace("_", " ") if r else None


def _research():
    """The last research report for the dashboard: the question, numbered sources with links, what couldn't be read."""
    try:
        from room_agent.computer import research

        r = research.latest()
    except Exception:
        return None
    if not r:
        return None
    return {"question": r["question"], "status": r["status"], "at": r["at"], "seconds": r["seconds"],
            "sources": [{"n": s["n"], "title": s["title"], "host": s["host"], "url": s["url"], "primary": s["primary"],
                         "quote": (s["passages"] or [""])[0][:280]} for s in r["sources"]],
            "failed": [f"{u.split('/')[2] if '//' in u else u}: {w}" for u, w in r["failed"][:6]]}


def _lists():
    """Open list items and reminders waiting for a moment, for the dashboard."""
    try:
        from room_agent import triggers
        from room_agent.tools import lists

        return {"lists": lists.snapshot(),
                "moments": [{"when": triggers.EVENTS[i["event"]], "text": i["text"]} for i in triggers._load()]}
    except Exception:
        return None


def _tasks():
    try:
        from room_agent.actions import tasks

        return tasks.snapshot()
    except Exception:
        return []


def _missions():
    """The latest missions with their progress (from missions.db), for the Missions page."""
    try:
        from room_agent.missions import business, engine  # noqa: F401 (registers the workflow)
        from room_agent.missions.store import store

        return [engine.progress(m["id"]) for m in store().missions(6)]
    except Exception as e:
        log.debug("dashboard: missions unavailable: %s", e)
        return []


def mission_detail(mid):
    """Everything about one mission: steps, leads (with evidence), demo sites, drafts, approvals, budget ledger, events.
    Google Places details are never stored: for a Google-only business they're added here live from memory (if still
    there), labelled "Google Maps"."""
    from room_agent.missions import business, engine, places, preview  # noqa: F401
    from room_agent.missions.store import store

    s = store()
    m = s.mission(mid)
    if m is None:
        return None
    keep = ("id", "name", "category", "address", "phone", "email", "website", "website_status", "score", "level",
            "reasons", "confidence", "missing", "status", "contact_status", "sources")
    leads = []
    for x in s.leads(mission_id=mid, limit=300):
        row = {k: x.get(k) for k in keep}
        row["evidence"] = ((x.get("analysis") or {}).get("evidence") or [])[:4]
        if (x.get("extra") or {}).get("google_only") and x.get("place_id"):
            g = places.recall(x["place_id"])
            if g:
                row["google"] = {k: g.get(k) for k in ("name", "address", "phone", "website")}
                row["google"]["attribution"] = places.ATTRIBUTION
        leads.append(row)
    projects = []
    for p in s.projects(mid):
        lead = s.lead(p["lead_id"]) or {}
        url = preview.url_for(p["path"])  # ("" when MISSIONS_DIR changed since: shown without a preview button)
        projects.append({"id": p["id"], "lead": lead.get("name", ""), "path": p["path"], "preview": url,
                         "status": p["status"], "history": (p.get("history") or [])[-5:]})
    return {"mission": engine.progress(mid), "params": m["params"],
            "steps": [{k: st[k] for k in ("key", "kind", "title", "state", "attempts", "evidence", "error", "cost_usd")}
                      for st in s.steps(mid)],
            "leads": leads, "projects": projects,
            "outreach": [{k: o[k] for k in ("id", "lead_id", "kind", "recipient", "subject", "body", "status", "problems")}
                         for o in s.outreach(mid)],
            "approvals": [{k: a[k] for k in ("id", "lead_id", "action", "summary", "status", "result")}
                          for a in s.approvals(mid)],
            "charges": [{**{k: c[k] for k in ("id", "what", "provider", "estimate_usd", "actual_usd", "state", "note",
                                               "created")},
                         "basis": c.get("basis") or ("estimate" if c["state"] == "uncertain" else "")}
                        for c in s.charges(mid, 50)],
            # operations: ids, kinds, states and reasons only (no payloads, results, keys or message contents)
            "operations": [{k: o[k] for k in ("id", "kind", "what", "state", "error", "created", "updated")}
                           for o in s.ops(mid)][-60:],
            "attention": engine.pending_actions(mid),
            "goal": __import__("room_agent.missions.goals", fromlist=["explain"]).explain(mid),
            "plan": engine.plan_view(mid)[-80:],
            "owner": __import__("room_agent.missions.ownership", fromlist=["info"]).info(),
            "events": [{k: e[k] for k in ("at", "level", "text")} for e in s.events(mid, 60)]}


def _mission_action(what, body):
    """A dashboard click is the user's explicit decision (the request passed the local-only + X-Jarvis checks)."""
    from room_agent.missions import business, engine, outreach, preview
    from room_agent.missions.store import store

    s = store()
    mid = str(body.get("id") or "")
    if what == "mission_pause":
        return {"ok": engine.pause(mid, "paused from the dashboard"), "message": "Paused."}
    if what == "mission_resume":
        why = engine.resume_blocker(mid)
        ok = not why and engine.resume(mid)
        return {"ok": ok, "message": "Resumed." if ok else f"Not resumed: {why or engine.resume_blocker(mid) or 'it changed meanwhile'}."}
    if what == "mission_budget":
        extra = max(0.0, min(float(body.get("extra_usd") or 0), 10.0))
        res = engine.raise_budget(mid, extra, "dashboard")
        if res is None:
            return {"ok": False, "message": "The budget wasn't changed."}
        old, new = res
        if new <= old + 1e-9:
            return {"ok": False, "message": f"Not changed: the budget is already at the maximum (${new:.2f}, "
                                            "MISSION_MAX_BUDGET_USD)."}
        msg = f"Budget raised from ${old:.2f} to ${new:.2f}" + (" (capped at the maximum)" if new < old + extra - 1e-9 else "")
        m = s.mission(mid)
        if m and m["state"] == "paused_budget":
            msg += "; running again" if engine.resume(mid) else f"; still paused: {engine.resume_blocker(mid)}"
        elif m and m["state"] == "paused_daily":
            msg += "; still paused by today's overall model budget"
        return {"ok": True, "message": msg + "."}
    if what == "mission_reconcile":
        notes = engine.reconcile_now(mid)
        return {"ok": True, "message": ("Checked: " + "; ".join(notes[:4])) if notes else "Checked: nothing changed."}
    if what == "mission_resolve":
        ok, msg = engine.resolve(mid, str(body.get("item_type") or ""), str(body.get("item_id") or ""),
                                 str(body.get("action") or ""), "dashboard")
        return {"ok": ok, "message": msg}
    if what == "mission_stop":
        return {"ok": engine.stop(mid, "stopped from the dashboard"), "message": "Stopped; the work so far is kept."}
    if what == "mission_demos":
        try:
            picked, res = business.build_demos(mid, int(body.get("count") or 3))
        except ValueError as e:  # (engine.MissionClosed: a stopped mission takes no new work)
            return {"ok": False, "message": f"{e}."}
        if not picked:
            return {"ok": False, "message": "Nothing to build (no qualifying business without a demo)."}
        names = ", ".join(x["name"] for x in picked)
        return {"ok": True, "message": f"Queued: {names}." if res["running"] else
                f"Queued but not running: {names}. {res['why_not'].capitalize()}."}
    if what == "mission_preview":
        p = next((x for x in s.projects(mid) if x["id"] == int(body.get("project_id") or 0)), None)
        if p is None:
            return {"ok": False, "message": "That preview isn't available."}
        url = preview.url_for(p["path"])
        if not url:
            return {"ok": False, "message": f"This demo is outside the current missions folder; open {p['path']}\\index.html "
                                            "directly."}
        if not preview.start():
            return {"ok": False, "message": "The preview server couldn't start (MISSION_PREVIEW_PORT in use?)."}
        return {"ok": True, "message": "Preview ready.", "url": url}
    if what in ("approval_approve", "approval_reject", "approval_retry"):
        aid = int(body.get("approval_id") or 0)
        if what == "approval_reject":
            ok = s.decide_approval(aid, "rejected", "dashboard")
            return {"ok": ok, "message": "Rejected; nothing was done." if ok else "That item isn't waiting any more."}
        ok, result = outreach.retry(aid, "dashboard") if what == "approval_retry" else outreach.approve(aid, "dashboard")
        return {"ok": ok, "message": result}
    return {"ok": False, "message": "Unknown action."}


def _browser():
    try:
        from room_agent.computer.browsers import KNOWN, host
        from room_agent.computer.context import desk

        p = desk.current_page()
    except Exception:
        return None
    return {"name": KNOWN[p["browser"]].name, "title": p["title"][:80], "site": host(p["url"])} if p else None


def snapshot():
    from room_agent.llm.budget import budget
    from room_agent.tools import location

    if rt.turn.timing:
        _last_timing.update(rt.turn.timing)
    loc = location.locate() if location.enabled() else None
    google = "not set up"
    try:
        from room_agent.integrations import provider

        g = provider("google")
        google = {"not_configured": "not set up", "disconnected": "not connected", "expired": "needs reconnecting",
                  "connected": f"connected ({g.active()})"}[g.connection_status()]
    except Exception:
        pass
    ringing, phone = rt.ringing, _phone()
    return {
        "online": True,
        "health": {"pid": os.getpid(), "uptime_s": round(time.time() - rt.started_at) if rt.started_at else None,
                   "heartbeat_age_s": round(time.time() - rt.heartbeat, 1) if rt.heartbeat else None,
                   "state": rt.state.state.name.lower()},
        "state": {"name": rt.state.state.name.lower(), "label": rt.state.state.value},
        "conversation": [{"role": m["role"], "text": m["text"], "time": m.get("time", "")} for m in list(rt.recent)[-40:]],
        "timers": _timers(),
        "ringing": {"label": ringing["label"], "kind": ringing["kind"]} if ringing else None,
        "location": location.describe(loc) if loc else None,
        "now_playing": _now_playing(),
        "volume": _read_volume(),
        "app": (rt.last_active_app or {}).get("name"),
        "phone": phone, "driving": phone["driving"], "in_call": phone["in_call"],
        "activity": list(reversed(_activity[-15:])),
        "undo": _undo_hint(),
        "timing": dict(_last_timing),
        "spend": {"today": round(budget.total(), 4), "limit": config.DAILY_BUDGET_USD},
        "brain": config.OPENAI_MODEL if config.LLM_DEFAULT == "openai" else config.MODEL,
        "voice": config.TTS_PROVIDER, "hearing": config.STT_PROVIDER, "wake_word": config.WAKE_WORD,
        "user": config.USER_NAME,
        "connections": {"google": google, "phone": "on" if config.PHONE_MODE else "off"},
        "home": _home(),
        "research": _research(),
        "lists": _lists(),
        "tasks": _tasks(),
        "missions": _missions(),
        "browser": _browser(),
    }


def knowledge():
    """What Jarvis knows about you: facts (memory) and learned preferences, each with where it came from."""
    out = {"profile": [], "facts": [], "preferences": [], "summaries": []}
    try:
        snap = rt.memory.snapshot()
        out["profile"] = [{"key": k, "label": k.replace("_", " ").capitalize(), "value": v} for k, v in snap["profile"].items()]
        out["facts"] = [{"id": f["id"], "text": f["fact"], "saved": f["saved"], "category": f["category"]}
                        for f in reversed(snap["facts"])]
        out["summaries"] = snap["summaries"][-6:][::-1]
    except Exception as e:
        log.debug("dashboard: memory unavailable: %s", e)
    try:
        from room_agent import learning
        from room_agent.learning import model as um

        m = learning.user_model()
        sure = {p["key"] for p in m.all()}
        for p in m.all(include_tentative=True):
            kind, subject = um.split_key(p["key"])
            out["preferences"].append({
                "key": p["key"], "kind": kind.replace("_", " "), "text": um.KINDS[kind]["say"](subject, p["value"]),
                "source": p["source"], "confidence": round(p["confidence"], 2), "evidence": p["evidence"],
                "auto": bool(p["auto"]), "applied": p["key"] in sure, "because": p.get("because") or ""})
    except Exception as e:
        log.debug("dashboard: learning unavailable: %s", e)
    return out


# ---------------------------------------------------------------- doing
def run_command(text):
    """A typed command: answered like a spoken one (out loud in the room). -> the reply text."""
    from room_agent.conversation.history import reply_text
    from room_agent.conversation.turn import take_turn

    mark = len(_history)
    take_turn(_history, text, final=True)
    return reply_text(_history[mark + 1:]) if len(_history) > mark else ""


# button -> (capability, what the user is asking for, in words: the executor reads it like a spoken request)
_CAPS = {
    "play_pause": ("play_pause", "pause or play the music"), "next": ("next_track", "next song"),
    "previous": ("previous_track", "previous song"), "mute": ("mute", "mute the sound"), "unmute": ("unmute", "unmute"),
    "volume": ("set_volume", "set the volume to {percent} percent"), "undo": ("undo_last_action", "undo that"),
    "timer": ("set_timer", "set a timer for {seconds} seconds"), "routine": ("run_routine", "{trigger}"),
}


def _execute(name, args, words):
    from room_agent.actions import executor

    with rt.brain:  # one thing at a time, like a spoken turn
        rt.new_turn(words)
        result = executor.execute(name, args)
    msg = result.message.split(":", 1)[-1].strip()
    return {"ok": result.success, "message": msg}


def do(body):
    """One dashboard button. -> {"ok", "message"}."""
    what = str(body.get("do") or "")
    if what in _CAPS:
        name, words = _CAPS[what]
        args = {k: v for k, v in body.items() if k != "do"}
        if what == "volume":
            args = {"percent": max(0, min(100, int(body.get("percent", 0))))}
        if what == "timer":
            args = {"seconds": max(1, int(body.get("seconds", 60))), "label": str(body.get("label") or "")[:60],
                    "message": f"Hey, your {body.get('label') or 'timer'} is done!"}
        out = _execute(name, args, words.format(**args))
        if what == "undo" and out["ok"]:
            _activity.append({"at": time.time(), "text": "Undid the last change", "kind": "undo"})
        if what in ("volume", "mute", "unmute"):
            _read_volume(force=True)
        if what in ("play_pause", "next", "previous"):
            _media["at"] = 0
        return out
    if what == "light":  # (through the executor, like "make the strip blue": verified, undoable)
        args = {k: body[k] for k in ("device", "on", "toggle", "brightness", "color", "white", "effect") if k in body}
        if "brightness" in args:
            args["brightness"] = max(1, min(100, int(args["brightness"])))
        return _execute("set_light", args, "change the light")
    if what == "cancel_timer":
        from room_agent.tools import timers

        label = timers.cancel_id(int(body.get("id")))
        if label is None:
            return {"ok": False, "message": "That one isn't set anymore."}
        _on_event({"name": "timer.cancelled", "at": time.time()})
        return {"ok": True, "message": f"Cancelled {label}."}
    if what == "stop_ringing":
        from room_agent.tools import timers

        return {"ok": timers.acknowledge_ring(), "message": "Stopped."}
    if what == "quiet":
        from room_agent.conversation.states import State

        on = bool(body.get("on"))
        if rt.state.awake:
            return {"ok": False, "message": "Jarvis is in a conversation right now; try again in a moment."}
        rt.state.go(State.QUIET if on else State.WAKE_WORD_ONLY, "from the dashboard")
        return {"ok": True, "message": "Quiet mode is on." if on else "Quiet mode is off."}
    if what == "forget_fact":
        n = rt.memory.remove_ids([int(body.get("id"))])
        rt.memory.generation += 1
        return {"ok": bool(n), "message": "Forgotten." if n else "Already gone."}
    if what in ("forget_preference", "auto_preference"):
        from room_agent import learning

        m = learning.user_model()
        key = str(body.get("key") or "")
        if not m.store.preference(m.user, key):
            return {"ok": False, "message": "That preference is already gone."}
        if what == "forget_preference":
            m.forget(key)
            return {"ok": True, "message": "Forgotten, along with what it was learned from."}
        m.set_auto(key, bool(body.get("on")))
        return {"ok": True, "message": "Saved."}
    if what.startswith(("mission_", "approval_")):
        return _mission_action(what, body)
    if what == "emergency_stop":
        from room_agent import emergency

        done = emergency.stop_everything("dashboard")
        return {"ok": True, "message": "Stopped: " + (", ".join(done) if done else "nothing was running") + "."}
    if what == "call_me":
        p = _phone()
        if not p["ready"]:
            return {"ok": False, "message": "The phone line isn't set up. See phone.md."}
        if p["in_call"]:
            return {"ok": False, "message": "You're already on a call with Jarvis."}
        from room_agent.phone.server import place_call

        place_call("dashboard", "Hey, it's Jarvis. You asked me to call from the dashboard. What's up?")
        return {"ok": True, "message": "Calling your phone now."}
    return {"ok": False, "message": "Unknown action."}


# ---------------------------------------------------------------- the link
LOCAL_HOSTS = ("127.0.0.1", "localhost")


def local_host(host):
    """The Host header names this PC (a page on another site that re-points its name to 127.0.0.1, "DNS rebinding",
    sends its own name here, so it's refused)."""
    name = str(host or "").strip().lower()
    name = name[1:].split("]")[0] if name.startswith("[") else name.rsplit(":", 1)[0] if name.count(":") == 1 else name
    return name in LOCAL_HOSTS or name == "::1"


@web.middleware
async def _only_local(request, handler):
    if not local_host(request.headers.get("Host", "")):
        return web.json_response({"error": "refused: not a local request"}, status=403)
    from room_agent import localauth

    if not localauth.valid(request.headers.get(localauth.HEADER, "")):  # (any local program can reach 127.0.0.1)
        return web.json_response({"error": "refused: missing or wrong control token"}, status=403)
    return await handler(request)


def _allowed(request):
    origin = request.headers.get("Origin", "")
    return request.headers.get("X-Jarvis") == "1" and (not origin or origin.startswith(("http://127.0.0.1:", "http://localhost:")))


async def live(request):
    return web.json_response(await asyncio.get_running_loop().run_in_executor(None, snapshot))


async def knowledge_view(request):
    return web.json_response(await asyncio.get_running_loop().run_in_executor(None, knowledge))


async def mission_view(request):
    mid = request.query.get("id", "")
    out = await asyncio.get_running_loop().run_in_executor(None, mission_detail, mid)
    return web.json_response(out if out is not None else {"error": "no such mission"}, status=200 if out else 404,
                             dumps=lambda o: json.dumps(o, default=str))


async def command(request):
    if not _allowed(request):
        return web.json_response({"error": "refused"}, status=403)
    try:
        text = str((await request.json()).get("text", "")).strip()[:500]
    except ValueError:
        text = ""
    if not text:
        return web.json_response({"error": "say something"}, status=400)
    log.info("typed: %s", text)
    reply = await asyncio.get_running_loop().run_in_executor(None, run_command, text)
    return web.json_response({"reply": reply})


async def action(request):
    if not _allowed(request):
        return web.json_response({"error": "refused"}, status=403)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    log.info("dashboard: %s", body.get("do"))
    try:
        out = await asyncio.get_running_loop().run_in_executor(None, do, body)
    except (TypeError, ValueError):
        out = {"ok": False, "message": "That didn't look right."}
    except Exception as e:
        log.warning("dashboard action %s failed: %s", body.get("do"), e)
        out = {"ok": False, "message": f"That didn't work ({e.__class__.__name__})."}
    return web.json_response(out)


def start():
    from room_agent.actions.events import events

    events.on("*", _on_event)

    from room_agent import localauth

    localauth.token()  # (made now, so the dashboard finds it)

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app = web.Application(middlewares=[_only_local])
        app.add_routes([web.get("/live", live), web.get("/knowledge", knowledge_view), web.get("/missions", mission_view),
                        web.post("/command", command), web.post("/action", action)])
        runner = web.AppRunner(app, access_log=None)
        loop.run_until_complete(runner.setup())
        try:
            loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", config.CONTROL_PORT).start())
        except OSError as e:
            log.warning("dashboard link couldn't start on port %d (%s)", config.CONTROL_PORT, e)
            return
        loop.run_forever()

    threading.Thread(target=run, daemon=True, name="control").start()
