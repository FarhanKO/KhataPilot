"""The scripted demo (`scripts/demo_run.py`) in-process: every step passes, the run is deterministic in
mock mode, and the evidence export maps every scene to its audit and provider-log lines."""

from __future__ import annotations

import json

from scripts.demo_run import STEPS, Demo, InProcessServices, write_run
from scripts.export_evidence import export, verify_chain


def play(tmp_path, generated_dir, credit_model_path, name: str):
    services = InProcessServices(tmp_path / name, generated_dir, credit_model_path)
    services.reload_shop()
    demo = Demo(services)
    return services, demo, demo.run(progress=lambda _line: None)


def signature(records) -> list:
    """What must be identical on every mock-mode run: each action's tool outcomes and every check."""
    return [(r.key, [(a.kind, [(o["tool"], o["status"], o["tier"]) for o in a.outcomes]) for a in r.actions],
             [(c.what, c.ok) for c in r.checks]) for r in records]


def test_the_demo_passes_every_step_and_is_deterministic(tmp_path, generated_dir, credit_model_path):
    first_services, demo, first = play(tmp_path, generated_dir, credit_model_path, "first")
    try:
        failed = [(r.n, r.title, r.error, [c.what for c in r.checks if not c.ok]) for r in first if not r.ok]
        assert not failed, failed
        assert [r.key for r in first] == [key for key, _, _ in STEPS]

        out = tmp_path / "evidence"
        out.mkdir()
        (out / "redteam_report.json").write_text(json.dumps({  # keep the export from running the red team here
            "meta": {"git_commit": "test", "mode": "mock"},
            "summary": {"attacks": {"total": 1, "stopped": 1, "block_rate": 1.0},
                        "benign": {"total": 1, "false_alarm_rate": 0.0}}}), encoding="utf-8")
        manifest = write_run(demo, first, out, "test")
        result = export(first_services, out, manifest=manifest, run_tests=False)
    finally:
        first_services.close()

    assert result["chain"]["ok"], result["chain"]
    audit = [json.loads(line) for line in (out / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert verify_chain(audit)["ok"]
    index = (out / "INDEX.md").read_text(encoding="utf-8")
    assert all(f"| {n} | " in index for n in range(1, len(STEPS) + 1))
    assert "nothing reached the provider" in index and "**FAILED**" not in index
    transcript = (out / "demo_run.md").read_text(encoding="utf-8")
    assert "not in your bKash statement" in transcript and "Possible split payment" in transcript

    second_services, _, second = play(tmp_path, generated_dir, credit_model_path, "second")
    second_services.close()
    assert signature(second) == signature(first), "mock mode must play the same way every time"


def test_a_tampered_export_is_caught_by_the_independent_check(tmp_path, generated_dir, credit_model_path):
    services, _, _ = play(tmp_path, generated_dir, credit_model_path, "tamper")
    try:
        from scripts.export_evidence import fetch_audit

        audit = fetch_audit(services)
    finally:
        services.close()
    payment = next(i for i, e in enumerate(audit) if e["event"] == "execution" and e["decision"] == "EXECUTED")
    audit[payment] = {**audit[payment], "reason": audit[payment]["reason"].replace("3,000", "300")}
    assert verify_chain(audit) == {"ok": False, "entries": len(audit), "first_bad_line": payment + 1,
                                   "problem": "an entry's content does not match its hash"}
