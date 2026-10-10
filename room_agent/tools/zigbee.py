"""Zigbee devices (Aqara sensors, lights) through Zigbee2MQTT running on this PC.

Zigbee2MQTT talks to the USB dongle and publishes every device on a local MQTT broker (127.0.0.1:1883); this keeps a
live copy of every device's state, announces what happens on the event bus ("door.opened", "vibration.detected"...)
and sends commands ("turn the LED strip blue"). Nothing leaves the PC.

    hub.devices        name -> what it is (model, kind, what it reports, what a light can do)
    hub.state          name -> its latest readings
    hub.set(name, {...})  a command; returns the state read back afterwards (or None if it didn't answer)
"""

import json
import logging
import os
import socket
import threading
import time

from room_agent import config

log = logging.getLogger("room-agent")

COLORS = {"red": "#FF0000", "orange": "#FF7A00", "yellow": "#FFD000", "green": "#00C853", "teal": "#00BFA5",
          "cyan": "#00E5FF", "blue": "#0066FF", "navy": "#1A237E", "purple": "#8E24FF", "violet": "#8E24FF",
          "pink": "#FF4FA3", "magenta": "#FF00FF", "lavender": "#B39DDB", "gold": "#FFB300", "amber": "#FFB300"}
WHITES = {"warm": 0.85, "soft": 0.85, "neutral": 0.5, "natural": 0.5, "cool": 0.15, "daylight": 0.05, "cold": 0.05}
MOVES = {"vibration", "tilt", "drop"}


def clock(at):
    return time.strftime("%I:%M %p", time.localtime(at)).lstrip("0") if at else ""


def _ago(at):
    s = time.time() - at
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    return f"at {clock(at)}" + ("" if s < 20 * 3600 else time.strftime(" on %a", time.localtime(at)))


class Hub:
    def __init__(self):
        self.devices, self.state = {}, {}
        self.changed = {}  # name -> {"opened": at, "closed": at, "moved": at, "presence": at, "updated": at}
        self.events = self._load_events()  # what happened, newest last, kept on disk (a restart doesn't forget the door)
        self.offline = set()  # devices Zigbee2MQTT reports unavailable
        self.online = False
        self.client = None
        self._lock = threading.Lock()
        self._updated = threading.Condition(self._lock)

    # ---------------------------------------------------------------- event history (on disk, 48 h)
    KEEP_EVENTS_S = 48 * 3600

    def _load_events(self):
        try:
            items = json.loads(config.ZIGBEE_EVENTS_FILE.read_text(encoding="utf-8"))
            cutoff = time.time() - self.KEEP_EVENTS_S
            return [e for e in items if isinstance(e, dict) and e.get("at", 0) >= cutoff][-300:]
        except (FileNotFoundError, ValueError, OSError):
            return []

    def _save_events(self):
        try:
            config.ZIGBEE_EVENTS_FILE.write_text(json.dumps(self.events[-300:]), encoding="utf-8")
        except OSError as e:
            log.debug("zigbee: couldn't save the event history (%s)", e)

    def record(self, device, event, note="", at=None):
        """One thing that happened ("door opened"), with an optional note about what Jarvis did about it."""
        with self._lock:
            self.events.append({"at": at or time.time(), "device": device, "event": event, "note": note})
            cutoff = time.time() - self.KEEP_EVENTS_S
            self.events = [e for e in self.events if e["at"] >= cutoff][-300:]
        self._save_events()

    def annotate(self, device, event, note):
        """Add what Jarvis did to the latest such event (e.g. 'no greeting: you were talking with me')."""
        with self._lock:
            hit = next((e for e in reversed(self.events) if e["device"] == device and e["event"] == event), None)
            if hit is not None:
                hit["note"] = note
        self._save_events()

    def last_event(self, device, event):
        return next((e for e in reversed(self.events) if e["device"] == device and e["event"] == event), None)

    def recent_events(self, within_s=6 * 3600, limit=8):
        cutoff = time.time() - within_s
        return [e for e in self.events if e["at"] >= cutoff][-limit:]

    def event_lines(self, within_s=6 * 3600, limit=8):
        out = []
        for e in self.recent_events(within_s, limit):
            out.append(f"{e['device']} {e['event']} at {clock(e['at'])}" + (f" ({e['note']})" if e.get("note") else ""))
        return out

    # ---------------------------------------------------------------- connection
    def ready(self):
        return self.online and bool(self.devices)

    def start(self):
        if not config.ZIGBEE:
            return
        threading.Thread(target=self._connect, daemon=True, name="zigbee").start()

    def _broker_up(self):
        try:
            with socket.create_connection((config.ZIGBEE_MQTT_HOST, config.ZIGBEE_MQTT_PORT), timeout=1):
                return True
        except OSError:
            return False

    def _autostart(self):
        """Start Zigbee2MQTT (and its broker) with the start.bat next to it, if it isn't running yet."""
        bat = os.path.join(config.ZIGBEE2MQTT_DIR, "start.bat") if config.ZIGBEE2MQTT_DIR else ""
        if self._broker_up() or not bat or not os.path.exists(bat) or os.name != "nt":
            return
        log.info("zigbee: starting Zigbee2MQTT (%s)", bat)
        os.startfile(bat)  # (two small windows: the broker and Zigbee2MQTT)
        for _ in range(30):
            if self._broker_up():
                return
            time.sleep(1)

    def _connect(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.warning("zigbee: paho-mqtt isn't installed (pip install paho-mqtt), so Zigbee devices are off")
            return
        self._autostart()
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"jarvis-{os.getpid()}")
        c.on_connect = lambda cl, u, f, rc, p=None: (cl.subscribe(f"{config.ZIGBEE_TOPIC}/#"),
                                                     log.info("zigbee: connected to Zigbee2MQTT"))
        c.on_disconnect = lambda *a: setattr(self, "online", False)
        c.on_message = lambda cl, u, m: self.handle(m.topic, m.payload)
        c.reconnect_delay_set(2, 60)
        self.client = c
        try:
            c.connect_async(config.ZIGBEE_MQTT_HOST, config.ZIGBEE_MQTT_PORT, keepalive=30)
            c.loop_start()
        except Exception as e:
            log.warning("zigbee: couldn't reach the MQTT broker (%s)", e)

    # ---------------------------------------------------------------- incoming
    def handle(self, topic, payload):
        base = config.ZIGBEE_TOPIC + "/"
        if not topic.startswith(base):
            return
        rest = topic[len(base):]
        try:
            data = json.loads(payload) if payload else None
        except ValueError:
            data = payload.decode(errors="replace") if isinstance(payload, bytes) else payload
        if rest == "bridge/state":
            self.online = (data.get("state") if isinstance(data, dict) else data) == "online"
        elif rest == "bridge/devices" and isinstance(data, list):
            self._devices(data)
        elif rest.endswith("/availability") and not rest.startswith("bridge/"):
            name = rest[:-len("/availability")]
            state = data.get("state") if isinstance(data, dict) else data
            was_offline = name in self.offline
            (self.offline.discard if state == "online" else self.offline.add)(name)
            if state != "online" and not was_offline:
                self.record(name, "went offline")
                try:
                    from room_agent import proactive

                    proactive.decide(proactive.Event("device_offline", f"offline:{name}", f"{name} went offline"))
                except Exception as e:
                    log.debug("zigbee: offline notice skipped (%s)", e)
        elif not rest.startswith("bridge/") and "/" not in rest and isinstance(data, dict):
            self._update(rest, data)

    def _devices(self, items):
        found = {}
        for d in items:
            if d.get("type") == "Coordinator" or not d.get("definition"):
                continue
            df = d["definition"]
            props, light = set(), {}
            for e in df.get("exposes", []):
                for f in [e] + list(e.get("features", [])):
                    if f.get("property"):
                        props.add(f["property"])
                    if f.get("property") == "color_temp":
                        light["mireds"] = (f.get("value_min", 153), f.get("value_max", 500))
                    if f.get("property") == "effect":
                        light["effects"] = f.get("values", [])
                    if f.get("property") == "effect_speed":  # Aqara LED Strip T1 (lumi.light.acn132): 0-100%, numeric
                        light["effect_speed"] = (f.get("value_min", 0), f.get("value_max", 100))
                    if f.get("property") == "brightness":
                        light["max"] = f.get("value_max", 254)
                    if f.get("property") == "color_xy" or f.get("name") == "color_xy":
                        light["color"] = True
            found[d["friendly_name"]] = {"name": d["friendly_name"], "ieee": d["ieee_address"], "model": df.get("model"),
                                         "description": df.get("description", ""), "vendor": df.get("vendor", ""),
                                         "battery": d.get("power_source") == "Battery", "props": props, "light": light,
                                         "kind": self._kind(props, df.get("description", ""))}
        with self._lock:
            self.devices = found
            self.online = True

    @staticmethod
    def _kind(props, description):
        if "contact" in props:
            return "contact"
        if props & {"presence", "occupancy"}:
            return "presence"
        if "vibration" in props:
            return "vibration"
        if "state" in props and "brightness" in props:
            return "light"
        if "temperature" in props and "humidity" in props:
            return "climate"
        if "state" in props:
            return "switch"
        return "other"

    def _update(self, name, data):
        from room_agent.actions.events import events

        now = time.time()
        with self._lock:
            old = dict(self.state.get(name, {}))
            self.state[name] = {**old, **data}
            marks = self.changed.setdefault(name, {})
            marks["updated"] = now
            self._updated.notify_all()
        said = []
        if "contact" in data and data["contact"] != old.get("contact") and "contact" in old:
            what = "closed" if data["contact"] else "opened"
            marks[what] = now
            said.append(("door." + what, {}))
        if data.get("action") in MOVES and (data.get("action") != old.get("action") or data.get("vibration") != old.get("vibration")
                                           or now - marks.get("moved", 0) > 5):
            marks["moved"] = now
            said.append(("vibration.detected", {"action": data["action"]}))
        for key in ("presence", "occupancy"):
            if key in data and data[key] != old.get(key):
                marks["presence"] = now
                said.append(("presence." + ("detected" if data[key] else "cleared"), {}))
        for event, extra in said:
            self.record(name, {"door.opened": "opened", "door.closed": "closed", "vibration.detected": "moved",
                               "presence.detected": "someone detected", "presence.cleared": "nobody there"}[event]
                        + (f" ({extra['action']})" if extra.get("action") not in (None, "vibration") else ""), at=now)
            try:
                events.emit(event, device=name, app=name, **extra)
            except Exception as e:
                log.debug("zigbee event %s: %s", event, e)

    # ---------------------------------------------------------------- commands
    @staticmethod
    def aliases():
        """ZIGBEE_ALIASES="bed=Vibration sensor, desk light=LED strip" -> {"bed": "Vibration sensor", ...}"""
        out = {}
        for part in config.ZIGBEE_ALIASES.split(","):
            if "=" in part:
                a, name = part.split("=", 1)
                if a.strip() and name.strip():
                    out[a.strip().lower()] = name.strip()
        return out

    def find(self, said="", kind=None):
        """A device by what they called it ("the strip", "LED", "door", or one of your aliases); with no name, the only
        one of that kind."""
        with self._lock:
            pool = [d for d in self.devices.values() if kind is None or d["kind"] == kind]
        said = str(said or "").lower().strip()
        alias = self.aliases().get(said) or next((n for a, n in self.aliases().items() if a in said.split() or a == said), None)
        if alias and any(d["name"] == alias for d in pool):
            return next(d for d in pool if d["name"] == alias)
        if said:
            words = {w for w in said.replace("-", " ").split() if w not in {"the", "my", "a", "light", "lights", "sensor"}}
            hits = [d for d in pool if said == d["name"].lower() or words & set(d["name"].lower().split())
                    or any(w in (d["description"] + " " + (d["model"] or "")).lower() for w in words if len(w) > 2)]
            if hits:
                return hits[0]
        return pool[0] if len(pool) == 1 else None

    def set(self, name, payload, wait=4.0):
        """Send a command; -> the device's state once it reports back (None if it didn't within `wait`)."""
        if self.client is None or name in self.offline:
            return None  # (an offline device can't answer: fail at once instead of waiting)
        with self._lock:
            before = self.changed.get(name, {}).get("updated", 0)
        sent = time.time()
        wanted = {k: v for k, v in payload.items() if k in ("state", "brightness", "color_temp", "effect")}

        def answered():  # (its report reflects THIS command: rapid commands don't confirm each other)
            st = self.state.get(name, {})
            return (self.changed.get(name, {}).get("updated", 0) > max(before, sent)
                    and all(st.get(k) == v for k, v in wanted.items()))

        self.client.publish(f"{config.ZIGBEE_TOPIC}/{name}/set", json.dumps(payload))
        with self._lock:
            ok = self._updated.wait_for(answered, timeout=wait)
            return dict(self.state.get(name, {})) if ok else None

    # ---------------------------------------------------------------- reading
    def light_payload(self, dev, on=None, brightness=None, color=None, white=None, effect=None, speed=None):
        """What they asked for -> a Zigbee2MQTT command for this light (None + a reason if it can't)."""
        p = {}
        if on is not None:
            p["state"] = "ON" if on else "OFF"
        if brightness is not None:
            p["brightness"] = max(1, min(dev["light"].get("max", 254), round(int(brightness) / 100 * dev["light"].get("max", 254))))
            p.setdefault("state", "ON")
        if color:
            c = str(color).strip().lower()
            hexv = COLORS.get(c) or (c if c.startswith("#") and len(c) in (4, 7) else None)
            if c in WHITES or c == "white":
                white = white or ("neutral" if c == "white" else c)
            elif not hexv:
                return None, f"I don't know the color '{color}' (try a name like red or purple, or a #hex code)"
            elif not dev["light"].get("color"):
                return None, f"{dev['name']} can't change color"
            else:
                p["color"] = {"hex": hexv.upper()}
                p.setdefault("state", "ON")
        if white:
            lo, hi = dev["light"].get("mireds", (153, 500))
            p["color_temp"] = round(lo + (hi - lo) * WHITES.get(str(white).lower(), 0.5))
            p.setdefault("state", "ON")
        if effect:
            effects = dev["light"].get("effects", [])
            hit = next((e for e in effects if str(effect).lower().replace(" ", "") in e.lower()), None)
            if not hit:
                return None, f"{dev['name']} has no '{effect}' effect" + (f" (it has: {', '.join(effects[:8])})" if effects else "")
            p["effect"] = hit
            p.setdefault("state", "ON")
        if speed is not None:
            # effect_speed (Aqara LED Strip T1): the device itself reports 0 when no effect is active, so this only
            # makes sense together with (or right after) setting an effect - not invented, not a separate "mode".
            # The tool schema's minimum/maximum already refuses an out-of-range value before this code runs
            # (tools/validate.py); the clamp below is just a defensive second line, never the normal path.
            rng = dev["light"].get("effect_speed")
            if rng is None:
                return None, f"{dev['name']} doesn't support an adjustable effect speed"
            lo, hi = rng
            p["effect_speed"] = max(lo, min(hi, round(float(speed))))
            p.setdefault("state", "ON")
        return (p, "") if p else (None, "nothing to change was asked for")

    def temperature(self, c):
        return f"{c * 9 / 5 + 32:.0f}°F" if config.UNITS == "imperial" else f"{c:.1f}°C"

    def describe(self, name):
        dev, st = self.devices.get(name), self.state.get(name, {})
        if not dev:
            return f"{name}: unknown device"
        marks = self.changed.get(name, {})
        k = dev["kind"]
        if name in self.offline:
            return f"{name}: UNAVAILABLE (Zigbee2MQTT can't reach it right now; its last reading may be out of date)"
        if not st and k != "vibration":  # (a vibration sensor's history comes from the event log)
            return f"{name}: no reading yet since Jarvis started (unknown, don't guess)"
        last_open = self.last_event(name, "opened")
        if k == "contact":
            if "contact" not in st:
                text = "no reading yet"
            else:
                since = marks.get("closed" if st["contact"] else "opened")
                text = ("closed" if st["contact"] else "OPEN") + (f" (since {clock(since)})" if since else "")
                opened_at = marks.get("opened") or (last_open or {}).get("at")
                if st["contact"] and opened_at:
                    text += f", last opened {_ago(opened_at)}"
                    note = (last_open or {}).get("note")
                    text += f" ({note})" if note and last_open and abs(last_open["at"] - opened_at) < 2 else ""
        elif k == "climate":
            parts = [self.temperature(st["temperature"])] if "temperature" in st else []
            parts += [f"{st['humidity']:.0f}% humidity"] if "humidity" in st else []
            parts += [f"{st['pressure']:.0f} hPa"] if "pressure" in st else []
            text = ", ".join(parts) or "no reading yet"
        elif k == "vibration":  # (a vibration sensor stuck to one thing, e.g. the bed: not a room motion sensor)
            moved = marks.get("moved") or (self.last_event(name, "moved") or {}).get("at")
            text = (f"vibration sensor (on one object, NOT a room motion sensor): last movement {_ago(moved)}" if moved
                    else "vibration sensor (on one object, NOT a room motion sensor): no movement recorded in the last 48 hours")
        elif k == "presence":
            key = "presence" if "presence" in st else "occupancy"
            text = ("someone is there" if st.get(key) else "nobody detected") if key in st else "no reading yet"
        elif k in ("light", "switch"):
            on = st.get("state") == "ON"
            text = "on" if on else "off"
            if on and st.get("brightness") is not None:
                text += f", {round(st['brightness'] / dev['light'].get('max', 254) * 100)}% brightness"
            if on and st.get("color_mode") == "color_temp":
                text += ", white light"
            elif on and st.get("color_mode") in ("xy", "hs"):
                text += f", color {self.color_name(st)}"
            if on and st.get("effect") and st.get("color_mode") not in ("xy", "hs", "color_temp"):
                text += f", effect {st['effect']}"
        else:
            text = ", ".join(f"{k2}={v}" for k2, v in list(st.items())[:4]) or "no reading yet"
        updated = marks.get("updated")
        if k == "climate" and updated and time.time() - updated > 3 * 3600:
            text += f" (last reading {_ago(updated)}, may be out of date)"
        if dev["battery"] and isinstance(st.get("battery"), (int, float)) and st["battery"] <= 20:
            text += f" (battery low: {st['battery']}%)"
        return f"{name}: {text}"

    @staticmethod
    def color_name(st):
        """The nearest color name for a light's current xy color."""
        xy = st.get("color") or {}
        x, y = xy.get("x"), xy.get("y")
        if x is None or y is None:
            return "custom"
        ref = {"red": (0.69, 0.3), "orange": (0.6, 0.38), "yellow": (0.49, 0.47), "green": (0.2, 0.7), "cyan": (0.16, 0.33),
               "blue": (0.14, 0.1), "purple": (0.25, 0.1), "pink": (0.42, 0.22), "white": (0.33, 0.33)}
        return min(ref, key=lambda n: (ref[n][0] - x) ** 2 + (ref[n][1] - y) ** 2)

    def summary(self):
        with self._lock:
            names = sorted(self.devices, key=lambda n: ("contact presence climate vibration light switch other".split()
                                                        .index(self.devices[n]["kind"]), n))
        return [self.describe(n) for n in names]

    def snapshot(self):
        """For the dashboard."""
        with self._lock:
            devs = list(self.devices.values())
        out = []
        for d in devs:
            st, marks = self.state.get(d["name"], {}), self.changed.get(d["name"], {})
            item = {"name": d["name"], "kind": d["kind"], "model": d["model"], "summary": self.describe(d["name"]).split(": ", 1)[1],
                    "battery": st.get("battery") if d["battery"] else None, "updated": marks.get("updated")}
            if d["kind"] == "contact":
                item.update(open=st.get("contact") is False, opened=marks.get("opened"), closed=marks.get("closed"))
            elif d["kind"] == "climate":
                item.update(temperature=st.get("temperature"), humidity=st.get("humidity"),
                            temperature_text=self.temperature(st["temperature"]) if "temperature" in st else None)
            elif d["kind"] == "vibration":
                item.update(moved=marks.get("moved"))
            elif d["kind"] == "presence":
                item.update(present=bool(st.get("presence", st.get("occupancy"))))
            elif d["kind"] in ("light", "switch"):
                item.update(on=st.get("state") == "ON", color=bool(d["light"].get("color")),
                            brightness=round(st.get("brightness", 0) / d["light"].get("max", 254) * 100) if st.get("brightness") else 0,
                            effects=d["light"].get("effects", [])[:12])
            out.append(item)
        order = "contact presence climate vibration light switch other".split()
        return {"online": self.ready(), "devices": sorted(out, key=lambda i: (order.index(i["kind"]), i["name"]))}


hub = Hub()
