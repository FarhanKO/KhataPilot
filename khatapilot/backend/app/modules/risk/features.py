"""The feature vector Fraud Sentinel sees, shared by training and live scoring so they cannot drift
apart, with a plain-English phrase for each feature's contribution."""

from __future__ import annotations

import math

from app.modules.risk.schemas import PaymentFeatures

# Beyond this many past payments a payee is simply "well known"; capping keeps the feature from
# drifting just because histories grow over time.
HISTORY_CAP = 12

FEATURES = [
    "log_amount",
    "amount_to_daily_sales",
    "amount_to_payee_usual",
    "has_history",
    "log_history",
    "payee_trusted",
    "is_night",
    "hour_sin",
    "hour_cos",
    "payments_last_hour",
    "payments_last_day",
    "new_payees_last_day",
]


def vector(f: PaymentFeatures) -> list[float]:
    taka = f.amount_paisa / 100
    sales = max(f.average_daily_sales_paisa / 100, 1.0)
    usual = f.amount_paisa / f.payee_median_paisa if f.payee_median_paisa else 0.0
    angle = 2 * math.pi * f.local_hour / 24
    return [
        math.log1p(taka),
        taka / sales,
        usual,
        1.0 if f.payee_history_count > 0 else 0.0,
        math.log1p(min(f.payee_history_count, HISTORY_CAP)),
        1.0 if f.payee_trusted else 0.0,
        1.0 if f.local_hour < 6 or f.local_hour >= 23 else 0.0,
        math.sin(angle),
        math.cos(angle),
        float(f.payments_last_hour),
        float(f.payments_last_day),
        float(f.new_payees_last_day),
    ]


def phrase(feature: str, f: PaymentFeatures) -> str | None:
    """Why a feature pushed the risk *up*, in words an owner understands (None: not worth saying)."""
    sales = max(f.average_daily_sales_paisa, 1)
    if feature in ("log_amount", "amount_to_daily_sales"):
        days = f.amount_paisa / sales
        return f"the amount is {days:.1f} days of this shop's sales" if days >= 1 else None
    if feature == "amount_to_payee_usual" and f.payee_median_paisa:
        ratio = f.amount_paisa / f.payee_median_paisa
        return f"{ratio:.1f}x the usual payment to this payee" if ratio >= 1.5 else None
    if feature in ("has_history", "log_history"):
        return "the shop has never paid this number before" if f.payee_history_count == 0 else None
    if feature == "payee_trusted":
        return "the payee is not on the trusted list" if not f.payee_trusted else None
    if feature in ("is_night", "hour_sin", "hour_cos"):
        return "it is at night, when the shop is closed" if f.local_hour < 6 or f.local_hour >= 23 else None
    if feature == "payments_last_hour" and f.payments_last_hour:
        return f"{f.payments_last_hour} other payments in the last hour"
    if feature == "payments_last_day" and f.payments_last_day >= 3:
        return f"{f.payments_last_day} other payments today"
    if feature == "new_payees_last_day" and f.new_payees_last_day:
        return f"{f.new_payees_last_day} new payees in the last 24 hours"
    return None


def reassurance(f: PaymentFeatures) -> list[str]:
    """Why a low-risk payment looks normal."""
    reasons = []
    if f.payee_trusted and f.payee_history_count:
        reasons.append(f"a trusted payee the shop has paid {f.payee_history_count} times before")
    elif f.payee_history_count:
        reasons.append(f"the shop has paid this number {f.payee_history_count} times before")
    if f.payee_median_paisa and f.amount_paisa <= 1.5 * f.payee_median_paisa:
        reasons.append("within the usual amount for this payee")
    return reasons
