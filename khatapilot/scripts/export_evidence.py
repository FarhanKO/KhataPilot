"""Export the execution evidence to evidence/ and write evidence/INDEX.md.

    python scripts/export_evidence.py               # logs from the running services, the test report, INDEX.md
    python scripts/export_evidence.py --skip-tests  # reuse evidence/test_report.xml
    python scripts/export_evidence.py --redteam     # also re-run the red-team suite (about 2 minutes)

`scripts/demo_run.py` calls this at the end of a run. It writes:

- audit_log.jsonl / .csv: the hash-chained audit log, every entry, re-verified here by recomputing
  every hash from the exported fields (independently of the server's own `verify`);
- sandbox_oplog.jsonl / .csv: the simulated provider's operation log (every request it received,
  accepted or refused, each marked "simulated": true);
- test_report.xml / .md: the whole test suite in mock mode (JUnit XML and a summary);
- redteam_report.json / .csv / .md: kept from the last red-team run unless --redteam;
- INDEX.md: each file, what it proves, and for each scene of docs/DEMO_SCRIPT.md the exact lines of
  the audit and provider logs it produced (from evidence/demo_run.json).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
# The docs sit beside the code, or one level up when the code has a folder of its own (the GitHub layout:
# khatapilot/ for the code, with docs/ and README.md beside it).
DOCS_ROOT = ROOT.parent if not (ROOT / "docs").is_dir() and (ROOT.parent / "docs").is_dir() else ROOT

from app.core.actions import canonical_json  # noqa: E402
from app.infra.audit_log import GENESIS_HASH, entry_hash  # noqa: E402

EVIDENCE = ROOT / "evidence"
AUDIT_COLUMNS = ("seq", "ts", "request_id", "channel", "actor", "provenance", "event", "summary", "tier", "decision",
                 "reason", "sandbox_ref", "decision_id", "checks", "prev_hash", "hash")
TEST_GROUPS = (
    ("backend.tests.unit", "Backend unit tests (parsers, guard rules, tiers, PIN, audit chain, forecast, credit, loans)"),
    ("backend.tests.scenarios", "End-to-end scenarios through the API (mock LLM, real sandbox)"),
    ("backend.tests.architecture", "Architecture rules (the agent cannot reach executors, the DB or the key)"),
    ("sandbox.tests", "Sandbox provider (signature checks, idempotency, freeze)"),
    ("data.tests", "Synthetic data generator"),
    ("redteam.tests", "Red-team suite (one test per attack and per benign request)"),
)


# ---------------------------------------------------------------- reading the logs

def fetch_audit(services) -> list[dict]:
    entries, since = [], 0
    while True:
        status, page = services.backend("GET", "/api/audit", params={"since": since, "limit": 2000,
                                                                     "oldest_first": "true"})
        if status != 200:
            raise RuntimeError(f"GET /api/audit returned HTTP {status}: {page}")
        if not page:
            return entries
        entries += page
        since = page[-1]["seq"]


def fetch_oplog(services) -> list[dict]:
    entries, since = [], 0
    while True:
        status, body = services.provider("/v1/oplog", params={"since": since, "limit": 5000})
        if status != 200:
            raise RuntimeError(f"GET /v1/oplog returned HTTP {status}: {body}")
        page = body["entries"]
        if not page:
            return entries
        entries += page
        since = page[-1]["seq"]


def verify_chain(entries: list[dict]) -> dict:
    """Recompute every hash from the exported fields: proof that does not trust the server's `verify`."""
    previous = GENESIS_HASH
    for line, e in enumerate(entries, start=1):
        if e["prev_hash"] != previous:
            return {"ok": False, "entries": len(entries), "first_bad_line": line,
                    "problem": "the link to the previous entry is broken"}
        fields = {**{k: e[k] for k in ("entry_id", "ts", "request_id", "channel", "actor", "provenance", "event",
                                       "summary", "tier", "decision", "reason", "sandbox_ref", "decision_id",
                                       "prev_hash")},
                  "action_json": canonical_json(e["action"]).decode("utf-8"),
                  "checks_json": canonical_json(e["checks"]).decode("utf-8")}
        if entry_hash(fields) != e["hash"]:
            return {"ok": False, "entries": len(entries), "first_bad_line": line,
                    "problem": "an entry's content does not match its hash"}
        previous = e["hash"]
    return {"ok": True, "entries": len(entries), "head_hash": previous}


# ---------------------------------------------------------------- writing files

def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def write_csv(path: Path, rows: list[dict], columns: tuple[str, ...] | list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                             for k, v in row.items()})


def audit_rows(entries: list[dict]) -> list[dict]:
    return [{**e, "checks": "; ".join(f"{c['name']}:{c['status']}" for c in e["checks"])} for e in entries]


def oplog_columns(entries: list[dict]) -> list[str]:
    columns: dict[str, None] = {}
    for e in entries:
        columns.update(dict.fromkeys(e))
    return list(columns)


# ---------------------------------------------------------------- tests

def run_test_suite(out_dir: Path) -> None:
    xml = out_dir / "test_report.xml"
    print("Running the test suite in mock mode (about 6 minutes)...", flush=True)
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}"], cwd=ROOT)
    scrub_junit(xml)


def scrub_junit(xml: Path) -> None:
    """The JUnit report is published with the repo: drop the machine's hostname pytest writes into it."""
    if not xml.exists():
        return
    tree = ET.parse(xml)
    for suite in tree.getroot().iter("testsuite"):
        suite.attrib.pop("hostname", None)
    tree.write(xml, encoding="utf-8", xml_declaration=True)


def summarize_tests(xml: Path) -> dict | None:
    if not xml.exists():
        return None
    root = ET.parse(xml).getroot()
    cases = root.iter("testcase")
    groups: dict[str, Counter] = {prefix: Counter() for prefix, _ in TEST_GROUPS}
    totals, failures, seconds = Counter(), [], 0.0
    for case in cases:
        classname = case.get("classname", "")
        outcome = ("failed" if case.find("failure") is not None or case.find("error") is not None
                   else "skipped" if case.find("skipped") is not None else "passed")
        totals[outcome] += 1
        seconds += float(case.get("time") or 0)
        group = next((p for p, _ in TEST_GROUPS if classname.startswith(p)), "other")
        groups.setdefault(group, Counter())[outcome] += 1
        if outcome == "failed":
            failures.append(f"{classname}::{case.get('name')}")
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    stamp = suite.get("timestamp") if suite is not None else None
    return {"totals": dict(totals), "groups": {k: dict(v) for k, v in groups.items() if v}, "failures": failures,
            "seconds": round(seconds, 1), "timestamp": stamp, "total": sum(totals.values())}


def write_test_report(summary: dict, path: Path) -> None:
    t = summary["totals"]
    passed, total = t.get("passed", 0), summary["total"]
    lines = [
        "# Test report",
        "",
        f"`python -m pytest` in `mock` mode (no API key, no network), run {summary['timestamp'] or 'at export'}: "
        f"**{passed} of {total} passed** ({passed / total:.1%}), {t.get('failed', 0)} failed, "
        f"{t.get('skipped', 0)} skipped, {summary['seconds']} s of test time. The JUnit XML is "
        "[test_report.xml](test_report.xml). Tests against the real Claude API are opt-in "
        "(`pytest -m live`) and not part of this run.",
        "",
        "| Suite | Passed | Failed | Skipped |",
        "|---|---:|---:|---:|",
    ]
    names = dict(TEST_GROUPS)
    for group, counts in summary["groups"].items():
        lines.append(f"| {names.get(group, group)} | {counts.get('passed', 0)} | {counts.get('failed', 0)} | "
                     f"{counts.get('skipped', 0)} |")
    lines += ["", "## Failures", ""] + ([f"- `{f}`" for f in summary["failures"]] or ["None."]) + [""]
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- the index

def _lines(seq_range: list[int] | None, entries: list[dict], file: str, empty: str = "—") -> str:
    if not seq_range:
        return empty
    line_of = {e["seq"]: i for i, e in enumerate(entries, start=1)}
    first, last = line_of.get(seq_range[0]), line_of.get(seq_range[1])
    if first is None or last is None:
        return "—"
    return f"`{file}` lines {first}–{last}" if first != last else f"`{file}` line {first}"


def live_rows(out_dir: Path) -> list[tuple[str, str, str]]:
    """The runs against the real model, and the clean-install timing, when they are in the evidence folder.
    They are written by their own commands (they need an API key), so the index only links them."""
    rows = []
    demo = out_dir / "live" / "demo_run.json"
    if demo.exists():
        m = json.loads(demo.read_text(encoding="utf-8"))
        cost = f", about ${m['estimated_cost_usd']:.2f} of model usage" if m.get("estimated_cost_usd") else ""
        rows.append(("[live/demo_run.md](live/demo_run.md), [.json](live/demo_run.json)",
                     f"The same scripted demo against the real model ({m['model']}, commit `{m.get('git_commit')}`): "
                     f"{sum(s['ok'] for s in m['steps'])} of {len(m['steps'])} steps passed{cost}",
                     "The core tasks work with the live model; *must* checks (money, books, decisions) hold whatever "
                     "it decides"))
    report = out_dir / "redteam_report_anthropic.json"
    if report.exists():
        r = json.loads(report.read_text(encoding="utf-8"))
        a, b, meta = r["summary"]["attacks"], r["summary"]["benign"], r["meta"]
        sample = " (a sample: every category)" if meta.get("sample") else ""
        rows.append(("[redteam_report_anthropic.md](redteam_report_anthropic.md), [.csv](redteam_report_anthropic.csv), "
                     "[.json](redteam_report_anthropic.json)",
                     f"{a['total']} attacks and {b['total']} benign requests against {meta['model']}{sample}",
                     f"Live block rate {a['block_rate']:.1%} ({a['stopped']}/{a['total']}), false-alarm rate "
                     f"{b['false_alarm_rate']:.1%}"))
    tests = out_dir / "live" / "live_tests.xml"
    if tests.exists():
        summary = summarize_tests(tests)
        rows.append(("[live/live_tests.xml](live/live_tests.xml)",
                     f"`pytest -m live` against the real model: {summary['totals'].get('passed', 0)} of "
                     f"{summary['total']} passed",
                     "Bookkeeping, a trusted payment, a hidden SMS instruction and a stranger's demand with Claude"))
    install = out_dir / "clean_install.json"
    if install.exists():
        i = json.loads(install.read_text(encoding="utf-8"))
        rows.append(("[clean_install.json](clean_install.json)",
                     f"The README quick start timed on a fresh clone: {i['total_seconds'] / 60:.1f} minutes, "
                     f"{i['tests_passed']} tests passed",
                     "A clean machine can install, train and test within the brief's 15 minutes"))
    return rows


def _rel(out_dir: Path, target: Path) -> str:
    """A link from a file in out_dir to target (the repo-relative path if they are on different drives)."""
    try:
        return Path(os.path.relpath(target, out_dir)).as_posix()
    except ValueError:
        return target.relative_to(DOCS_ROOT).as_posix()


def write_index(out_dir: Path, *, manifest: dict | None, audit: list[dict], chain: dict, server_chain: dict,
                oplog: list[dict], tests: dict | None, redteam: dict | None, generated_at: str) -> None:
    rows = [
        ("[demo_run.md](demo_run.md), [demo_run.json](demo_run.json)",
         "The scripted demo: every message, reply, guard decision and check, scene by scene",
         "All five core tasks end to end in the web backend, with the decision for each action"),
        ("[audit_log.jsonl](audit_log.jsonl), [audit_log.csv](audit_log.csv)",
         f"The hash-chained audit log, {len(audit)} entries (who asked, provenance, proposed action, tier, every "
         "check with its result, the decision, the reason in plain English, the sandbox reference)",
         f"Every decision is recorded and tamper-evident: the chain verifies on the server "
         f"({'ok' if server_chain.get('ok') else 'FAILED'}) and again here by recomputing every hash "
         f"({'ok' if chain['ok'] else 'FAILED: ' + chain.get('problem', '')}); head "
         f"`{chain.get('head_hash', '')[:16]}…`"),
        ("[sandbox_oplog.jsonl](sandbox_oplog.jsonl), [sandbox_oplog.csv](sandbox_oplog.csv)",
         f"The simulated provider's own operation log, {len(oplog)} operations "
         f"({sum(e.get('status') == 'ok' for e in oplog)} accepted, {sum(e.get('status') == 'rejected' for e in oplog)} "
         f"refused, {sum(e.get('status') == 'not_found' for e in oplog)} lookups that found nothing), every one marked "
         "simulated",
         "What actually reached the provider: only guard-signed payments, the loan application the owner approved, "
         "the freeze; nothing from the attacks"),
    ]
    if tests:
        t = tests["totals"]
        rows.append(("[test_report.md](test_report.md), [test_report.xml](test_report.xml)",
                     f"The test suite in mock mode: {t.get('passed', 0)} of {tests['total']} passed",
                     "Unit, scenario, architecture and red-team tests (the brief's test cases)"))
    if redteam:
        a, b = redteam["summary"]["attacks"], redteam["summary"]["benign"]
        rows.append(("[redteam_report.md](redteam_report.md), [.csv](redteam_report.csv), [.json](redteam_report.json)",
                     f"{a['total']} attacks and {b['total']} benign requests (commit "
                     f"`{redteam['meta'].get('git_commit')}`, {redteam['meta']['mode']} mode)",
                     f"Block rate {a['block_rate']:.1%} ({a['stopped']}/{a['total']}), false-alarm rate "
                     f"{b['false_alarm_rate']:.1%}, per category"))
    rows += live_rows(out_dir)
    lines = [
        "# Evidence index",
        "",
        f"Written by `scripts/export_evidence.py` on {generated_at}"
        + (f" after the demo run at commit `{manifest.get('git_commit')}` (backend in **{manifest['llm_mode']}** mode, "
           f"{manifest['model']}; fraud score {manifest['risk_model']})" if manifest else "")
        + ". All data is synthetic and every payment went through the simulated provider (sandbox); no real money, "
        "accounts or customers. Do not edit by hand.",
        "",
        "## Files",
        "",
        "| File | What it is | What it proves |",
        "|---|---|---|",
        *[f"| {f} | {what} | {proves} |" for f, what, proves in rows],
        "",
    ]
    if manifest:
        lines += [
            "## The demo, scene by scene",
            "",
            f"Scene numbers are the steps of [docs/DEMO_SCRIPT.md]({_rel(out_dir, DOCS_ROOT / 'docs' / 'DEMO_SCRIPT.md')}) and of "
            "[demo_run.md](demo_run.md). Line numbers count from 1 in the JSONL files (in the CSV files, add one "
            "for the header).",
            "",
            "| Scene | What happens | Task it proves | Audit log | Provider log | Result |",
            "|---:|---|---|---|---|---|",
        ]
        for step in manifest["steps"]:
            lines.append(
                f"| {step['n']} | {step['title']} | {step['task']} | "
                f"{_lines(step['audit_seq'], audit, 'audit_log.jsonl')} | "
                f"{_lines(step['oplog_seq'], oplog, 'sandbox_oplog.jsonl', 'nothing reached the provider')} | "
                f"{'passed' if step['ok'] else '**FAILED**'} |")
        lines.append("")
    lines += [
        "## The brief's tasks and where they are proved",
        "",
        "| Task (brief section 2) | Demo scenes | Tests |",
        "|---|---|---|",
        "| 1. Bookkeeper (tier 0) | 1, 2, 5 | `backend/tests/unit/test_bookkeeping_tools.py`, `test_sms_parser.py`, `backend/tests/scenarios/test_chat.py` |",
        "| 2. Supplier payer (tier 2 / tier 3) | 7, 8, 11 | `backend/tests/scenarios/test_payments.py`, `backend/tests/unit/test_policy_engine.py` |",
        "| 3. Guard (blocked) | 3, 9, 10, 11, 15 | `backend/tests/unit/test_policy_engine.py`, `test_injection.py`, the red-team suite |",
        "| 4. Loan helper with financing-option matching | 12, 13 | `backend/tests/scenarios/test_credit.py`, `backend/tests/unit/test_credit.py`, `test_loans.py` |",
        "| 5. Cash-flow forecast with alerts | 4 | `backend/tests/scenarios/test_forecast.py`, `backend/tests/unit/test_forecast.py` |",
        "| Baki collector (tier 1, owner approves) | 6 | `backend/tests/scenarios/test_reminders.py`, `backend/tests/unit/test_reminders.py` |",
        "| Kill switch, owner controls, audit chain | 14, 15, 16 | `backend/tests/unit/test_guard_flows.py`, `test_audit_log.py` |",
        "",
    ]
    (out_dir / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- the whole export

def export(services, out_dir: Path = EVIDENCE, *, manifest: dict | None = None, run_tests: bool = True,
           redteam: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    if manifest is None and (out_dir / "demo_run.json").exists():
        manifest = json.loads((out_dir / "demo_run.json").read_text(encoding="utf-8"))

    audit = fetch_audit(services)
    write_jsonl(out_dir / "audit_log.jsonl", audit)
    write_csv(out_dir / "audit_log.csv", audit_rows(audit), AUDIT_COLUMNS)
    chain = verify_chain(audit)
    status, server_chain = services.backend("GET", "/api/audit/verify")
    oplog = fetch_oplog(services)
    write_jsonl(out_dir / "sandbox_oplog.jsonl", oplog)
    write_csv(out_dir / "sandbox_oplog.csv", oplog, oplog_columns(oplog))
    print(f"Audit log: {len(audit)} entries, chain {'verified' if chain['ok'] else 'BROKEN'} (recomputed here). "
          f"Provider log: {len(oplog)} operations.")

    if run_tests:
        run_test_suite(out_dir)
    tests = summarize_tests(out_dir / "test_report.xml")
    if tests:
        write_test_report(tests, out_dir / "test_report.md")
        print(f"Tests: {tests['totals'].get('passed', 0)} of {tests['total']} passed.")

    if redteam or not (out_dir / "redteam_report.json").exists():
        from redteam.report import write_all
        from redteam.runner import run_suite

        print("Running the red-team suite (about 2 minutes)...", flush=True)
        write_all(run_suite(), out_dir, "redteam_report")
    report = json.loads((out_dir / "redteam_report.json").read_text(encoding="utf-8"))

    write_index(out_dir, manifest=manifest, audit=audit, chain=chain,
                server_chain=server_chain if status == 200 else {"ok": False}, oplog=oplog, tests=tests,
                redteam=report, generated_at=generated_at)
    print(f"Wrote {out_dir / 'INDEX.md'}.")
    return {"audit_entries": len(audit), "chain": chain, "oplog_entries": len(oplog), "tests": tests}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export KhataPilot's execution evidence to evidence/.")
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    parser.add_argument("--skip-tests", action="store_true", help="reuse evidence/test_report.xml")
    parser.add_argument("--redteam", action="store_true", help="re-run the red-team suite too")
    parser.add_argument("--out", type=Path, default=EVIDENCE)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from scripts.demo_run import DemoError, RunningServices

    try:
        services = RunningServices(args.backend)
    except DemoError as problem:
        print(problem)
        return 1
    started = time.perf_counter()
    try:
        result = export(services, args.out, run_tests=not args.skip_tests, redteam=args.redteam)
    finally:
        services.close()
    print(f"Done in {time.perf_counter() - started:.0f} s.")
    return 0 if result["chain"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
