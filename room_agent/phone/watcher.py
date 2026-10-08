"""While you drive: is anything important enough to call you about? Only then, and never twice for the same thing.

Important means (all checked by code, nothing guessed by the model):
  - an unread email from someone in VIP_SENDERS, or one Gmail itself marks important, that arrived since you set off
    (promotions / social / updates / forums never count)
  - a meeting starting within MEETING_SOON_MIN minutes, or one that moved since you set off
  - a timer or alarm going off at home while you're out

It only looks while you're driving (every DRIVE_CHECK_S), never when you're home; it waits CALL_COOLDOWN_MIN between
calls (an alarm going off doesn't wait); it never calls about the same thing twice.
"""

import datetime
import logging
import threading
import time

from room_agent import config
from room_agent.phone import state

log = logging.getLogger("room-agent")


class Watcher:
    def __init__(self, call):
        """call(key, greeting, item) places the call."""
        self.call = call
        self.notified = set()
        self.last_call = 0.0
        self.last_check = 0.0
        self.since = time.time()
        self.snapshot = {}  # event id -> start, taken when driving starts (to notice a meeting that moved)
        self._stop = threading.Event()

    # ----- events
    def on_driving(self, event):
        if event["name"] == "driving.started":
            self.since = time.time() - 60
            self.snapshot = {e["id"]: e["start"] for e in self._events(hours=24)}
            self.last_check = 0.0

    def on_ring(self, event):
        if state.is_driving():
            text = event.get("message") or f"your {event.get('label', 'timer')} is done"
            self.consider(f"ring:{event.get('label')}:{int(event['at'])}", f"Hey, it's Jarvis. Heads up: {text}", None,
                          urgent=True)

    def on_door(self, event):
        """Your door opened while you're out driving: worth a call."""
        if state.is_driving():
            self.consider(f"door:{event.get('device')}:{int(event['at'] // 60)}",
                          f"Hey, it's Jarvis. Heads up: the {event.get('device', 'door').lower()} just opened while you're out.",
                          None, urgent=True)

    # ----- deciding
    def consider(self, key, greeting, item, urgent=False):
        if key in self.notified:
            return False
        if not urgent and time.time() - self.last_call < config.CALL_COOLDOWN_MIN * 60:
            return False  # (not marked: it's reconsidered after the cool-down)
        self.notified.add(key)
        self.last_call = time.time()
        log.info("phone: calling about %s", key.split(":")[0])
        try:
            self.call(key, greeting, item)
        except Exception as e:
            log.warning("phone: couldn't place the call (%s)", e.__class__.__name__)
        return True

    def tick(self):
        """One look at email and calendar (only while driving)."""
        if not state.is_driving():
            return
        self.last_check = time.time()
        for key, greeting, item in self.important_email() + self.important_events():
            if self.consider(key, greeting, item):
                break  # (one call at a time; the rest waits for the next look)

    # ----- what counts as important
    def important_email(self):
        from room_agent.integrations import provider
        from room_agent.integrations.base import IntegrationError

        g = provider("google")
        if not g.can("gmail", "read"):
            return []
        try:
            from room_agent.integrations.google.gmail import GmailService

            svc = GmailService(g)
            found = svc.search(f"is:unread in:inbox after:{int(self.since)} -category:promotions -category:social "
                               "-category:updates -category:forums", 10)
        except IntegrationError as e:
            log.info("phone: couldn't check email (%s)", e.code)
            return []
        out = []
        for m in found:
            vip = any(v and (v in m["from_email"] or v in m["from_name"].lower()) for v in config.VIP_SENDERS)
            if vip or m["important"]:
                out.append((f"email:{m['id']}",
                            f"Hey, it's Jarvis. You got an email from {m['from_name']} about {m['subject']}. Want me to read it?",
                            {"kind": "email", "id": m["id"], "thread_id": m["thread_id"], "account": svc.account,
                             "from_email": m["from_email"], "label": f"email from {m['from_email']} ({m['date']})"}))
        return out

    def _events(self, hours):
        from room_agent.integrations import provider
        from room_agent.integrations.base import IntegrationError

        g = provider("google")
        if not g.can("calendar", "read"):
            return []
        try:
            from room_agent.integrations.google.calendar import CalendarService

            svc = CalendarService(g)
            now = datetime.datetime.now(svc.tz())
            return svc.events(now, now + datetime.timedelta(hours=hours), limit=20)
        except IntegrationError as e:
            log.info("phone: couldn't check the calendar (%s)", e.code)
            return []

    def important_events(self):
        from room_agent.integrations import provider

        out = []
        events = self._events(hours=24)
        account = provider("google").active() or ""
        for e in events:
            if e["all_day"]:
                continue
            mins = (e["start"] - datetime.datetime.now(e["start"].tzinfo)).total_seconds() / 60
            when = e["start"].strftime("%I:%M %p").lstrip("0")
            if 0 < mins <= config.MEETING_SOON_MIN:
                out.append((f"soon:{e['id']}:{e['start'].isoformat()}",
                            f"Hey, it's Jarvis. Heads up, {e['title']} starts in {round(mins)} minutes, at {when}.",
                            {"kind": "event", "id": e["id"], "account": account, "label": f"event at {when}"}))
            elif e["id"] in self.snapshot and self.snapshot[e["id"]] != e["start"]:
                out.append((f"moved:{e['id']}:{e['start'].isoformat()}",
                            f"Hey, it's Jarvis. Heads up, {e['title']} moved to {when}.",
                            {"kind": "event", "id": e["id"], "account": account, "label": f"event at {when}"}))
        self.snapshot.update({e["id"]: e["start"] for e in events})
        return out

    # ----- running
    def start(self):
        from room_agent.actions.events import events

        events.on("driving.*", self.on_driving)
        events.on("alarm.ringing", lambda e: self.on_ring({**e, "at": e["at"]}))
        events.on("timer.finished", lambda e: self.on_ring({**e, "at": e["at"]}))
        events.on("door.opened", self.on_door)
        threading.Thread(target=self._loop, daemon=True, name="phone-watcher").start()

    def _loop(self):
        while not self._stop.wait(15):
            if state.is_driving() and time.time() - self.last_check >= config.DRIVE_CHECK_S:
                try:
                    self.tick()
                except Exception as e:
                    log.warning("phone watcher: %s", e)
