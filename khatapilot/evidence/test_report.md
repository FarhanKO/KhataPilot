# Test report

`python -m pytest` in `mock` mode (no API key, no network), run 2026-11-14T07:38:03.639002+06:00: **907 of 907 passed** (100.0%), 0 failed, 0 skipped, 270.5 s of test time. The JUnit XML is [test_report.xml](test_report.xml). Tests against the real Claude API are opt-in (`pytest -m live`) and not part of this run.

| Suite | Passed | Failed | Skipped |
|---|---:|---:|---:|
| Backend unit tests (parsers, guard rules, tiers, PIN, audit chain, forecast, credit, loans) | 539 | 0 | 0 |
| End-to-end scenarios through the API (mock LLM, real sandbox) | 158 | 0 | 0 |
| Architecture rules (the agent cannot reach executors, the DB or the key) | 15 | 0 | 0 |
| Sandbox provider (signature checks, idempotency, freeze) | 25 | 0 | 0 |
| Synthetic data generator | 10 | 0 | 0 |
| Red-team suite (one test per attack and per benign request) | 160 | 0 | 0 |

## Failures

None.
