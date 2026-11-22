# Test report

`python -m pytest` in `mock` mode (no API key, no network), run 2026-11-22T22:18:39.037277+06:00: **1064 of 1064 passed** (100.0%), 0 failed, 0 skipped, 300.2 s of test time. The JUnit XML is [test_report.xml](test_report.xml). Tests against the real Claude API are opt-in (`pytest -m live`) and not part of this run.

| Suite | Passed | Failed | Skipped |
|---|---:|---:|---:|
| Backend unit tests (parsers, guard rules, tiers, PIN, audit chain, forecast, credit, loans) | 668 | 0 | 0 |
| End-to-end scenarios through the API (mock LLM, real sandbox) | 186 | 0 | 0 |
| Architecture rules (the agent cannot reach executors, the DB or the key) | 15 | 0 | 0 |
| Sandbox provider (signature checks, idempotency, freeze) | 25 | 0 | 0 |
| Synthetic data generator | 10 | 0 | 0 |
| Red-team suite (one test per attack and per benign request) | 160 | 0 | 0 |

## Failures

None.
