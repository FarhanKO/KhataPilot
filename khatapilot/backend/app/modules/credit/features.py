"""Credit features from a shop's books: one function for training and for the app.

Input is a table of days with the columns of the synthetic shops' daily.csv (see
`ledger.schemas.BookDay`), amounts in taka. Training reads daily.csv; the app reads the ledger
through `LedgerService.book_days` (paisa, converted here). Shop type and region are never features.

Three features are shown to the owner and the lender but not scored: sales level, margin and the
mobile-money share. On the synthetic shops they did not predict repayment once behaviour was known,
and they differ by shop type and region, so scoring them only widened gaps between groups (the
ablation is in docs/results/credit_model.md).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

WINDOW_DAYS = 180
MIN_HISTORY_DAYS = 90


@dataclass(frozen=True)
class Feature:
    name: str
    label: str  # plain words for the owner and the lender
    monotone: int  # +1: a higher value means more risk, -1: less risk, 0: let the data decide
    unit: str  # how to show the value: taka, pct, days, months, count
    scored: bool = True  # False: shown in the profile, not used by the model


FEATURES: list[Feature] = [
    Feature("avg_daily_sales", "Average daily sales", -1, "taka", scored=False),
    Feature("sales_volatility", "Week-to-week swing in sales", +1, "pct"),
    Feature("sales_growth", "Sales growth (per year)", -1, "pct"),
    Feature("gross_margin", "Share of sales kept after stock costs", -1, "pct", scored=False),
    Feature("cash_buffer_days", "Days of costs covered by cash on hand", -1, "days"),
    Feature("baki_collection_rate", "Share of customer credit paid back", -1, "pct"),
    Feature("baki_days_outstanding", "Days customer credit stays unpaid", +1, "days"),
    Feature("supplier_on_time", "Supplier bills paid on time", -1, "pct"),
    Feature("mobile_money_share", "Sales through bKash/Nagad", 0, "pct", scored=False),
    Feature("months_of_history", "Months of records", -1, "months"),
    Feature("drawings_share", "Household withdrawals as a share of sales", +1, "pct"),
    Feature("informal_borrowing", "Times borrowed from relatives to pay bills (6 months)", +1, "count"),
]
NAMES = [f.name for f in FEATURES]
SCORED = [f for f in FEATURES if f.scored]
BY_NAME = {f.name: f for f in FEATURES}

COLUMNS = ("sales_cash", "sales_mobile", "sales_baki", "baki_collected", "purchases", "expenses", "drawings",
           "supplier_payments_on_time", "supplier_payments_late", "informal_borrowed", "baki_outstanding",
           "closing_balance")


def _ratio(numerator: float, denominator: float, default: float) -> float:
    return float(numerator / denominator) if denominator > 0 else default


def compute(table: Mapping[str, Sequence[float]], history_days: int | None = None) -> dict[str, float]:
    """Features over the last `WINDOW_DAYS` days of `table` (ordered by day, amounts in taka)."""
    total_days = len(table["sales_cash"])
    if total_days < MIN_HISTORY_DAYS:
        raise ValueError(f"At least {MIN_HISTORY_DAYS} days of records are needed (found {total_days}).")
    col = {c: np.asarray(table[c], dtype=float)[-WINDOW_DAYS:] for c in COLUMNS}
    sales = col["sales_cash"] + col["sales_mobile"] + col["sales_baki"]
    days = len(sales)

    weeks = sales[days % 7:].reshape(-1, 7).sum(axis=1)  # complete weeks, oldest first
    volatility = _ratio(weeks.std(), weeks.mean(), 1.0)
    if len(weeks) >= 4 and (weeks > 0).all():
        slope = np.polyfit(np.arange(len(weeks)), np.log(weeks), 1)[0]
        growth = float(np.exp(slope * 52) - 1)
    else:
        growth = 0.0
    costs_per_day = (col["purchases"].sum() + col["expenses"].sum()) / days
    on_time, late = col["supplier_payments_on_time"].sum(), col["supplier_payments_late"].sum()
    given = col["sales_baki"].sum()
    return {
        "avg_daily_sales": float(sales.mean()),
        "sales_volatility": float(np.clip(volatility, 0, 2)),
        "sales_growth": float(np.clip(growth, -0.9, 3)),
        "gross_margin": float(np.clip(1 - _ratio(col["purchases"].sum(), sales.sum(), 1.0), -0.5, 0.8)),
        "cash_buffer_days": float(np.clip(_ratio(np.median(col["closing_balance"]), costs_per_day, 0.0), 0, 60)),
        "baki_collection_rate": float(np.clip(_ratio(col["baki_collected"].sum(), given, 1.0), 0, 1.5)),
        "baki_days_outstanding": float(np.clip(_ratio(col["baki_outstanding"].mean(), given / days, 0.0), 0, 365)),
        "supplier_on_time": _ratio(on_time, on_time + late, 1.0),
        "mobile_money_share": _ratio(col["sales_mobile"].sum(), col["sales_cash"].sum() + col["sales_mobile"].sum(), 0.0),
        "months_of_history": round((history_days or total_days) / 30.4, 1),
        "drawings_share": float(np.clip(_ratio(col["drawings"].sum(), sales.sum(), 0.0), 0, 1)),
        "informal_borrowing": float((col["informal_borrowed"] > 0).sum()),
    }


def from_books(books: Sequence, history_days: int | None = None) -> dict[str, float]:
    """Features from `ledger.schemas.BookDay` rows (amounts in paisa)."""
    counts = {"supplier_payments_on_time", "supplier_payments_late"}
    table = {c: [getattr(b, c) / (1 if c in counts else 100) for b in books] for c in COLUMNS}
    return compute(table, history_days)


def vector(features: Mapping[str, float], names: Sequence[str]) -> np.ndarray:
    return np.array([[features[name] for name in names]], dtype=float)
