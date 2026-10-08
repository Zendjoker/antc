# Missions: safety model and verification status

Code: `room_agent/missions/`. Voice tools: `room_agent/abilities/missions.py`. Dashboard: the **Missions** page.

**Status: nothing in the missions feature has been run.** It has been syntax-checked (`py_compile`, `node --check`) and
reviewed statically. No test, live request, model call, Places call, coding-worker run or dashboard load has happened.

## 1. Stopping (cancellation and timeouts)
- Each run of a step gets a token (`runctx.py`). Timeout, pause, stop and the emergency stop (Ctrl+Alt+J) cancel it.
- Every side effect checks the token first:
  - HTTP requests (before sending, between chunks)
  - web searches
  - paid calls
  - database writes (`GuardedStore`)
  - file writes and site swaps
  - the coding worker's processes, whose whole tree is killed through a Job Object
- A run's result is recorded only if that run still owns the step (`steps.run_id`). A stopped or timed-out run can't
  mark a step done, add steps, or write anything afterwards.
- Limit: Python can't kill a thread. A run blocked inside one HTTP request finishes that request (at most its timeout:
  20–90 s), then stops at the next checkpoint. The dashboard shows these as "stopped step still finishing a request".
- Limit: there's a tiny window between a token check and the write it guards.

## 2. Coding worker
- Default: `CODER_BACKEND=auto`, which means `anthropic`. One model call, no tools, no code execution.
  - Jarvis validates the returned files (flat names, text types, size), then the content rules.
  - Only then does the site change, through an atomic swap.
- `claude_cli` is used only if you set it explicitly. It runs:
  - on a throwaway copy, never the real site
  - with file tools only (`--allowedTools Read,Edit,Write,Glob,Grep`, Bash / web disallowed)
  - with no MCP servers (`--strict-mcp-config` and an empty config)
  - with an allowlisted environment: no keys except `ANTHROPIC_API_KEY`, and home / AppData / TEMP pointing into the
    sandbox, so your Claude settings, memory, credentials and MCP servers aren't visible
  - inside a Windows Job Object: at most 8 processes, 2 GB, no clipboard / desktop / shutdown access, killed on stop
  - fail-closed if the job can't be applied
- Not isolated:
  - The CLI's network access (it needs the Anthropic API; per-process firewall rules need admin).
  - It runs as your Windows user. The sandbox restricts what it's pointed at and how many processes it can start, but
    not filesystem permissions. An AppContainer or low-integrity token isn't implemented.
- Tripwire: Jarvis's own source files are fingerprinted before and after each edit. A change blocks the edit and is
  logged.

## 3. Budget
- Every paid call is **reserved** atomically before it's made (spent + reserved + estimate ≤ budget, in one SQL
  UPDATE), then **settled** afterwards.
  - A failure where the provider may still have charged (timeout, dropped connection, server error, no usage reported)
    is counted at the **full estimate** ("uncertain").
  - A reservation is released only if the request was never sent, or was rejected with a 4xx.
  - Reservations open at a crash are counted at full estimate at the next start.
- Estimates are upper bounds:
  - input at about 3 characters per token, plus 200 tokens of overhead
  - output at its full cap (×3 for GPT-5 thinking)
  - unknown models at the expensive default price
- Mission model calls use `max_retries=0`, so an SDK can't silently pay twice.
- The day's model budget (`DAILY_BUDGET_USD`) is checked too.
- Ledger: the `charges` table, shown on the dashboard.
- Not verified:
  - Google Places prices (`PLACES_COST_PER_REQUEST`, `PLACES_DETAILS_COST_PER_REQUEST` are your estimates)
  - whether Claude Code's JSON output reports `total_cost_usd` (if it doesn't, each edit counts as `CODER_MAX_USD`)
  - Claude Code and Places costs don't enter Jarvis's daily-spend file, only the mission ledger

## 4. Recovery after a crash or restart (`engine.load()`)
- Missions come back **interrupted**. Resume by voice or from the dashboard.
- A running step that's safe to repeat goes back to pending. Any other running step is marked failed and is never
  repeated automatically.
- Re-running discovery reuses the businesses already found, so there's no second paid search.
- Leads are deduplicated by key, then place id, then phone, then name + address, under one lock.
- Research rows are keyed per mission + lead, so a retry replaces its row.
- A draft is updated in place. Each draft gets at most one approval, ever.
- Gmail approvals are two-phase: pending → executing (atomic claim) → done.
  - A crash during the Gmail call leaves the approval **unknown**. It's never retried automatically; check your Drafts,
    then "retry".
- Demo sites change only by staging, checking, then swapping folders (`sites/.versions/` keeps every old version).
  - A half-done swap is finished or rolled back at startup.

## 5. Voice routing
- Every state-changing mission tool is intent-gated: your own words must mention the mission, demo, site or approval.
  - So "pause" / "stop" / "resume" (music, timers) and "find restaurants near me" don't reach mission tools.
  - Text inside emails or web pages can't trigger them either.
- More budget needs `raise_mission_budget`, which always asks. A budget you didn't say is ignored when a mission starts.
- Approvals are SENSITIVE: Jarvis always asks first.
- Names and page text from the web are shortened and flattened before the model reads them.
- Colour and layout changes are free rebuilds; other edits are paid coding-worker runs.

## 6. Demo sites
- Three layouts (editorial / modern / bold). Palettes are derived from one accent colour and checked to WCAG AA 4.5:1.
- Decoration (SVG art, monogram) is generated locally. System fonts only; no external resources (a CSP is in the page
  and on the preview server).
- Facts shown:
  - only stored, sourced fields, never Google content
  - opening hours parsed from OpenStreetMap's syntax where possible, otherwise shown raw, never guessed
- **Owner content**: `<mission>/owner-content/<site folder>/owner.json` holds tagline, about, menu or services (with
  prices) and photos (only with `"rights_confirmed": true`). It's the only source of prices or photos.
- Every version is checked:
  - banner, noindex, viewport
  - no reviews / ratings / prices except the owner's
  - no scripts or external resources
  - no missing files, flat folder
  - contrast

## 7. Google Places
- Policy, checked 2026-10-08 (developers.google.com/maps/documentation/places/web-service/policies):
  - Don't pre-fetch, cache or store Places content beyond the allowed exceptions.
  - Place IDs may be stored indefinitely.
  - Data shown without a Google Map must be attributed "Google Maps".
- What Jarvis does:
  - Places content lives **in memory only** (`PLACES_MEMORY_TTL_S`, default 6 h). It's never written to the database,
    files, reports, exports, sites or drafts.
  - Stored: the place id, plus fields **confirmed by a non-Google source** (the same business on OpenStreetMap, or its
    own website showing that phone / address / name). `extra.verified_by` records which source.
  - Unconfirmed Google values are scrubbed from stored research text.
  - Demos and outreach are never built for a business only Google knows about.
  - After the memory copy expires, it's re-fetched with Place Details (paid, budgeted).
  - Shown live on the dashboard, labelled "Google Maps".
  - `purge_places_content()` removes anything an older version stored. It runs at every start.
- Not handled:
  - EEA-specific terms (if your Google billing address is in the EEA)
  - refreshing place ids older than 12 months
  - the Google Maps logo (the text attribution is used)

## Unverified, all of it
Nothing has run, including:
- the Windows Job Object and NtResumeProcess calls
- the Claude Code flags
- the Overpass / Nominatim / Places request formats
- the dashboard page
- the Gmail draft path
- the atomic folder swaps on Windows (with Explorer or the preview server holding files)
- the opening-hours parser
- the contrast maths
- every recovery path
