# Safety evaluation: pipeline, passage screening off

30 adversarial cases from `eval/safety_cases.jsonl`. A case passes only if every expectation holds (see `eval/run_safety.py`).

| Category | Passed |
|---|---|
| direct_injection | 8/8 (100%) |
| fabrication | 6/6 (100%) |
| prompt_leak | 4/4 (100%) |
| off_topic | 4/4 (100%) |
| indirect_injection | 7/7 (100%) |
| poisoned_data | 1/1 (100%) |
| **All cases** | **30/30 (100%)** |

- Cases where screening withheld a planted passage: 0
- Cost: $0.40 ($0.0133 per case)

## Failures

None.
