"""Backtest the cash forecast's everyday part on the synthetic shops and write docs/results/forecast_backtest.*.

    python scripts/evaluate_forecast.py

What is tested: the estimated money in (sales in cash and mobile money, plus baki coming back) over
the next 14 days, using exactly the app's code (`build_baseline`, `expected_inflow`) on every shop at
rolling cutoffs, with only the days before each cutoff visible. Bills, supplier credit and scheduled
payments are known amounts in the app, so they are not part of this test.
Compared against two naive forecasts a shopkeeper might use: "same as last week" and "the last
4 weeks' average".
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
# The docs sit beside the code, or one level up when the code has a folder of its own (the GitHub layout:
# khatapilot/ for the code, with docs/ and README.md beside it).
DOCS_ROOT = ROOT.parent if not (ROOT / "docs").is_dir() and (ROOT.parent / "docs").is_dir() else ROOT

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.modules.forecast.service import HISTORY_DAYS, Z_80, build_baseline, expected_inflow  # noqa: E402
from app.modules.ledger.schemas import DailyFlow  # noqa: E402
from data.generate_shops import OUT_DIR  # noqa: E402

RESULTS = DOCS_ROOT / "docs" / "results"
HORIZON = 14
STEP = 14


def _flows(rows: pd.DataFrame) -> dict[date, DailyFlow]:
    return {
        r.date: DailyFlow(sales_in=int(r.sales_cash + r.sales_mobile), baki_collected=int(r.baki_collected),
                          owner_drawings=int(r.drawings))
        for r in rows.itertuples(index=False)
    }


def backtest(daily: pd.DataFrame, shops: pd.DataFrame) -> dict:
    daily = daily.assign(date=pd.to_datetime(daily["date"]).dt.date,
                         inflow=daily["sales_cash"] + daily["sales_mobile"] + daily["baki_collected"])
    shop_type = dict(zip(shops["shop_id"], shops["shop_type"], strict=True))
    windows = []
    for shop_id, rows in daily.groupby("shop_id", sort=True):
        rows = rows.sort_values("date")
        flows = _flows(rows)
        actual = dict(zip(rows["date"], rows["inflow"], strict=True))
        first, last = rows["date"].iloc[0], rows["date"].iloc[-1]
        cutoff = first + timedelta(days=HISTORY_DAYS)
        while cutoff + timedelta(days=HORIZON - 1) <= last:
            history = {d: f for d, f in flows.items() if cutoff - timedelta(days=HISTORY_DAYS) <= d < cutoff}
            baseline = build_baseline(history, shop_type[shop_id], cutoff)
            days = [cutoff + timedelta(days=i) for i in range(HORIZON)]
            model = [expected_inflow(baseline, shop_type[shop_id], d) for d in days]
            last_week = [actual[cutoff - timedelta(days=7 - (i % 7))] for i in range(HORIZON)]
            mean_28 = np.mean([actual[cutoff - timedelta(days=k)] for k in range(1, 29)])
            truth = [actual[d] for d in days]
            band = Z_80 * np.sqrt(sum(baseline.sales_spread[d.weekday()] ** 2 + baseline.baki_spread ** 2
                                      for d in days) + sum(baseline.level_se[d.weekday()] for d in days) ** 2)
            windows.append({"shop_id": shop_id, "cutoff": cutoff, "truth": truth, "model": model,
                            "last_week": last_week, "mean_28": [mean_28] * HORIZON, "band": band})
            cutoff += timedelta(days=STEP)

    def score(name: str) -> dict:
        truth = np.array([w["truth"] for w in windows], dtype=float)
        pred = np.array([w[name] for w in windows], dtype=float)
        total_truth, total_pred = truth.sum(axis=1), pred.sum(axis=1)
        week1 = np.abs(pred[:, :7].sum(axis=1) - truth[:, :7].sum(axis=1)) / truth[:, :7].sum(axis=1)
        return {
            "daily_wape": float(np.abs(pred - truth).sum() / truth.sum()),
            "total_14d_median_ape": float(np.median(np.abs(total_pred - total_truth) / total_truth)),
            "total_14d_p90_ape": float(np.quantile(np.abs(total_pred - total_truth) / total_truth, 0.9)),
            "total_7d_median_ape": float(np.median(week1)),
            "bias": float((total_pred.sum() - total_truth.sum()) / total_truth.sum()),
        }

    truth_totals = np.array([sum(w["truth"]) for w in windows])
    model_totals = np.array([sum(w["model"]) for w in windows])
    bands = np.array([w["band"] for w in windows])
    return {
        "windows": len(windows),
        "shops": int(daily["shop_id"].nunique()),
        "horizon_days": HORIZON,
        "history_days": HISTORY_DAYS,
        "model": score("model"),
        "naive_last_week": score("last_week"),
        "naive_mean_28d": score("mean_28"),
        "band_80_coverage_14d_total": float(np.mean(np.abs(model_totals - truth_totals) <= bands)),
    }


def pct(value: float) -> str:
    return f"{value:.1%}"


def report(m: dict) -> str:
    rows = [("KhataPilot forecast", m["model"]), ("Same as last week", m["naive_last_week"]),
            ("Last 4 weeks' average", m["naive_mean_28d"])]
    lines = [
        "# Cash forecast: backtest",
        "",
        "Generated by `python scripts/evaluate_forecast.py`; do not edit by hand. **Synthetic data**: these",
        "numbers show the method works on the patterns we simulated, not how it will perform on real shops.",
        "",
        f"What is tested: the forecast's estimated money in (sales in cash and mobile money, plus baki coming back)",
        f"over the next {m['horizon_days']} days, at {m['windows']:,} rolling cutoffs across {m['shops']} shops,",
        f"using only the {m['history_days']} days before each cutoff. The test runs the app's own code",
        "(`build_baseline`, `expected_inflow`). Bills, supplier credit and scheduled payments are known amounts in",
        "the app, so they are not part of this test.",
        "",
        "| Forecast | Daily WAPE | 7-day total, median error | 14-day total, median error | 14-day, 90th pct error | Bias |",
        "|---|---|---|---|---|---|",
    ]
    for name, s in rows:
        lines.append(f"| {name} | {pct(s['daily_wape'])} | {pct(s['total_7d_median_ape'])} | "
                     f"{pct(s['total_14d_median_ape'])} | {pct(s['total_14d_p90_ape'])} | {s['bias']:+.1%} |")
    lines += [
        "",
        f"80% band: the 14-day total fell inside the forecast's 80% range in {pct(m['band_80_coverage_14d_total'])}"
        " of windows.",
        "",
        "How to read it: WAPE is total absolute error divided by total actual money in. The two-week total is",
        "what matters for a shortfall warning; single days are noisy for any method.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    daily = pd.read_csv(OUT_DIR / "daily.csv")
    shops = pd.read_csv(OUT_DIR / "shops.csv")
    metrics = backtest(daily, shops)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "forecast_backtest.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8", newline="\n")
    (RESULTS / "forecast_backtest.md").write_text(report(metrics), encoding="utf-8", newline="\n")
    print(report(metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
