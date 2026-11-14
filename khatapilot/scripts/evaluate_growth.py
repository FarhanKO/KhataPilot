"""Backtest the growth plan's "keep going as now" path on the synthetic shops and write
docs/results/growth_backtest.*.

    python scripts/evaluate_growth.py

What is tested, using exactly the app's code (`MonthBooks`, `gross_margin`, `everyday`, `seasonal_extras`,
`project`): at every month boundary with at least four finished months behind it, the plan sees
only those months (up to six) and projects monthly cash profit for the next three. That is compared with
what the shop really made, and with two naive guesses: "same as last month" and "the last three months'
average". The margin estimate is also checked against the margin the generator gave each shop.

What is NOT tested: the "put profit back into stock" and "collect baki" paths. They depend on the owner
changing what they do, and the synthetic shops never do, so there is nothing to compare them with. Their
rule (extra stock earns the margin on half of it) stays a stated, deliberately modest assumption.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
DOCS_ROOT = ROOT.parent if not (ROOT / "docs").is_dir() and (ROOT.parent / "docs").is_dir() else ROOT

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.modules.growth import plan as G  # noqa: E402
from data.generate_shops import OUT_DIR  # noqa: E402

RESULTS = DOCS_ROOT / "docs" / "results"
MIN_HISTORY, MAX_HISTORY, HORIZON = 4, 6, 3


def month_books(rows: pd.DataFrame) -> list[G.MonthBooks]:
    """Full calendar months only, in paisa (daily.csv is in taka), the same fields the app's ledger gives."""
    rows = rows.assign(month=pd.to_datetime(rows["date"]).dt.to_period("M"))
    out = []
    for period, m in rows.groupby("month", sort=True):
        first = period.to_timestamp().date()
        if len(m) < G.month_end(first).day:  # a part month at either end
            continue
        out.append(G.MonthBooks(
            month=first, sales=int(m.sales_cash.sum() + m.sales_mobile.sum()) * 100,
            baki_sales=int(m.sales_baki.sum()) * 100, baki_back=int(m.baki_collected.sum()) * 100,
            supplier_paid=int(m.supplier_paid.sum()) * 100, purchases=int(m.purchases.sum()) * 100,
            costs={"Running costs": int(m.expenses.sum()) * 100}, drawings=int(m.drawings.sum()) * 100))
    return out


def backtest(daily: pd.DataFrame, shops: pd.DataFrame) -> dict:
    kinds = dict(zip(shops["shop_id"], shops["shop_type"], strict=True))
    true_margin = dict(zip(shops["shop_id"], shops["margin"], strict=True))
    cases, margins = [], []
    for shop_id, rows in daily.groupby("shop_id", sort=True):
        months = month_books(rows.sort_values("date"))
        kind = kinds[shop_id]
        for k in range(MIN_HISTORY, len(months)):
            history = months[max(0, k - MAX_HISTORY):k]
            margin = G.gross_margin(history[-3:])
            usual = G.everyday(history, [G.month_factor(kind, m.month) for m in history], margin)
            first = months[k].month
            ahead = months[k:k + HORIZON]
            seasonal = G.seasonal_extras(kind, first, len(ahead), usual, margin)
            band = usual.band
            path = G.project(base=usual.base, monthly_trend=usual.trend, margin=margin, reinvest_pct=0,
                             start_capital=0, months=len(ahead), band=band, first=first, seasonal=seasonal)
            last, mean3 = history[-1].profit, float(np.mean([m.profit for m in history[-3:]]))
            for h, (actual, guess) in enumerate(zip(ahead, path), start=1):
                cases.append({"h": h, "money_in": actual.money_in, "actual": actual.profit,
                              "model": guess.profit_paisa, "last_month": last, "mean_3": mean3,
                              "inside": guess.low_paisa <= actual.profit <= guess.high_paisa})
        if len(months) >= 3:
            margins.append((G.gross_margin(months[-3:]), float(true_margin[shop_id])))

    frame = pd.DataFrame(cases)

    def score(name: str, part: pd.DataFrame) -> dict:
        error = (part[name] - part["actual"]).abs() / part["money_in"]
        return {"median_error_pct_of_money_in": float(error.median()),
                "p90_error_pct_of_money_in": float(error.quantile(0.9))}

    by_h = {}
    for h in range(1, HORIZON + 1):
        part = frame[frame.h == h]
        model_err = (part["model"] - part["actual"]).abs()
        by_h[h] = {"cases": int(len(part)), "model": score("model", part), "last_month": score("last_month", part),
                   "mean_3": score("mean_3", part),
                   "beats_last_month": float((model_err < (part["last_month"] - part["actual"]).abs()).mean()),
                   "beats_mean_3": float((model_err < (part["mean_3"] - part["actual"]).abs()).mean()),
                   "band_80_coverage": float(part["inside"].mean())}
    est, true = np.array([m[0] for m in margins]), np.array([m[1] for m in margins])
    return {
        "shops": int(daily["shop_id"].nunique()), "cases": int(len(frame)), "horizon_months": HORIZON,
        "by_horizon": by_h,
        "margin": {"shops": len(margins), "median_abs_error_points": float(np.median(np.abs(est - true)) * 100),
                   "correlation": float(np.corrcoef(est, true)[0, 1])},
        "as_of": date.today().isoformat(),
    }


def pct(value: float) -> str:
    return f"{value:.1%}"


def report(m: dict) -> str:
    lines = [
        "# Growth plan: backtest",
        "",
        "Generated by `python scripts/evaluate_growth.py`; do not edit by hand. **Synthetic data**: these numbers",
        "show the method works on the patterns we simulated, not how it will perform on real shops.",
        "",
        f"What is tested: the \"keep going as now\" path's monthly cash profit, {m['horizon_months']} months ahead, at",
        f"{m['cases']:,} month-by-month cases across {m['shops']} shops, seeing only the months before each cutoff",
        "(four to six). The test runs the app's own growth code. Errors are shown as a share of that month's money",
        "in, because profit itself can sit near zero.",
        "",
        "| Months ahead | Cases | KhataPilot, median error | Same as last month | Last 3 months' average | Beats last month | Beats 3-month average | 80% band holds |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for h, s in m["by_horizon"].items():
        lines.append(f"| {h} | {s['cases']:,} | {pct(s['model']['median_error_pct_of_money_in'])} | "
                     f"{pct(s['last_month']['median_error_pct_of_money_in'])} | "
                     f"{pct(s['mean_3']['median_error_pct_of_money_in'])} | {pct(s['beats_last_month'])} | "
                     f"{pct(s['beats_mean_3'])} | {pct(s['band_80_coverage'])} |")
    mg = m["margin"]
    lines += [
        "",
        f"Margin estimate (1 - stock bought / goods sold, last three months) against each shop's true margin: median",
        f"error {mg['median_abs_error_points']:.1f} points, correlation {mg['correlation']:.2f}, over {mg['shops']} shops.",
        "",
        "Not tested: the \"put profit back into stock\" and \"collect baki\" paths. They depend on the owner changing",
        "what they do, which the synthetic shops never do; their rule stays a stated, deliberately modest assumption.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    daily = pd.read_csv(OUT_DIR / "daily.csv")
    shops = pd.read_csv(OUT_DIR / "shops.csv")
    shops = shops[shops["is_hero"] == 0] if "is_hero" in shops else shops
    daily = daily[daily["shop_id"].isin(shops["shop_id"])]
    metrics = backtest(daily, shops)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "growth_backtest.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8", newline="\n")
    (RESULTS / "growth_backtest.md").write_text(report(metrics), encoding="utf-8", newline="\n")
    print(report(metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
