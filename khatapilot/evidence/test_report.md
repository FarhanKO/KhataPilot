# Test report

`python -m pytest` in `mock` mode (no API key, no network), run 2026-11-17T16:53:17.118983+06:00: **955 of 955 passed** (100.0%), 0 failed, 0 skipped, 395.2 s of test time. The JUnit XML is [test_report.xml](test_report.xml). Tests against the real Claude API are opt-in (`pytest -m live`) and not part of this run.

| Suite | Passed | Failed | Skipped |
|---|---:|---:|---:|
| Backend unit tests (parsers, guard rules, tiers, PIN, audit chain, forecast, credit, loans) | 582 | 0 | 0 |
| End-to-end scenarios through the API (mock LLM, real sandbox) | 163 | 0 | 0 |
| Architecture rules (the agent cannot reach executors, the DB or the key) | 15 | 0 | 0 |
| Sandbox provider (signature checks, idempotency, freeze) | 25 | 0 | 0 |
| Synthetic data generator | 10 | 0 | 0 |
| Red-team suite (one test per attack and per benign request) | 160 | 0 | 0 |

## Failures

None.
