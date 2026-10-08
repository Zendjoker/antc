# Missions: crash recovery, idempotency and uncertain outcomes

Code: `room_agent/missions/` (engine, store, meter, coder, outreach, sitegen, ownership). Tests: `tests/test_recovery.py`
and `tests/test_hardening.py`. They kill a real child process at named crash points (`missions/crashpoints.py`, active
only with `JARVIS_TEST=1`), restart and check the results. All providers are fakes.

## What is guaranteed (and tested)
- **No silently lost work.**
  - A step's completion, the follow-up steps it creates and the steps it makes unnecessary are **one transaction**
    (`store.complete_step`).
  - A crash leaves either all of it or none of it, and "none" means the step runs again.
- **No step reported done unless its effect is in place.**
  - Each step attempt has a run id. Only that run can record the outcome.
  - After a restart, interrupted steps are decided from records and files (below), never guessed.
- **No duplicated paid call when a completed result exists.** A paid call with an idempotency key stores its result in
  the same transaction that settles its charge, and a retry reuses that result without calling the provider.
- **No double charge.**
  - Every paid call is reserved before it's sent and settled exactly once.
  - Its operation record is created and closed in the same transactions as its charge, so the two can't disagree.
- **No uncertain request assumed free.** A reservation still open at a crash is settled at the **full estimate**, marked
  "uncertain".
- **Cancellation doesn't undo committed work.**
  - A stopped mission keeps its completed steps.
  - New or queued work can't restart it, and a step can't be claimed once its mission isn't running.
- **Every mission state change is logged** (`transitions`: from, to, reason, time), in the same transaction as the change.

## Records
- **steps:** each has a stable id + key, and each attempt a run id. States: pending, running, completed, failed,
  blocked, cancelled, skipped, **uncertain** (interrupted with an unknown effect; it blocks steps that depend on it).
- **operations:** every side effect, with a stable id (uuid).
  - Kinds: `paid_call` (one per charge), `edit_result` (a checked edit, stored before it's applied), `site_swap`,
    `gmail_draft`.
  - States: running, completed, failed, uncertain.
  - `idem_key` is the operation's logical identity, for example `edit:<mission>:<step>:model`.
  - No keys or tokens are stored.
- **charges:** the budget ledger, one-to-one with the paid-call operations. `basis` says where the amount comes from:
  - `provider_usage`: the provider's token counts x the price table
  - `provider_reported`: the cost the provider reported (Claude Code)
  - `configured_price`: a configured per-request price (Places)
  - `estimate`: **a local estimate** (outcome uncertain: the provider may or may not have billed it)
  - `none`: not billed
- **owner:** the fencing token of the process that owns the missions (below).

## At startup (`engine.load()`), in this order
1. **Open reservations:** settled as uncertain at their full estimate, and their operations become uncertain.
2. **Gmail approvals left "executing":** become **unknown**.
3. **Files:** a half-done site swap is finished (the new version was complete and checked) or undone. Unfinished
   staging folders are deleted.
4. **Interrupted steps:**
   - A step safe to repeat goes back to pending.
   - Any other step is reconciled by its workflow (`reconcile`):
     - **site edit, swap recorded as done, or the live site matches the recorded hash of the new version:**
       completed
     - **paid result stored, not applied:** pending; the result is applied without paying again
     - **nothing sent yet:** pending
     - **model call outcome unknown** (crash between its answer and its record): **uncertain**, not repeated
       automatically; asking again costs again
     - **other kinds:** uncertain
5. **Missions that were running:** "interrupted", resumed only by you.

## Uncertain outcomes, and what to do
| Outcome | Shown as | What it means | What you do |
|---|---|---|---|
| Paid call cut off (no answer recorded) | charge "uncertain" | The provider may have billed it; it's counted in full | Nothing. Compare with the provider's usage page if you want the exact figure |
| Edit whose model / Claude Code answer was lost | step "uncertain" | Possibly billed, not applied | Retry (costs again) or accept |
| Gmail draft cut off | approval "unknown" | The draft may exist | Check (read-only), or mark it created / not created |
| Site edited by hand during an edit | conflict | Your edits were kept; Jarvis's version set aside | Keep my edits, or use Jarvis's version |
| Step interrupted with no reconcile rule | step "uncertain" | Its effect is unknown | Check the result, then retry or accept |

Where: the dashboard's mission page ("Needs your decision", "Operations"), or by voice ("check the mission's problems").

## One owner process
1. `ownership.acquire()` takes an OS lock on `<MISSIONS_DB>.owner` (Windows `msvcrt.locking`, elsewhere `fcntl.flock`).
2. The OS drops the lock when the process dies, however it dies, so a crash never leaves a lock that blocks.
   - Windows releases a killed process's lock a moment later, so startup waits up to 15 s for it.
3. Each new owner writes a new **fencing token**. A step can only be claimed with the current token, so a process that
   lost ownership can't start mission work.
4. A second Jarvis on the same database gets no startup recovery, no runner and no new missions (`NotOwner`).

## Gmail drafts
1. Before Gmail is called, the operation is recorded with a unique `Message-ID` and a marker (the operation id).
   Both go into the draft (`Message-ID`, `X-Jarvis-Operation`).
2. If the answer is lost, the approval is **unknown**. Approving again is refused while it's unknown.
3. **Check** (read-only, never creates):
   - a `rfc822msgid:` search, then a scan of the drafts' headers (finds it even if the search lags or Gmail rewrote
     the Message-ID)
   - **found:** recorded as done
   - **absent:** only if the scan read **every** draft **and** the attempt is at least 10 min old. Then it goes back to
     "waiting for approval", and creating it needs a new, explicit approve.
   - otherwise: "wait" (nothing changes)
4. **Manual resolution**, after you looked in Gmail: "it's in Gmail" (done) or "it's not in Gmail" (back to waiting
   for approval). "Not in Gmail" is refused if Gmail's own record shows it was created.
5. Nothing in this path sends an email.

## Website edits (Anthropic or Claude Code)
1. Generation is paid once per idempotency key (`edit:<mission>:<step>:model` or `:cli`).
2. The checked result is stored (`:result`) **before** it's applied. A crash after that re-applies it without paying.
3. The swap is recorded (`:swap`) with the new version's hash and the hash of the version it replaces.
4. After a crash:
   - **result stored, not applied:** applied, no new generation
   - **generation done, result not stored:** **uncertain**, charged at its reservation (`estimate`), not re-run
   - **swap done but not recorded:** completed (the live site matches the new hash)
   - **half-done swap:** finished at startup

## Hand edits
1. Jarvis records the fingerprint of every version it puts live (`sites/.versions/<site>/state.json`).
2. If the live site no longer matches, a person changed it, and Jarvis never overwrites it:
   - an edit, rebuild or redesign sets its new version aside in `sites/.pending/` and reports a **conflict**
   - an interrupted swap over a hand-edited site puts the person's version back
   - a recovered edit whose site was changed by hand becomes **uncertain** and isn't re-applied
3. Resolve with **keep my edits** (they become the baseline; Jarvis's version is deleted) or **use Jarvis's version**
   (your version is kept in `.versions`).

## Reconcile without restarting
- `engine.reconcile_now()` (dashboard "Check now", voice "check the mission's problems") re-runs only read-only checks
  of files and Gmail. It never re-runs a step or a paid call, and never creates a draft.
- `engine.resolve()` carries out your choice: retry / accept a step, check / mark a draft, keep mine / use Jarvis's.
  - Retry releases the uncertain operation's key (its record and charge stay) and runs the step once more. It may cost
    again.
  - Sending-type actions still need their own approval.

## Limits (no false guarantees)
- **No exactly-once for these providers.** None of them (Anthropic Messages, OpenAI Chat Completions, Claude Code,
  Google Places, Gmail drafts) offers an idempotency key Jarvis could verify. What's used instead:
  - **paid calls:** at most one automatic attempt per key; a lost answer is counted, not retried
  - **Gmail:** write-ahead identifiers + reconciliation; a draft is created again only after an explicit approve
  - **Places discovery:** page checkpoints; content is never cached, per Google's terms
- **Places Details** after a restart is fetched again, and paid again: Google doesn't allow storing its content.
- **Lost answers cost twice if retried.** A call answered just before a crash may have been billed, and a retry pays
  again.
- **"Absent" in Gmail is an inference.** It relies on a complete drafts scan plus a 10-minute wait. A draft you deleted
  in Gmail also reads as absent; approving it again then creates a new one.
- **Not verified against the real Gmail API:**
  - whether `rfc822msgid:` finds drafts
  - how long its index lags
  - whether Gmail keeps the Message-ID and the `X-Jarvis-Operation` header

  These were tested with fakes, including the real `GmailService` code on a fake HTTP layer. To check for real (only
  once approved; it creates one draft to yourself, never sends it, then deletes it):
  `.venv\Scripts\python -m tests.gmail_live_check --yes`
- **Claude Code itself is faked in the tests.** Its process layer (`sandbox.run`) is replaced, and the real CLI's JSON
  output (`total_cost_usd`) is assumed. A CLI run without a reported cost is charged at its full reservation.
- **Hand edits are detected by content.** A hand edit that exactly restores Jarvis's last version isn't a conflict.
- **Ownership is per database file on one machine.** A database on a network share used by two machines isn't
  supported.
