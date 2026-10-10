"""Room context: a read-only combination of what's already tracked elsewhere (FP1E presence, the bed vibration
sensor, the door, PC keyboard/mouse activity, time of day) into one inspectable room state, plus a separate, more
tentative "activity hint". Nothing here speaks, changes a light, or acts: it only answers "what does the room
probably look like right now", with explicit uncertainty, for something else (proactive.py, a future ability) to
decide what to do about later. No automation is wired to this module yet.

Hard rules (do not relax without re-reading why, each is from a real mistake this is built to avoid):
    - Occupancy is never identity. "Someone is there" is the only claim an occupancy signal supports.
    - The bed vibration sensor is a SUPPLEMENTARY, POSITIVE-ONLY signal: fresh vibration can suggest someone is at
      the bed; STALE or ABSENT vibration never suggests the bed is empty, and never feeds EMPTY/RoomState at all.
      It only ever raises ActivityHint.POSSIBLY_AT_BED, never lowers anything.
    - Inactivity (no PC input, no vibration) is never read as "asleep". The closest state is POSSIBLY_RESTING, and
      its own text says "possibly" and lists the evidence, never "asleep" or "in bed".
    - A stale or offline sensor reports UNKNOWN_OFFLINE / ActivityHint.UNKNOWN, never a guessed last-known value.
    - Conflicting signals (e.g. desk activity AND fresh bed vibration at once) report ActivityHint.AMBIGUOUS, never
      pick one arbitrarily.

Reuses, not reimplements: `room_agent.tools.zigbee.hub` (device state, the 48h event history, `.find(kind=...)` for
"the only device of that kind", `.offline`), `room_agent.triggers.idle_seconds()` (Windows keyboard/mouse idle time,
already used by the desk/break nudges) and `config.DESK_AWAY_MIN`. No new MQTT handling, no new polling loop, no new
on-disk state (this is all derivable from what `hub` and `triggers` already keep live).
"""

import time
from dataclasses import dataclass
from enum import Enum

from room_agent import config

# How long a device's last update may be before we stop trusting it (matches the general "is this still live"
# feel of the rest of the codebase; not a guess about the FP1E's own reporting interval).
PRESENCE_STALE_S = 5 * 60
# Raw presence must hold its current value this long before we treat it as the confirmed reading (the real
# zigbee_events.json log for bedroom_presence shows it flipping every 6-30s; this is not a hypothetical problem).
PRESENCE_DEBOUNCE_S = 45
# A vibration reading counts as "fresh" for this long (supplementary signal only; see module docstring).
BED_VIBRATION_FRESH_S = 3 * 60
# PC input more recent than this counts as "at the desk right now" (reuses config.DESK_AWAY_MIN for the opposite
# direction - "away" - so the two thresholds agree with each other instead of drifting apart).
DESK_ACTIVE_S = min(90.0, config.DESK_AWAY_MIN * 60)
# Same evening/night window triggers.on_bed() already uses for its one-shot "bed" moment, reused here for the
# POSSIBLY_RESTING hint so the two don't disagree about what "night" means.
REST_HOURS = (20, 4)  # 8 PM to 4 AM, wrapping past midnight


class RoomState(str, Enum):
    OCCUPIED = "occupied"                  # presence confirmed (debounced) true
    EMPTY = "empty"                         # presence confirmed (debounced) false
    RETURNING = "returning_to_room"         # door opened just before presence turned true after an absence
    UNKNOWN_OFFLINE = "unknown_offline"     # presence sensor missing, offline, or its last reading is stale


class ActivityHint(str, Enum):
    """Only meaningful when RoomState is OCCUPIED or RETURNING; always None otherwise."""
    POSSIBLY_AT_DESK = "possibly_at_desk"   # recent keyboard/mouse input at THIS PC (also covers Phase 2's
                                            # "possibly_working"); there are two real seating spots in the room and
                                            # only one is wired to a PC - this hint can only ever speak to that one.
    POSSIBLY_AT_BED = "possibly_at_bed"     # fresh bed-vibration sensor reading (supplementary, positive-only)
    POSSIBLY_RESTING = "possibly_resting"   # occupied, evening/night hours, no desk activity - NOT "asleep"
    AMBIGUOUS = "ambiguous"                 # desk activity and fresh bed vibration both true at once: can't pick
    UNKNOWN = "unknown"                     # occupied, no desk/bed/rest-hours signal - may simply be at the OTHER
                                            # seating spot (no sensor there at all): genuinely unknown, not a 3rd guess


@dataclass
class RoomContext:
    state: RoomState
    hint: ActivityHint | None
    why: str                 # short, human-readable evidence trail (for logs / a future dashboard card)
    presence_age_s: float | None      # seconds since the presence sensor's reading last changed (None: no data)
    bed_vibration_age_s: float | None  # seconds since the bed sensor last moved (None: never, this run)
    desk_idle_s: float | None         # seconds since the last keyboard/mouse input (None: not on Windows)


_debounced = {"occupied": None, "since": 0.0}  # our own confirmed reading, separate from the raw/flapping one


def _hub():
    from room_agent.tools.zigbee import hub

    return hub


def _presence_device():
    return _hub().find("", kind="presence")


def _bed_device():
    return _hub().find("", kind="vibration")


def _door_device():
    return _hub().find("", kind="contact")


def _raw_presence(now):
    """-> (value or None, age_s or None). None/None: no device, no reading, or the device is offline/stale."""
    h, dev = _hub(), _presence_device()
    if not dev:
        return None, None
    name = dev["name"]
    if name in h.offline:
        return None, None
    st = h.state.get(name, {})
    key = "presence" if "presence" in st else ("occupancy" if "occupancy" in st else None)
    if key is None:
        return None, None
    updated = h.changed.get(name, {}).get("updated", 0)
    if updated <= 0 or now - updated > PRESENCE_STALE_S:
        return None, None
    changed_at = h.changed.get(name, {}).get("presence", updated)
    return bool(st[key]), now - changed_at


def _debounced_presence(now):
    """Folds in PRESENCE_DEBOUNCE_S so a sensor that flips every few seconds doesn't flip RoomState just as often.
    -> (confirmed bool or None, age_s of the raw reading or None)."""
    raw, age = _raw_presence(now)
    if raw is None:
        return None, None
    if _debounced["occupied"] is None:
        _debounced["occupied"], _debounced["since"] = raw, now
        return (raw if age is None or age >= PRESENCE_DEBOUNCE_S else None), age
    if raw != _debounced["occupied"]:
        if age is not None and age >= PRESENCE_DEBOUNCE_S:
            _debounced["occupied"], _debounced["since"] = raw, now  # held long enough: accept the flip
        # else: too recent a change to trust yet - keep reporting the previous confirmed value
    return _debounced["occupied"], age


def _bed_vibration_age(now):
    dev = _bed_device()
    if not dev:
        return None
    moved = _hub().changed.get(dev["name"], {}).get("moved")
    return (now - moved) if moved else None


def _desk_idle(now=None):
    from room_agent import triggers

    return triggers.idle_seconds()  # None off Windows; reused as-is, no duplicate polling


def _door_opened_recently(now):
    """Door opened within PRESENCE_DEBOUNCE_S of `now` (regardless of what the debounced presence value says yet)."""
    dev = _door_device()
    if not dev:
        return False
    last_open = _hub().last_event(dev["name"], "opened")
    return bool(last_open) and now - last_open["at"] <= PRESENCE_DEBOUNCE_S


def _in_rest_hours(now):
    hour = time.localtime(now).tm_hour
    start, end = REST_HOURS
    return hour >= start or hour < end


def current(now=None):
    """-> RoomContext. Pure read: no event is recorded, nothing is spoken, no device is commanded."""
    now = now or time.time()
    prior_occupied = _debounced["occupied"]  # snapshot BEFORE this read can change it (needed for RETURNING below)
    raw, _ = _raw_presence(now)
    bed_age = _bed_vibration_age(now)
    idle = _desk_idle(now)
    desk_active = idle is not None and idle < DESK_ACTIVE_S
    bed_fresh = bed_age is not None and bed_age < BED_VIBRATION_FRESH_S

    # RETURNING is the one case allowed to skip the normal debounce wait: a door opening and the presence sensor
    # agreeing within the same short window is two independent sensors corroborating each other, which is stronger
    # evidence than one sensor's raw reading alone (the thing the debounce elsewhere exists to distrust).
    returning = raw is True and prior_occupied in (False, None) and _door_opened_recently(now)
    if returning:
        _debounced["occupied"], _debounced["since"] = True, now
        occupied, presence_age = True, 0.0
    else:
        occupied, presence_age = _debounced_presence(now)

    if occupied is None:
        return RoomContext(RoomState.UNKNOWN_OFFLINE, None,
                            "presence sensor missing, offline, or no reading in the last "
                            f"{PRESENCE_STALE_S // 60} min", presence_age, bed_age, idle)
    if returning:
        return RoomContext(RoomState.RETURNING, None, "door opened just before presence was (re)confirmed",
                            presence_age, bed_age, idle)
    if not occupied:
        # Vibration is positive-only: its absence here is NOT evidence for EMPTY, it simply isn't consulted.
        return RoomContext(RoomState.EMPTY, None, "presence sensor confirms nobody detected", presence_age,
                            bed_age, idle)
    if desk_active and bed_fresh:
        return RoomContext(RoomState.OCCUPIED, ActivityHint.AMBIGUOUS,
                            f"desk activity {idle:.0f}s ago AND bed vibration {bed_age:.0f}s ago: can't tell which",
                            presence_age, bed_age, idle)
    if desk_active:
        return RoomContext(RoomState.OCCUPIED, ActivityHint.POSSIBLY_AT_DESK,
                            f"presence confirmed, keyboard/mouse input {idle:.0f}s ago", presence_age, bed_age, idle)
    if bed_fresh:
        return RoomContext(RoomState.OCCUPIED, ActivityHint.POSSIBLY_AT_BED,
                            f"presence confirmed, bed sensor moved {bed_age:.0f}s ago (supplementary signal only)",
                            presence_age, bed_age, idle)
    if _in_rest_hours(now):
        return RoomContext(RoomState.OCCUPIED, ActivityHint.POSSIBLY_RESTING,
                            "presence confirmed, within rest hours, no desk activity - NOT a claim of sleep",
                            presence_age, bed_age, idle)
    return RoomContext(RoomState.OCCUPIED, ActivityHint.UNKNOWN,
                        "presence confirmed, no desk or bed signal, outside rest hours", presence_age, bed_age, idle)


def reset():
    """Test/debug only: clears the debounce memory so a fresh reading is accepted immediately."""
    _debounced["occupied"], _debounced["since"] = None, 0.0
