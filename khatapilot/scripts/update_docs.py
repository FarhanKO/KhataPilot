"""Fill every number in the docs from the result files, never by hand.

    python scripts/update_docs.py           # rewrite the marked numbers in README.md and docs/*.md
    python scripts/update_docs.py --check   # list stale numbers and exit 1 (a test runs this in CI)

A number in a doc sits between two invisible markers, and a generated table between two block markers:

    <!--n:redteam.block_rate-->100.0%<!--/n-->
    <!--t:redteam.by_category-->
    | ... |
    <!--/t-->

The values come from the files the evaluations write: the test report, the red-team reports (mock and
live), the demo runs (mock and live), the live test run, and the three model evaluations in docs/results.
Run it after any of those change; `scripts/export_evidence.py` refreshes the test and red-team files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The docs sit beside the code, or one level up when the code has a folder of its own (the GitHub layout:
# khatapilot/ for the code, with docs/ and README.md beside it).
DOCS_ROOT = ROOT.parent if not (ROOT / "docs").is_dir() and (ROOT.parent / "docs").is_dir() else ROOT
EVIDENCE = ROOT / "evidence"
RESULTS = DOCS_ROOT / "docs" / "results"
DOCS = [DOCS_ROOT / "README.md", DOCS_ROOT / "docs" / "TECHNICAL.md", DOCS_ROOT / "docs" / "SECURITY_ASSESSMENT.md",
        DOCS_ROOT / "docs" / "RISKS.md", DOCS_ROOT / "docs" / "DEMO_SCRIPT.md"]

NUMBER = re.compile(r"<!--n:([a-z0-9_.]+)-->(.*?)<!--/n-->", re.S)
TABLE = re.compile(r"(<!--t:([a-z0-9_.]+)-->\n)(.*?)(<!--/t-->)", re.S)

CATEGORY_NAMES = {
    "direct_injection": "Direct injection (stranger's message)", "sms_injection": "Injection hidden in an SMS",
    "sms_spoofing": "Fake payment SMS", "invoice_injection": "Injection hidden in invoice text",
    "supplier_message": "A supplier's (or look-alike's) message", "roleplay_override": "Role-play and \"system override\"",
    "fake_authority": "Fake urgency and authority", "bangla_banglish": "Bangla and Banglish",
    "encoding": "Encoding tricks", "amount_tampering": "Amount, date and payee tampering",
    "payee_spoofing": "Payee spoofing", "payment_splitting": "Payment splitting and caps",
    "limit_change": "Changing limits or payees", "kill_switch_bypass": "Kill-switch bypass",
    "pin_fishing": "PIN fishing", "data_exfiltration": "Data exfiltration",
    "tool_argument_injection": "Tool-argument injection", "guard_bypass": "Skipping the guard (provider API)",
}


def pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def taka(value: float) -> str:
    return f"৳{value:,.0f}"


def load(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# The suites of the test report, in the order it lists them (same names as scripts/export_evidence.py).
SUITES = (
    ("backend.tests.unit", "Unit tests: parsers, guard rules and tiers, PIN, audit chain, forecast, credit, loans"),
    ("backend.tests.scenarios", "End-to-end scenarios through the API (mock model, real provider in-process)"),
    ("backend.tests.architecture", "Architecture rules (the agent cannot reach executors, the database or the key)"),
    ("sandbox.tests", "Simulated provider (signature checks, idempotency, freeze)"),
    ("data.tests", "Synthetic data generator"),
    ("redteam.tests", "Red-team suite (one test per attack and per genuine request)"),
)


def junit(path: Path) -> dict | None:
    if not path.exists():
        return None
    passed = failed = skipped = 0
    suites: dict[str, list[int]] = {prefix: [0, 0] for prefix, _ in SUITES}
    for case in ET.parse(path).getroot().iter("testcase"):
        ok = case.find("failure") is None and case.find("error") is None
        if not ok:
            failed += 1
        elif case.find("skipped") is not None:
            skipped += 1
        else:
            passed += 1
        prefix = next((p for p, _ in SUITES if case.get("classname", "").startswith(p)), None)
        if prefix:
            suites[prefix][0] += ok and case.find("skipped") is None
            suites[prefix][1] += 1
    return {"passed": passed, "failed": failed, "skipped": skipped, "total": passed + failed + skipped,
            "suites": suites}


# ---------------------------------------------------------------- the values

def values() -> tuple[dict[str, str], dict[str, str]]:
    n: dict[str, str] = {}
    t: dict[str, str] = {}

    tests = junit(EVIDENCE / "test_report.xml")
    if tests:
        n["tests.passed"], n["tests.total"] = str(tests["passed"]), str(tests["total"])
        n["tests.rate"] = pct(tests["passed"] / tests["total"])
        t["tests.by_suite"] = "\n".join(
            ["| Suite | Passed | Tests |", "|---|---:|---:|"]
            + [f"| {name} | {tests['suites'][prefix][0]} | {tests['suites'][prefix][1]} |" for prefix, name in SUITES
               if tests["suites"][prefix][1]]
            + [f"| **All** | **{tests['passed']}** | **{tests['total']}** |"]) + "\n"

    live_tests = junit(EVIDENCE / "live" / "live_tests.xml")
    n["live.tests.passed"] = str(live_tests["passed"]) if live_tests else "0"
    n["live.tests.total"] = str(live_tests["total"]) if live_tests else "0"

    red = load(EVIDENCE / "redteam_report.json")
    if red:
        a, b, meta = red["summary"]["attacks"], red["summary"]["benign"], red["meta"]
        n.update({
            "redteam.attacks": str(a["total"]), "redteam.stopped": str(a["stopped"]), "redteam.refused": str(a["refused"]),
            "redteam.held": str(a["held_for_pin"]), "redteam.missed": str(a["missed"]),
            "redteam.block_rate": pct(a["block_rate"]), "redteam.as_designed": str(a["as_designed"]),
            "redteam.benign": str(b["total"]), "redteam.benign_passed": str(b["passed"]),
            "redteam.false_alarms": str(b["false_alarms"]), "redteam.false_alarm_rate": pct(b["false_alarm_rate"]),
            "redteam.categories": str(len(red["summary"]["by_category"])), "redteam.cases": str(meta["cases_run"]),
            "redteam.commit": meta["git_commit"] or "unknown", "redteam.fraud_model": meta["risk_model"],
        })
        t["redteam.by_category"] = category_table(red, live=None)
        t["redteam.first_layer"] = "\n".join(
            ["| Layer and check | Attacks |", "|---|---:|"]
            + [f"| {layer} | {count} |" for layer, count in a["first_layer"].items()]) + "\n"

    live = load(EVIDENCE / "redteam_report_anthropic.json")
    if live:
        a, b, meta = live["summary"]["attacks"], live["summary"]["benign"], live["meta"]
        declined = sum(c for layer, c in a["first_layer"].items() if layer.startswith("model:"))
        n.update({
            "live.redteam.attacks": str(a["total"]), "live.redteam.stopped": str(a["stopped"]),
            "live.redteam.refused": str(a["refused"]), "live.redteam.held": str(a["held_for_pin"]),
            "live.redteam.missed": str(a["missed"]), "live.redteam.block_rate": pct(a["block_rate"]),
            "live.redteam.benign": str(b["total"]), "live.redteam.benign_passed": str(b["passed"]),
            "live.redteam.false_alarm_rate": pct(b["false_alarm_rate"]), "live.redteam.model": meta["model"],
            "live.redteam.model_first": str(declined),
            "live.redteam.categories": str(sum(1 for r in live["summary"]["by_category"] if r["attacks"])),
            "live.redteam.cost": f"${meta['estimated_cost_usd']:.2f}" if meta.get("estimated_cost_usd") is not None
            else "n/a",
            "live.redteam.calls": str((meta.get("usage") or {}).get("calls", 0)),
        })
        if red:
            t["redteam.by_category"] = category_table(red, live=live)

    demo = load(EVIDENCE / "demo_run.json")
    if demo:
        n["demo.steps"], n["demo.passed"] = str(len(demo["steps"])), str(sum(s["ok"] for s in demo["steps"]))
    live_demo = load(EVIDENCE / "live" / "demo_run.json")
    if live_demo:
        steps = live_demo["steps"]
        n["live.demo.steps"], n["live.demo.passed"] = str(len(steps)), str(sum(s["ok"] for s in steps))
        n["live.demo.model"] = live_demo["model"]
        n["live.demo.cost"] = f"${live_demo['estimated_cost_usd']:.2f}" if live_demo.get("estimated_cost_usd") else "n/a"
        n["live.demo.design_misses"] = str(sum(1 for s in steps for c in s["checks"] if not c["must"] and not c["ok"]))

    fraud = load(RESULTS / "fraud_sentinel.json")
    if fraud:
        medium, rules = fraud["cascade_at_medium"], fraud["rules_baseline_at_medium"]
        typ = fraud["per_typology"]
        n.update({
            "fraud.payments": f"{fraud['train_payments'] + fraud['test_payments']:,}",
            "fraud.train": f"{fraud['train_payments']:,}", "fraud.test": f"{fraud['test_payments']:,}",
            "fraud.auc": f"{fraud['layer2']['roc_auc']:.3f}", "fraud.pr_auc": f"{fraud['layer2']['pr_auc']:.3f}",
            "fraud.recall": pct(medium["recall"], 0), "fraud.precision": pct(medium["precision"], 0),
            "fraud.far": pct(medium["false_alarm_rate"]), "fraud.rules_recall": pct(rules["recall"], 0),
            "fraud.rules_far": pct(rules["false_alarm_rate"]),
            "fraud.cost_cut": pct(1 - medium["banking_risk_cost_taka"] / rules["banking_risk_cost_taka"], 0),
            "fraud.high_recall": pct(fraud["cascade_at_high"]["recall"], 0),
            "fraud.high_precision": pct(fraud["cascade_at_high"]["precision"], 0),
            "fraud.scam_small": pct(typ["scam_small"]["flagged_rate"], 0),
            "fraud.spoof": pct(typ["spoof_supplier"]["flagged_rate"], 0),
            "fraud.structuring": pct(typ["structuring"]["flagged_rate"], 0),
            "fraud.takeover": pct(typ["takeover_burst"]["flagged_rate"], 0),
            "fraud.max_psi": f"{max(fraud['psi'].values()):.4f}",
        })

    forecast = load(RESULTS / "forecast_backtest.json")
    if forecast:
        model = forecast["model"]
        n.update({
            "forecast.windows": f"{forecast['windows']:,}", "forecast.shops": str(forecast["shops"]),
            "forecast.mape14": pct(model["total_14d_median_ape"]),
            "forecast.p90": pct(model["total_14d_p90_ape"]),
            "forecast.naive28": pct(forecast["naive_mean_28d"]["total_14d_median_ape"]),
            "forecast.naive_week": pct(forecast["naive_last_week"]["total_14d_median_ape"]),
            "forecast.wape": pct(model["daily_wape"]), "forecast.bias": pct(model["bias"]),
            "forecast.coverage": pct(forecast["band_80_coverage_14d_total"]),
        })

    credit = load(RESULTS / "credit_model.json")
    if credit:
        fair = credit["fairness"]
        repeats = credit["oof_roc_auc_repeats"]
        n.update({
            "credit.shops": str(credit["shops"]), "credit.default_rate": pct(credit["default_rate"]),
            "credit.auc": f"{credit['oof']['roc_auc']:.3f}", "credit.oracle": f"{credit['true_pd']['oracle_roc_auc']:.3f}",
            "credit.logistic": f"{credit['baseline_logistic']['roc_auc']:.3f}",
            "credit.auc_low": f"{min(repeats):.3f}", "credit.auc_high": f"{max(repeats):.3f}",
            "credit.brier": f"{credit['oof']['brier']:.3f}", "credit.ece": pct(credit["oof"]["ece"]),
            "credit.approval_rate": pct(credit["approval"]["approval_rate"], 0),
            "credit.default_approved": pct(credit["approval"]["default_rate_approved"]),
            "credit.default_declined": pct(credit["approval"]["default_rate_declined"]),
            "credit.type_ratio": f"{fair['shop_type']['approval_ratio_min_max']:.2f}",
            "credit.type_gap": f"{fair['shop_type']['good_payer_approval_gap'] * 100:.0f}",
            "credit.urban_ratio": f"{fair['urbanicity']['approval_ratio_min_max']:.2f}",
            "credit.urban_gap": f"{fair['urbanicity']['good_payer_approval_gap'] * 100:.0f}",
            "credit.division_ratio": f"{fair['division']['approval_ratio_min_max']:.2f}",
            "credit.division_gap": f"{fair['division']['good_payer_approval_gap'] * 100:.0f}",
        })

    install = load(EVIDENCE / "clean_install.json")
    if install:
        n["install.minutes"] = f"{install['total_seconds'] / 60:.1f}"
        n["install.tests"] = str(install["tests_passed"])
    return n, t


def category_table(red: dict, live: dict | None) -> str:
    live_rows = {r["category"]: r for r in live["summary"]["by_category"]} if live else {}
    head = "| Category | Attacks | Stopped (mock, model fooled) | Refused | Held for PIN |"
    rule = "|---|---:|---:|---:|---:|"
    if live:
        head += " Live sample: stopped |"
        rule += "---:|"
    lines = [head, rule]
    for row in red["summary"]["by_category"]:
        line = (f"| {CATEGORY_NAMES.get(row['category'], row['category'])} | {row['attacks']} | {row['stopped']} | "
                f"{row['refused']} | {row['held_for_pin']} |")
        if live:
            lr = live_rows.get(row["category"])
            line += f" {lr['stopped']} of {lr['attacks']} |" if lr and lr["attacks"] else " — |"
        lines.append(line)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- rewriting

def render(text: str, n: dict[str, str], t: dict[str, str], missing: Callable[[str], None]) -> str:
    def number(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in n:
            missing(key)
            return match.group(0)
        return f"<!--n:{key}-->{n[key]}<!--/n-->"

    def table(match: re.Match[str]) -> str:
        key = match.group(2)
        if key not in t:
            missing(key)
            return match.group(0)
        return f"{match.group(1)}{t[key]}{match.group(4)}"

    return TABLE.sub(table, NUMBER.sub(number, text))


def update(check: bool = False, skip: tuple[str, ...] = ()) -> list[str]:
    """Rewrite the docs (or, with check, only report). Returns the problems found. Keys starting with a prefix
    in `skip` are left alone (the test suite skips "tests.", the numbers of the very run it is part of)."""
    n, t = values()
    n = {k: v for k, v in n.items() if not k.startswith(skip)} if skip else n
    t = {k: v for k, v in t.items() if not k.startswith(skip)} if skip else t
    problems: list[str] = []
    for path in DOCS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        def missing(key: str, p: Path = path) -> None:
            if not (skip and key.startswith(skip)):
                problems.append(f"{p.relative_to(ROOT)}: no value for {key!r}")

        new = render(text, n, t, missing)
        if new != text:
            if check:
                stale = [m.group(1) for m in NUMBER.finditer(text) if m.group(1) in n and m.group(2) != n[m.group(1)]]
                stale += [m.group(2) for m in TABLE.finditer(text) if m.group(2) in t and m.group(3) != t[m.group(2)]]
                problems.append(f"{path.relative_to(ROOT)}: out of date: {', '.join(stale)}")
            else:
                path.write_text(new, encoding="utf-8")
                print(f"Updated {path.relative_to(ROOT)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill the numbers in the docs from the result files.")
    parser.add_argument("--check", action="store_true", help="only report stale numbers (exit 1 if any)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    problems = update(check=args.check)
    for problem in problems:
        print(problem)
    if args.check and not problems:
        print("Every number in the docs matches the result files.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
