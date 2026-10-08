# Routing benchmark

40 cases, offline (`python -m tests.routing_benchmark`). Measures whether the right tool is OFFERED to the model, the tool-schema tokens per call, and dangerous tools offered when a page carries injected instructions or the request is unsupported. Which tool a model then picks needs a model run (tests/tool_choice_live.py, with approval).

| Router | Right tool offered | Avg tools offered | Avg schema tokens / call | Dangerous offered (injection / unsupported) |
|---|---|---|---|---|
| current | 37/37 | 24.6 | 3249 | 3 |
| all | 37/37 | 126.0 | 15810 | 50 |
| semantic-12 | 37/37 | 22.2 | 2904 | 3 |

## Misses (current router)

None.

## Misses (semantic-12)

None.
