# Reliability: before / after

Same fault-injected scenarios, scored by the end state (files, the simulated world), offline (`python -m tests.reliability_compare`). 'Before' = steps run one by one through the existing per-request Plan (the model calling tools itself); 'after' = the same steps as one task (actions/tasks.py). Execution machinery only: a real model's planning isn't part of this.

| Scenario | Before | After |
|---|---|---|
| a temporary network error on a read step | **wrong** | correct |
| a step that hangs (should end in ~1 s, not claimed) | **wrong** | correct |
| a failed step: its dependent must not run, an independent one must | **wrong** | correct |
| Jarvis restarts after step 2 of 4: the rest gets done, step 2 not repeated | **wrong** | correct |
| an ambiguous result: not claimed, not repeated when asked again | correct | correct |
| the tool says OK but the file lacks what was asked: caught | **wrong** | correct |
| the PC-wide stop during step 2: step 3 never runs | correct | correct |

**Correct end state: before 2/7, after 7/7.**
