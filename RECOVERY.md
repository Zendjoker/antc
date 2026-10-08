# Missions: crash recovery, idempotency and uncertain outcomes

Code: `room_agent/missions/` (engine, store, meter, coder, outreach). Tests: `tests/test_recovery.py`. That suite kills a
real child process at 10 named crash points (`missions/crashpoints.py`), restarts and checks the results.

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
  - Kinds: `paid_call` (one per charge), `site_swap`, `gmail_draft`.
  - States: running, completed, failed, uncertain.
  - `idem_key` is the operation's logical identity, for example `edit:<mission>:<step>:model`.
  - No keys or tokens are stored.
- **charges:** the budget ledger, one-to-one with the paid-call operations.

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
| Edit whose model answer was lost | step "uncertain" | Possibly billed, not applied | Ask for the edit again (costs again) |
| Gmail draft cut off | approval "unknown" | The draft may exist | Say "retry" (see below) |
| Step interrupted with no reconcile rule | step "uncertain" | Its effect is unknown | Check the result, then add the work again if needed |

**Gmail reconciliation:**
1. Each draft is created with a unique `Message-ID`, recorded **before** Gmail is called.
2. "Retry" first searches your drafts for that Message-ID (`rfc822msgid:`).
   - **Found:** the approval is recorded as done; no second draft.
   - **Gmail answers "not found":** it's created once.
   - **Gmail can't be asked:** nothing is done.

## Limits (no false guarantees)
- **Exactly-once isn't possible for these providers.** Jarvis relies on no provider idempotency keys: none of the
  endpoints it uses (Anthropic Messages, OpenAI Chat Completions, Google Places, Gmail drafts) gives a deduplication
  guarantee it could verify. What's used instead:
  - **paid calls:** at most one automatic attempt per idempotency key (a lost answer is counted, not retried)
  - **Gmail:** reconciliation by Message-ID
  - **Places discovery:** page checkpoints; content is never cached, per Google's terms
- **Places Details** after a restart is fetched again, and paid again: Google doesn't allow storing its content.
- **Gmail search lag:** a draft created moments before a crash might not be searchable yet, so a very quick retry could
  create a second draft. Wait a minute before "retry".
- **Lost answers cost twice if repeated.** A model call answered just before a crash, with its answer lost, may have
  been billed. Asking again pays again.
- **Not crash-tested:** the Claude Code worker (`claude_cli`) stores no reusable result (its output is files in the
  sandbox), so an interrupted CLI edit is reconciled only through the site-swap record.
- **Assumes no external edits.** A swap's "applied?" check compares the live site's content hash with the recorded new
  version. A site edited by hand in between would be seen as "not applied".
- **Recovery runs at startup only:** a single Jarvis process (`jarvis.lock`) is assumed.
- **Not verified against the live service:** the real Gmail API's `rfc822msgid:` search on drafts. It was tested with
  a fake.
