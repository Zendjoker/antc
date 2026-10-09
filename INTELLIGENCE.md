# General intelligence (V2): understanding, plan checks, recovery, supervision, coding, experience

Code: `cognition/understand.py`, `actions/planning.py`, `actions/recovery.py`, `actions/supervisor.py`,
`computer/coding.py` + `abilities/coding.py`, `cognition/experience_v2.py`. Tests: `tests/test_v2_intelligence.py`.
Benchmarks: `tests/universal_benchmark.py`, `tests/understanding_eval.py` (results in `tests/benchmarks/`).
No second agent loop: everything runs inside `run_task` (actions/tasks.py) and the existing executor.

## 1. Understanding (`GOAL_UNDERSTANDING=1`)
1. The conversational model stays the primary interpreter (its tool calls; `update_goal` now also takes capabilities,
   constraints, permissions, deliverables and missing information).
2. Code reads the user's own words, with no model call:
   - capability families
   - explicit limits ("don't send it", "don't touch the text files", "only the PNG files", "keep the screenshots",
     "don't use Google", "at most $2")
   - implied permissions
   - what must be asked ("send it to him")
3. Their explicit limits are enforced on every state-changing call, even on a quick turn without a tracked goal.
4. A limit the model proposes is kept only if its words appear in the request. Contradictions are listed, not resolved.
5. `GOAL_VERIFIER=1` (off by default, paid): a cheap model re-reads only ambiguous requests. It may add questions and
   grounded limits, never remove anything.

## 2. Plan checks (`TASK_CONTRACTS=1`)
1. Every step gets a contract: family, inputs, expected output, verification, cost, risk, retry policy, permission,
   resource.
2. Before anything runs, the plan is:
   - **rejected** for: a tool that doesn't exist, a dependency on a missing step, a cycle, or a state-changing step
     whose outcome nothing could verify
   - **sent back with a question** for: missing information, a missing required input, or a file step that would act
     on "whatever is selected"
   - **trimmed:** a step their words rule out, or that needs a permission they never implied, is left out and reported
   - **reordered:** a step that needs what a later step creates waits for it
3. Each step gets its family's default check, run by code after the tool says OK:
   - a file on disk, or a folder that exists
   - research with sources, and citations that point at real sources
   - a draft addressed to the named recipient

## 3. Recovery (`TASK_RECOVERY=1`, at most `TASK_MAX_RECOVERIES` per task, one per step)
- **Weather service down:** a web search for the weather instead.
- **Wrong or dead page:** a search restricted to the same site, then that page is opened and re-checked.
- **File not found:** look it up by name, then read it.
- **Research found nothing:** research again, deeper.
- **Folder missing:** make the folder their request named.
- Never recovered: an unknown outcome, a refusal (by their words, a permission or them), or a cancelled task.
  Alternatives go through the same executor and approvals.

## 4. Supervision (`TASK_SUPERVISOR=1`, deterministic)
- An exact repeat of a completed step is not run again.
- Over `TASK_MAX_USD` spent: no further step starts.
- A stalled step, a repeated identical failure, and "said OK but the check failed" are recorded.
- An output gone by the end means the task isn't reported as done.

## 5. Coding (`CODING=1`; the fixer is `CODING_FIXER=none` by default)
1. **`run_tests`:** a sandbox copy, only `python -m unittest` / `python -m pytest`, a Job Object (process, memory and UI
   limits), a minimal environment, a timeout, cancellation. Free; the project isn't touched.
2. **`fix_code`:** the fixer proposes changes, and they are verified by the tests in a sandbox copy (at most 2 attempts).
   Test files are protected. Nothing live changes.
3. **`apply_code_fix`:** always asks first (SENSITIVE).
   - It refuses if the files changed since the fix was made.
   - It backs up first, re-runs the tests on the live project, and rolls back if they fail.
   - Undo restores the backup.
4. **Boundaries:** only projects inside the user's own folders (or `CODING_ROOTS`), never Jarvis's own code.
   - Not isolated from the network.

## 6. Experience (`TASK_EXPERIENCE=1`, in experience.db)
- **Verified procedures:** a plan whose every step was checked. Offered to the model as a hint.
- **Failure patterns:** which alternative worked for which failure; recovery tries that one first.
- **Expiry and conflicts:** entries expire after `EXPERIENCE_TTL_DAYS`. A procedure that fails more than it works isn't
  offered.
- **Stored:** tool names, outcomes, failure kinds and the user's own words. Never content from pages, files or emails.
- **Inspect / correct:** `list_task_knowledge`, `forget_task_knowledge`.

## 7. Optional (off by default) and visibility
- `ROUTER_ESCALATION=1` (costs more): a request needing 3+ capability families, coding plus other work, or 2+ failures
  in the current goal goes to the smart model, with the reason logged. Measured only offline (no model calls here).
- The dashboard's task list shows:
  - each step's verification
  - steps left out, with the reason
  - recoveries and supervisor findings

  Names, states and reasons only: no arguments or results.

## Limits
- Benchmarks are offline, with fakes and a fixed model plan, written by the same author as the code. They measure what
  the code adds around a model, not the model's own judgement.
- The understanding layer uses regular expressions: English only, and narrow phrasings will slip through. It cross-checks
  the model; it doesn't replace it.
- Not done: parallel task steps, network isolation for
  test runs, recovery for tools other than the five above.
