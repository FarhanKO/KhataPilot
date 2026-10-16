"""Synthetic outgoing mobile-money payments for training Fraud Sentinel (mobile-money edition).

PaySim-style in spirit (a simulator of mobile-money behaviour with injected fraud), but our own
code and our own typologies, matched to how micro-shops in Bangladesh actually get robbed:

    legit_supplier      trusted suppliers on their usual cycle and amount
    legit_bill          rent and electricity, monthly
    legit_regular       known, untrusted numbers paid regularly (the helper's wage, small top-ups)
    legit_new_small     occasional one-off payments to someone new (a repairman, a van)
    legit_new_supplier  onboarding a real new supplier: a large first payment to a new number
    legit_new_large     a genuine big one-off to someone new (a freezer, a signboard)
    scam_large          social engineering: one large transfer to a stranger, often at night
    scam_small          a small fake fee (prize, delivery) to a stranger: hard to tell from normal
    takeover_burst      a stolen phone: several payments to new numbers within an hour
    spoof_supplier      "our number changed": the usual supplier amount sent to a new number
    structuring         several payments just under the per-payment limit to one new number

Every row carries the same raw fields the live guard computes, so training and scoring match.

Run:  python -m data.generate_payments [--shops 400] [--days 60] [--seed 7]
"""

from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUT = Path(__file__).resolve().parent / "generated" / "payments.csv"
FRAUD_TYPES = {"scam_large", "scam_small", "takeover_burst", "spoof_supplier", "structuring"}


@dataclass
class Event:
    t: float  # hours since the start of the window
    payee: str
    trusted: bool
    amount: float  # taka
    typology: str


def _day_hour(rng: np.random.Generator, low: float = 9, high: float = 20) -> float:
    return float(rng.uniform(low, high))


def simulate_shop(index: int, sales: float, days: int, rng: np.random.Generator) -> list[dict]:
    events: list[Event] = []
    history: dict[str, list[float]] = {}

    suppliers = [("s1", 7, 0.45 * 7), ("s2", 14, 0.25 * 14), ("s3", 7, 0.18 * 7)]
    usual = {}
    for name, cycle, share in suppliers:
        mean = sales * 0.85 * share * float(rng.uniform(0.8, 1.2))
        usual[name] = mean
        history[name] = list(mean * rng.lognormal(0, 0.1, int(rng.integers(8, 20))))
        offset = int(rng.integers(0, cycle))
        for day in range(offset, days, cycle):
            events.append(Event(day * 24 + _day_hour(rng), name, True, mean * float(rng.lognormal(0, 0.12)),
                                "legit_supplier"))

    for name, fraction, trusted_p in [("landlord", float(rng.uniform(1.0, 1.6)), 0.5), ("electric", 0.25, 0.5)]:
        trusted = bool(rng.random() < trusted_p)
        history[name] = [sales * fraction] * int(rng.integers(4, 9))
        bill_day = int(rng.integers(0, 28))
        for day in range(bill_day, days, 30):
            events.append(Event(day * 24 + _day_hour(rng), name, trusted,
                                sales * fraction * float(rng.normal(1, 0.05)), "legit_bill"))

    history["helper"] = [sales * 0.6] * int(rng.integers(3, 8))
    history["topup"] = list(sales * 0.05 * rng.lognormal(0, 0.3, 20))
    for day in range(int(rng.integers(0, 30)), days, 30):
        events.append(Event(day * 24 + _day_hour(rng, 18, 21), "helper", False, sales * 0.6, "legit_regular"))
    for day in range(int(rng.integers(0, 7)), days, 7):
        events.append(Event(day * 24 + _day_hour(rng, 8, 22), "topup", False,
                            sales * 0.05 * float(rng.lognormal(0, 0.3)), "legit_regular"))

    for k in range(int(rng.poisson(days / 15))):
        events.append(Event(int(rng.integers(0, days)) * 24 + _day_hour(rng, 9, 21), f"new{k}", False,
                            sales * float(rng.uniform(0.03, 0.6)), "legit_new_small"))
    if rng.random() < 0.3:
        day = float(rng.integers(0, days))
        events.append(Event(day * 24 + _day_hour(rng, 10, 18), "newsupplier", False,
                            sales * float(rng.uniform(1.0, 2.5)), "legit_new_supplier"))
    if rng.random() < 0.4:  # a freezer, a signboard, a new wholesaler's first order: large, new, but genuine
        day = float(rng.integers(0, days))
        events.append(Event(day * 24 + _day_hour(rng, 9, 21), "bigbuy", False,
                            sales * float(rng.uniform(1.0, 4.0)), "legit_new_large"))

    if rng.random() < 0.4:
        kind = str(rng.choice(sorted(FRAUD_TYPES)))
        day = float(rng.integers(0, days))
        night = rng.random() < 0.5
        start = day * 24 + (float(rng.uniform(0, 5)) if night else _day_hour(rng, 8, 22))
        if kind == "scam_large":
            events.append(Event(start, "scam", False, sales * float(rng.uniform(1.5, 6)), kind))
        elif kind == "scam_small":  # "pay ৳500 delivery fee to claim your prize": looks like an ordinary new payee
            events.append(Event(day * 24 + _day_hour(rng, 9, 21), "feescam", False,
                                sales * float(rng.uniform(0.03, 0.3)), kind))
        elif kind == "takeover_burst":
            for j in range(int(rng.integers(3, 8))):
                events.append(Event(start + j * float(rng.uniform(0.05, 0.2)), f"thief{j}", False,
                                    sales * float(rng.uniform(0.3, 1.5)), kind))
        elif kind == "spoof_supplier":
            victim = str(rng.choice([s[0] for s in suppliers]))
            for j in range(int(rng.integers(1, 3))):
                events.append(Event(day * 24 + _day_hour(rng) + j * 24, "spoofed", False,
                                    usual[victim] * float(rng.normal(1, 0.08)), kind))
        else:  # structuring
            for j in range(int(rng.integers(3, 6))):
                events.append(Event(start + j * float(rng.uniform(0.2, 0.5)), "mule", False,
                                    float(rng.uniform(8000, 9900)), kind))

    events.sort(key=lambda e: e.t)
    rows = []
    for i, event in enumerate(events):
        prior = [e for e in events[:i] if e.t > event.t - 24]
        past = history.get(event.payee, [])
        rows.append({
            "shop": index,
            "day": int(event.t // 24),
            "local_hour": int(event.t % 24),
            "amount_paisa": int(round(max(event.amount, 10) * 100)),
            "payee_trusted": event.trusted,
            "payee_history_count": len(past),
            "payee_median_paisa": int(statistics.median(past) * 100) if past else 0,
            "average_daily_sales_paisa": int(sales * float(rng.normal(1, 0.05)) * 100),
            "payments_last_hour": sum(1 for e in prior if e.t > event.t - 1),
            "payments_last_day": len(prior),
            "new_payees_last_day": len({e.payee for e in prior if not e.trusted}),
            "typology": event.typology,
            "label": int(event.typology in FRAUD_TYPES),
        })
        history.setdefault(event.payee, []).append(event.amount)
    return rows


def generate(n_shops: int = 400, days: int = 60, seed: int = 7, shops_csv: Path | None = None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    if shops_csv and shops_csv.exists():
        sales = pd.read_csv(shops_csv).query("is_hero == 0")["base_sales"].to_numpy(dtype=float)
    else:
        sales = 9000 * rng.lognormal(0, 0.5, 400)
    rows = []
    for index in range(n_shops):  # more simulated shops than profiles: reuse a profile's sales level
        rows += simulate_shop(index, float(sales[index % len(sales)]), days, np.random.default_rng([seed, index]))
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic payments for Fraud Sentinel.")
    parser.add_argument("--shops", type=int, default=400)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    frame = generate(args.shops, args.days, args.seed, OUT.parent / "shops.csv")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT, index=False)
    print(f"{len(frame)} payments, {frame['label'].mean():.2%} fraud -> {OUT}")
    print(frame["typology"].value_counts().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
