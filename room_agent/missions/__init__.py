"""Missions: long-running, checkpointed workflows that run in the background on this PC (room_agent/missions/).

    engine.py      the reusable mission engine: workflows are lists of steps (with dependencies) run by code, one at a
                   time, checkpointed to SQLite after every step, resumable after a restart, with retry limits, pause /
                   resume / stop, the emergency stop, and a hard per-mission budget. No open-ended loop of model calls.
    store.py       SQLite: missions, steps, events, leads (with research history), projects, outreach drafts, approvals
    net.py         polite HTTP: identifying User-Agent, per-host rate limits, robots.txt, timeouts, public addresses only
    meter.py       per-mission cost accounting (model tokens + paid API requests) and the budget check before spending
    llm.py         the few model calls a mission may make (light / strong tier), always through the meter

    business.py    the first workflow: find local businesses with no / weak websites, research them, rank them, build demo
                   sites, prepare outreach drafts for approval
    discovery.py   finding businesses: OpenStreetMap (free) and Google Places (optional, needs a key)
    websites.py    finding the official site and judging it (reachability, mobile, contact info, ordering, ...), with an
                   explainable opportunity score
    sitegen.py     generating a demo website (static HTML/CSS, grounded in the verified facts; sample content labeled)
    coder.py       the coding worker for iterative edits (Claude Code CLI or an API model), confined to the project folder
    outreach.py    email drafts, call talking points, proposals; external actions only after an explicit approval
    preview.py     a local-only preview server for the generated sites

Generated sites and mission data live under MISSIONS_DIR (outside this repository).
"""
