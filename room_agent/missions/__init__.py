"""Missions: long-running, checkpointed workflows that run in the background on this PC (room_agent/missions/).

    engine.py      the reusable mission engine: workflows are lists of steps (with dependencies) run by code, one at a
                   time, checkpointed to SQLite after every step, resumable after a restart, with retry limits, pause /
                   resume / stop, the emergency stop, and a hard per-mission budget. No open-ended loop of model calls.
    runctx.py      enforceable cancellation: a token per step run, checked at every side effect
    store.py       SQLite: missions, steps, events, leads (with research history), projects, outreach drafts, approvals,
                   the budget ledger; GuardedStore (writes refused once a step is cancelled)
    net.py         polite HTTP: identifying User-Agent, per-host rate limits, robots.txt, timeouts, public addresses only
    meter.py       the budget: every paid call reserved (atomically) before, settled after; uncertain charges counted
    llm.py         the few model calls a mission may make (light / strong tier), always through the meter, no retries

    business.py    the first workflow: find local businesses with no / weak websites, research them, rank them, build demo
                   sites, prepare outreach drafts for approval
    discovery.py   finding businesses: OpenStreetMap (free) and Google Places (optional, needs a key)
    places.py      Google Places within its terms: content in memory only, place ids stored, "Google Maps" attribution
    websites.py    finding the official site and judging it (reachability, mobile, contact info, ...), with an
                   explainable opportunity score
    design.py      layouts, contrast-checked palettes, generated SVG art, opening-hours parsing
    sitegen.py     demo websites (static HTML/CSS from sourced facts + owner content), staged, checked, swapped atomically
    coder.py       the coding worker for free-form edits, on a sandboxed copy (sandbox.py)
    sandbox.py     the worker's restrictions: throwaway copy, allowlisted environment, Windows Job Object
    outreach.py    email drafts, call talking points, proposals; approvals are two-phase and only create Gmail drafts
    preview.py     a local-only preview server for the generated sites

Generated sites and mission data live under MISSIONS_DIR (outside this repository). See MISSIONS.md for what is and
isn't verified.
"""
