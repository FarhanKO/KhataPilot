"""Synthetic micro-shop books: a reproducible population of shops plus the hand-tuned hero shop.

Each shop is simulated day by day: sales (cash, bKash/Nagad, baki), customers repaying baki,
supplier deliveries on credit or cash, bills, the owner's withdrawals, and the cash position by
method (drawer, bKash, Nagad, bank). Liquidity has consequences: when cash is short, supplier
payments slip and essential bills force informal borrowing. Each shop has a hidden "true"
repayment behaviour that becomes the credit label.

Outputs in data/generated/:
    shops.csv              one row per shop: profile, hidden parameters, credit label
    daily.csv              one row per shop per day (amounts in whole taka)
    hero_ledger.csv        the hero shop's transaction-level ledger
    hero_parties.json      the hero shop's suppliers and customers
    hero_obligations.json  bills, supplier payables and open baki after the last day of data
    meta.json              seed, dates, counts

Run:  python -m data.generate_shops [--shops 400] [--seed 42]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.hero_shop import HERO_SHOP_ID, hero_spec
from data.specs import CustomerSpec, ExpenseSpec, OneOffSpec, ShopSpec, SupplierSpec

GENERATOR_VERSION = "1.0"
DEFAULT_SEED = 42
DEFAULT_SHOPS = 400
AS_OF = date(2026, 10, 11)  # last full day of data; the demo's "today" is Mon 12 Oct 2026
HERO_HISTORY_DAYS = 345
OUT_DIR = Path(__file__).resolve().parent / "generated"
BDT = timezone(timedelta(hours=6), name="BDT")
METHODS = ("cash", "bkash", "nagad", "bank")

# 2026 calendar (Bangladesh). Moon-sighting dates are approximate, which is fine for synthetic data.
RAMADAN = (date(2026, 2, 18), date(2026, 3, 19))
EID_FITR = date(2026, 3, 20)
EID_ADHA = date(2026, 5, 27)
POHELA_BOISHAKH = date(2026, 4, 14)

WEEKDAY_FACTOR = {  # Monday .. Sunday; Friday is the weekly holiday
    "grocery": [1.00, 0.97, 1.00, 1.10, 0.92, 1.06, 1.00],
    "pharmacy": [1.03, 1.00, 1.00, 1.02, 0.80, 1.05, 1.05],
    "mobile_recharge": [1.00, 1.00, 1.00, 1.08, 0.85, 1.05, 1.02],
    "tea_stall": [1.00, 1.00, 1.00, 1.05, 0.95, 1.10, 1.00],
}

SHOP_TYPES = {
    #                  weight, median daily sales, sigma, base volatility, margin, sd, mobile, baki
    "grocery": dict(weight=0.45, sales=9_000, sigma=0.45, vol=0.15, margin=0.15, margin_sd=0.025, mobile=0.35, baki=0.15),
    "pharmacy": dict(weight=0.22, sales=7_500, sigma=0.50, vol=0.18, margin=0.19, margin_sd=0.035, mobile=0.30, baki=0.08),
    "mobile_recharge": dict(weight=0.15, sales=12_000, sigma=0.45, vol=0.17, margin=0.045, margin_sd=0.01, mobile=0.65, baki=0.04),
    "tea_stall": dict(weight=0.18, sales=3_500, sigma=0.40, vol=0.20, margin=0.32, margin_sd=0.05, mobile=0.10, baki=0.12),
}
TYPE_SUFFIX = {"grocery": "Store", "pharmacy": "Pharmacy", "mobile_recharge": "Telecom", "tea_stall": "Tea Stall"}

REGIONS = [  # division, district, urbanicity, areas, weight
    ("Dhaka", "Dhaka", "urban", ["Mirpur", "Mohammadpur", "Old Dhaka", "Badda", "Jatrabari", "Uttara"], 0.26),
    ("Dhaka", "Gazipur", "semi_urban", ["Tongi", "Board Bazar"], 0.09),
    ("Dhaka", "Narayanganj", "semi_urban", ["Chashara", "Fatullah"], 0.07),
    ("Chattogram", "Chattogram", "urban", ["Agrabad", "Halishahar", "Bahaddarhat"], 0.12),
    ("Sylhet", "Sylhet", "urban", ["Zindabazar", "Amberkhana"], 0.07),
    ("Rajshahi", "Rajshahi", "semi_urban", ["Shaheb Bazar", "Uposhohor"], 0.07),
    ("Khulna", "Khulna", "semi_urban", ["Daulatpur", "Sonadanga"], 0.07),
    ("Rangpur", "Kurigram", "rural", ["Ulipur", "Chilmari"], 0.08),
    ("Barishal", "Bhola", "rural", ["Char Fasson", "Lalmohan"], 0.08),
    ("Mymensingh", "Netrokona", "rural", ["Kendua", "Mohanganj"], 0.09),
]
URBAN_SALES_SCALE = {"urban": 1.2, "semi_urban": 1.0, "rural": 0.7}
URBAN_MOBILE_SCALE = {"urban": 1.15, "semi_urban": 1.0, "rural": 0.75}

FIRST_NAMES = ["Abdul", "Mohammad", "Rafiqul", "Shahidul", "Nurul", "Kamrul", "Anwar", "Jahid", "Rashed",
               "Mizan", "Shafiq", "Hasan", "Tariq", "Sabina", "Nasrin", "Rehana", "Shapla", "Dilara",
               "Parveen", "Momena", "Sumon", "Liton", "Ripon", "Sujon", "Polash", "Gopal", "Shyamal"]
LAST_NAMES = ["Hossain", "Rahman", "Islam", "Akter", "Uddin", "Mia", "Khan", "Sarkar", "Begum", "Das",
              "Chowdhury", "Sheikh", "Talukder", "Mondal", "Haque", "Ali", "Karim", "Saha", "Bhuiyan"]


# --------------------------------------------------------------------------- calendar

def season_factor(shop_type: str, day: date) -> float:
    factor = 1.0
    in_ramadan = RAMADAN[0] <= day <= RAMADAN[1]
    to_fitr = (EID_FITR - day).days
    to_adha = (EID_ADHA - day).days
    if shop_type == "grocery":
        if in_ramadan:
            factor *= 1.22
        if 1 <= to_fitr <= 7:
            factor *= 1.35
        if 1 <= to_adha <= 5:
            factor *= 1.25
    elif shop_type == "pharmacy":
        if day.month in (7, 8, 9):
            factor *= 1.10
    elif shop_type == "mobile_recharge":
        if 1 <= to_fitr <= 7 or 1 <= to_adha <= 5:
            factor *= 1.40
    elif shop_type == "tea_stall":
        if in_ramadan:
            factor *= 0.55
        if day.month in (12, 1):
            factor *= 1.15
    if day in (EID_FITR, EID_ADHA):
        factor *= 0.15
    elif day in (EID_FITR + timedelta(1), EID_ADHA + timedelta(1)):
        factor *= 0.45
    elif day in (EID_FITR + timedelta(2), EID_ADHA + timedelta(2)):
        factor *= 0.75
    if day == POHELA_BOISHAKH and shop_type in ("grocery", "tea_stall"):
        factor *= 1.15
    return factor


def bill_day(year: int, month: int, day_of_month: int) -> int:
    """Bills due on the 31st fall on the last day of shorter months."""
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    return min(day_of_month, (next_month - timedelta(1)).day)


# --------------------------------------------------------------------------- simulation

@dataclass
class _Baki:
    customer: int
    amount: int
    issued_day: int
    repay_day: int | None  # None: never repaid
    repaid: bool = False


@dataclass
class _Payable:
    supplier: int
    amount: int
    delivered_day: int
    due_day: int
    planned_delay: int
    entry_ref: str | None = None


@dataclass
class SimResult:
    daily: list[dict[str, Any]]
    entries: list[dict[str, Any]]
    end_balances: dict[str, int]
    open_payables: list[dict[str, Any]]
    open_baki: list[dict[str, Any]]
    stats: dict[str, float]


class ShopSimulator:
    def __init__(self, spec: ShopSpec, rng: np.random.Generator, *, detailed: bool = False) -> None:
        self.spec = spec
        self.rng = rng
        self.detailed = detailed
        self.start = AS_OF - timedelta(days=spec.history_days - 1)
        self.balances = {m: int(spec.opening.get(m, 0)) for m in METHODS}
        self.entries: list[dict[str, Any]] = []
        self.baki: list[_Baki] = []
        self.repay_schedule: dict[int, list[_Baki]] = defaultdict(list)
        self.payables: list[_Payable] = []
        self.accum = [spec.base_sales * (1 - spec.margin) * s.cogs_share * s.cycle_days * 0.5 for s in spec.suppliers]
        self.informal_debt = 0
        self.informal_events = 0
        self.recent_sales: list[int] = []
        self.mobile_pool = [self._random_number() for _ in range(150)] if detailed else []
        self.seq = 0

    # ---- helpers

    def _random_number(self) -> str:
        return f"01{self.rng.integers(3, 10)}{self.rng.integers(0, 10**8):08d}"

    def _stamp(self, day: date, hour: float) -> str:
        minutes = int(hour * 60)
        return datetime.combine(day, time(minutes // 60, minutes % 60), tzinfo=BDT).isoformat()

    def _entry(self, day: date, hour: float, **fields: Any) -> str | None:
        if not self.detailed:
            return None
        self.seq += 1
        ref = f"seed-{self.spec.shop_id}-{self.seq:06d}"
        self.entries.append({
            "ref": ref,
            "occurred_at": self._stamp(day, hour),
            "counterparty_name": None,
            "counterparty_number": None,
            "due_date": None,
            "days_late": None,
            "trx_id": None,
            "note": None,
            **fields,
        })
        return ref

    def _trx_id(self, method: str) -> str | None:
        if method not in ("bkash", "nagad"):
            return None
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        body = "".join(alphabet[i] for i in self.rng.integers(0, len(alphabet), 9))
        return ("B" if method == "bkash" else "N") + body

    def total(self) -> int:
        return sum(self.balances.values())

    def _move(self, day: date, source: str, target: str, amount: int, label: str) -> None:
        self.balances[source] -= amount
        self.balances[target] += amount
        hour = 12.0 + self.rng.random() * 6
        self._entry(day, hour, kind="transfer", direction="out", method=source, amount=amount,
                    category="Transfer", note=label)
        self._entry(day, hour, kind="transfer", direction="in", method=target, amount=amount,
                    category="Transfer", note=label)

    def _pay(self, day: date, amount: int, preferred: str) -> str | None:
        """Pay from the preferred method, falling back to others, consolidating if needed."""
        order = [preferred] + [m for m in METHODS if m != preferred]
        for method in order:
            if self.balances[method] >= amount:
                self.balances[method] -= amount
                return method
        if self.total() < amount:
            return None
        need = amount - self.balances[preferred]
        for method in sorted((m for m in METHODS if m != preferred), key=lambda m: -self.balances[m]):
            if need <= 0:
                break
            take = min(need, self.balances[method])
            if take > 0:
                self._move(day, method, preferred, take, f"Moved money to pay ({method} to {preferred})")
                need -= take
        self.balances[preferred] -= amount
        return preferred

    def _buffer_target(self) -> float:
        return self.spec.buffer_days * self.spec.base_sales

    # ---- one day

    def run(self) -> SimResult:
        spec = self.spec
        for method, amount in self.balances.items():
            if amount:
                self._entry(self.start, 7.0, kind="opening_balance", direction="in", method=method, amount=amount,
                            category="Opening balance")
        delivery_offset = [
            next(i for i in range(7) if (self.start + timedelta(i)).weekday() == s.first_delivery_weekday)
            if s.cycle_days > 1 else 0
            for s in spec.suppliers
        ]
        weekly_fixed = sum(e.amount for e in spec.expenses) * 12 / 52
        daily_rows = []
        stats = defaultdict(float)

        for i in range(spec.history_days):
            day = self.start + timedelta(i)
            row = defaultdict(int)
            row["date"] = day.isoformat()

            # 1. sales
            level = spec.base_sales * math.exp(spec.growth * i / 365)
            level *= WEEKDAY_FACTOR[spec.shop_type][day.weekday()] * season_factor(spec.shop_type, day)
            if spec.shock and spec.shock[0] <= i < spec.shock[0] + spec.shock[1]:
                level *= 1 - spec.shock[2]
            sales = max(0, int(round(level * self.rng.lognormal(-spec.volatility**2 / 2, spec.volatility))))
            mobile_frac = float(np.clip(spec.mobile_share * self.rng.normal(1, 0.10), 0, 0.95))
            baki_frac = float(np.clip(spec.baki_share * self.rng.normal(1, 0.25), 0, 0.6))
            if mobile_frac + baki_frac > 0.97:
                scale = 0.97 / (mobile_frac + baki_frac)
                mobile_frac, baki_frac = mobile_frac * scale, baki_frac * scale
            mobile = int(round(sales * mobile_frac))
            baki_amount = int(round(sales * baki_frac))
            cash_sales = sales - mobile - baki_amount
            self.recent_sales = (self.recent_sales + [sales])[-7:]
            row.update(sales_total=sales, sales_cash=cash_sales, sales_mobile=mobile, sales_baki=baki_amount)

            self.balances["cash"] += cash_sales
            if cash_sales:
                self._entry(day, 21.5, kind="sale", direction="in", method="cash", amount=cash_sales,
                            category="Sales", note="Cash sales (day total)")
            self._mobile_receipts(day, mobile)

            # 2. baki given today
            self._issue_baki(day, i, baki_amount)

            # 3. baki repaid today
            collected = 0
            for item in self.repay_schedule.pop(i, []):
                method = "cash" if self.rng.random() < 0.6 else "bkash"
                self.balances[method] += item.amount
                item.repaid = True
                collected += item.amount
                customer = spec.customers[item.customer]
                self._entry(day, 9 + self.rng.random() * 12, kind="baki_repayment", direction="in", method=method,
                            amount=item.amount, category="Baki collected", counterparty_name=customer.name,
                            counterparty_number=customer.number, trx_id=self._trx_id(method))
            row["baki_collected"] = collected

            # 4. deliveries from suppliers
            cogs = sales * (1 - spec.margin)
            for k, supplier in enumerate(spec.suppliers):
                self.accum[k] += cogs * supplier.cogs_share
                if i >= delivery_offset[k] and (i - delivery_offset[k]) % supplier.cycle_days == 0:
                    amount = max(0, int(round(self.accum[k] * self.rng.normal(1.0, 0.05))))
                    self.accum[k] = 0.0
                    if amount:
                        self._deliver(day, i, k, supplier, amount, row)

            # 5. fixed bills (rent, utilities, wages); borrow informally if they cannot be paid
            for bill in spec.expenses:
                if day.day == bill_day(day.year, day.month, bill.day_of_month):
                    amount = bill.amount if bill.category != "Utilities" else int(round(bill.amount * self.rng.normal(1, 0.08)))
                    self._pay_essential(day, bill, amount, row)

            # 6. one-off events (a family wedding, a new freezer) and then supplier payables, oldest first
            for event in spec.one_offs:
                if event.on == day.isoformat():
                    self._one_off(day, event, row)
            self._settle_payables(day, i, row)

            # 7. small daily costs
            misc = max(0, int(round(sales * spec.misc_expense_rate * self.rng.normal(1, 0.2))))
            if misc and self.balances["cash"] >= misc:
                self.balances["cash"] -= misc
                row["expenses"] += misc
                self._entry(day, 13.0, kind="expense", direction="out", method="cash", amount=misc,
                            category="Transport & misc", note="Van fare, packaging, tea")

            # 8. the owner's weekly withdrawal (Fridays) and informal-loan repayments (Saturdays)
            if day.weekday() == 4:
                self._withdraw(day, weekly_fixed, row)
            if day.weekday() == 5 and self.informal_debt and self.total() > self._buffer_target() + 1_000:
                amount = min(self.informal_debt, 1_000)
                method = self._pay(day, amount, "cash")
                if method:
                    self.informal_debt -= amount
                    row["informal_repaid"] += amount
                    self._entry(day, 18.0, kind="other_out", direction="out", method=method, amount=amount,
                                category="Informal loan", note="Repaid relative")

            # 9. cash management: bank the surplus, top up bKash
            if self.balances["cash"] > 25_000:
                self._move(day, "cash", "bank", int((self.balances["cash"] - 12_000) // 1_000 * 1_000), "Bank deposit")
            if self.balances["bkash"] < spec.wallet_float and self.balances["cash"] > 8_000:
                top_up = min(self.balances["cash"] - 6_000, spec.wallet_float - self.balances["bkash"] + 3_000)
                top_up = int(top_up // 1_000 * 1_000)
                if top_up > 0:
                    self._move(day, "cash", "bkash", top_up, "Cash in to bKash")

            row["baki_outstanding"] = sum(b.amount for b in self.baki if not b.repaid)
            row["payables_outstanding"] = sum(p.amount for p in self.payables)
            row["closing_balance"] = self.total()
            for method in METHODS:
                row[f"balance_{method}"] = self.balances[method]
                stats["min_balance_" + method] = min(stats.get("min_balance_" + method, 0), self.balances[method])
            daily_rows.append(dict(row))

        return SimResult(
            daily=daily_rows,
            entries=self.entries,
            end_balances=dict(self.balances),
            open_payables=[self._payable_view(p) for p in self.payables],
            open_baki=[self._baki_view(b) for b in self.baki if not b.repaid],
            stats={**stats, "informal_events": self.informal_events},
        )

    # ---- pieces of a day

    def _mobile_receipts(self, day: date, mobile: int) -> None:
        if mobile <= 0:
            return
        n = max(1, int(self.rng.poisson(mobile / 650))) if self.detailed else 1
        weights = self.rng.dirichlet(np.full(n, 2.0))
        amounts = [int(round(mobile * w)) for w in weights]
        amounts[-1] += mobile - sum(amounts)
        for amount in amounts:
            if amount <= 0:
                continue
            method = "nagad" if self.rng.random() < self.spec.nagad_share else "bkash"
            self.balances[method] += amount
            if self.detailed:
                self._entry(day, 8 + self.rng.random() * 14, kind="sale", direction="in", method=method, amount=amount,
                            category="Sales", counterparty_number=str(self.rng.choice(self.mobile_pool)),
                            trx_id=self._trx_id(method), note="Customer payment")

    def _issue_baki(self, day: date, i: int, amount: int) -> None:
        if amount <= 0:
            return
        customers = self.spec.customers
        weights = np.array([c.weight for c in customers], dtype=float)
        count = int(min(len(customers), 1 + self.rng.poisson(amount / 450)))
        chosen = self.rng.choice(len(customers), size=count, replace=False, p=weights / weights.sum())
        shares = self.rng.dirichlet(np.full(count, 2.0))
        parts = [int(round(amount * s)) for s in shares]
        parts[-1] += amount - sum(parts)
        for idx, part in zip(chosen, parts, strict=True):
            if part <= 0:
                continue
            customer = customers[int(idx)]
            repays = self.rng.random() < customer.reliability
            delay = max(1, int(round(self.rng.gamma(2.0, customer.repay_days / 2.0))))
            item = _Baki(int(idx), part, i, i + delay if repays else None)
            self.baki.append(item)
            if item.repay_day is not None:
                self.repay_schedule[item.repay_day].append(item)
            self._entry(day, 9 + self.rng.random() * 12, kind="baki_sale", direction="in", method="credit",
                        amount=part, category="Sales on credit (baki)", counterparty_name=customer.name,
                        counterparty_number=customer.number)

    def _deliver(self, day: date, i: int, k: int, supplier: SupplierSpec, amount: int, row: dict) -> None:
        row["purchases"] += amount
        planned_delay = 0 if self.rng.random() < self.spec.supplier_on_time else int(self.rng.integers(1, 8))
        if supplier.terms_days == 0 and planned_delay == 0:
            method = self._pay(day, amount, supplier.method)
            if method:
                row["supplier_paid"] += amount
                row["supplier_payments_on_time"] += 1
                self._entry(day, 8.0, kind="purchase", direction="out", method=method, amount=amount,
                            category=supplier.category, counterparty_name=supplier.name,
                            counterparty_number=supplier.number, trx_id=self._trx_id(method),
                            note="Paid on delivery")
                return
        due = i + supplier.terms_days
        ref = self._entry(day, 8.0, kind="purchase", direction="out", method="credit", amount=amount,
                          category=supplier.category, counterparty_name=supplier.name,
                          counterparty_number=supplier.number,
                          due_date=(self.start + timedelta(due)).isoformat(), note="On supplier credit")
        self.payables.append(_Payable(k, amount, i, due, planned_delay, ref))

    def _settle_payables(self, day: date, i: int, row: dict) -> None:
        for payable in sorted(self.payables, key=lambda p: p.due_day):
            if payable.due_day + payable.planned_delay > i:
                continue
            supplier = self.spec.suppliers[payable.supplier]
            preferred = supplier.method if supplier.method != "cash" else "cash"
            method = self._pay(day, payable.amount, preferred)
            if method is None:
                continue  # cannot afford today: the payment slips
            self.payables.remove(payable)
            late = i - payable.due_day
            row["supplier_paid"] += payable.amount
            row["supplier_payments_late" if late > 0 else "supplier_payments_on_time"] += 1
            self._entry(day, 11.0, kind="supplier_payment", direction="out", method=method, amount=payable.amount,
                        category=supplier.category, counterparty_name=supplier.name,
                        counterparty_number=supplier.number, trx_id=self._trx_id(method),
                        due_date=(self.start + timedelta(payable.due_day)).isoformat(), days_late=max(0, late),
                        note=f"Settles delivery of {(self.start + timedelta(payable.delivered_day)).isoformat()}")

    def _pay_essential(self, day: date, bill: ExpenseSpec, amount: int, row: dict) -> None:
        if self.total() < amount:
            borrow = amount - self.total() + 2_000
            self.balances["cash"] += borrow
            self.informal_debt += borrow
            self.informal_events += 1
            row["informal_borrowed"] += borrow
            self._entry(day, 9.0, kind="other_in", direction="in", method="cash", amount=borrow,
                        category="Informal loan", note="Borrowed from a relative to pay " + bill.name.lower())
        method = self._pay(day, amount, bill.method)
        row["expenses"] += amount
        self._entry(day, 10.0, kind="expense", direction="out", method=method, amount=amount,
                    category=bill.category, counterparty_name=bill.payee, note=bill.name)

    def _one_off(self, day: date, event: OneOffSpec, row: dict) -> None:
        if self.total() < event.amount:
            borrow = event.amount - self.total() + 2_000
            self.balances["cash"] += borrow
            self.informal_debt += borrow
            self.informal_events += 1
            row["informal_borrowed"] += borrow
            self._entry(day, 9.0, kind="other_in", direction="in", method="cash", amount=borrow,
                        category="Informal loan", note="Borrowed from a relative")
        method = self._pay(day, event.amount, event.method)
        row["drawings" if event.kind == "owner_drawing" else "expenses"] += event.amount
        self._entry(day, 17.0, kind=event.kind, direction="out", method=method, amount=event.amount,
                    category=event.category, note=event.note)

    def _withdraw(self, day: date, weekly_fixed: float, row: dict) -> None:
        spec = self.spec
        gross_week = sum(self.recent_sales) * spec.margin
        profit = gross_week - weekly_fixed - sum(self.recent_sales) * spec.misc_expense_rate
        base = max(0.0, spec.drawings_rate * profit)
        surplus = self.total() - self._buffer_target()
        draw = base + max(0.0, surplus - base) * 0.5
        floor = min(self._buffer_target(), self.total()) * (0.5 + 0.5 * spec.discipline)
        draw = min(draw, max(0.0, self.total() - floor))
        draw = int(draw * self.rng.normal(1.0, 0.1)) // 100 * 100
        if draw <= 0:
            return
        method = self._pay(day, draw, "cash")
        if method:
            row["drawings"] += draw
            self._entry(day, 20.0, kind="owner_drawing", direction="out", method=method, amount=draw,
                        category="Owner withdrawal", note="Household expenses")

    def _payable_view(self, payable: _Payable) -> dict[str, Any]:
        supplier = self.spec.suppliers[payable.supplier]
        return {
            "supplier": supplier.name,
            "number": supplier.number,
            "method": supplier.method,
            "amount": payable.amount,
            "delivered_on": (self.start + timedelta(payable.delivered_day)).isoformat(),
            "due_on": (self.start + timedelta(payable.due_day)).isoformat(),
            "entry_ref": payable.entry_ref,
        }

    def _baki_view(self, item: _Baki) -> dict[str, Any]:
        customer = self.spec.customers[item.customer]
        return {
            "customer": customer.name,
            "number": customer.number,
            "amount": item.amount,
            "given_on": (self.start + timedelta(item.issued_day)).isoformat(),
        }


# --------------------------------------------------------------------------- population

def _clip(value: float, low: float, high: float) -> float:
    return float(min(max(value, low), high))


def make_shop(index: int, seed: int) -> ShopSpec:
    rng = np.random.default_rng([seed, 1, index])
    types = list(SHOP_TYPES)
    shop_type = str(rng.choice(types, p=[SHOP_TYPES[t]["weight"] for t in types]))
    params = SHOP_TYPES[shop_type]
    region = REGIONS[int(rng.choice(len(REGIONS), p=[r[4] for r in REGIONS]))]
    division, district, urbanicity, areas, _ = region
    discipline = float(rng.beta(4, 2))
    history_days = int(rng.integers(183, 366))
    base = params["sales"] * URBAN_SALES_SCALE[urbanicity] * float(rng.lognormal(0, params["sigma"]))
    margin = _clip(rng.normal(params["margin"], params["margin_sd"]), 0.02, 0.45)
    mobile = _clip(rng.normal(params["mobile"] * URBAN_MOBILE_SCALE[urbanicity], 0.10), 0.02, 0.9)
    baki = _clip(rng.normal(params["baki"], 0.04) * (1 + 0.6 * (1 - discipline)), 0.0, 0.45)
    collect_rate = _clip(0.985 - 0.14 * (1 - discipline) + rng.normal(0, 0.015), 0.6, 0.995)
    collect_days = _clip(4 + 16 * (1 - discipline) + rng.normal(0, 2), 2, 40)
    gross_month = base * 30 * margin
    owner_first, owner_last = str(rng.choice(FIRST_NAMES)), str(rng.choice(LAST_NAMES))

    def number() -> str:
        return f"01{rng.integers(3, 10)}{rng.integers(0, 10**8):08d}"

    n_customers = int(rng.integers(10, 35))
    customers = [
        CustomerSpec(
            name=f"Customer {k + 1}",
            number=number(),
            repay_days=_clip(rng.gamma(4, collect_days / 4), 1, 60),
            reliability=_clip(rng.normal(collect_rate, 0.03), 0.5, 1.0),
            weight=float(rng.lognormal(0, 0.5)),
        )
        for k in range(n_customers)
    ]
    suppliers = [
        SupplierSpec("Main wholesaler", number(), "bkash", "Stock purchases", 0.55, 7, int(rng.integers(0, 7)), 7),
        SupplierSpec("Distributor", f"{rng.integers(10**12, 10**13)}", "bank", "Stock purchases", 0.30, 14,
                     int(rng.integers(0, 7)), 14),
        SupplierSpec("Local supplier", None, "cash", "Stock purchases", 0.15, 2, 0, 0),
    ]
    expenses = [
        ExpenseSpec("Shop rent", "Landlord", "Rent", int(round(gross_month * rng.uniform(0.22, 0.4), -2)),
                    int(rng.integers(1, 16)), "cash"),
        ExpenseSpec("Electricity bill", "Electricity", "Utilities", int(round(gross_month * rng.uniform(0.04, 0.08), -1)),
                    int(rng.integers(5, 25)), "bkash"),
    ]
    if gross_month > 45_000:
        expenses.append(ExpenseSpec("Helper's salary", "Helper", "Wages", int(round(gross_month * rng.uniform(0.12, 0.18), -2)),
                                    1, "cash"))
    shock = None
    if rng.random() < 0.15:
        shock = (int(rng.integers(30, history_days - 40)), int(rng.integers(20, 60)), float(rng.uniform(0.25, 0.5)))
    return ShopSpec(
        shop_id=f"shop-{index:04d}",
        name=f"{owner_last} {TYPE_SUFFIX[shop_type]}",
        owner_name=f"{owner_first} {owner_last}",
        shop_type=shop_type,
        area=str(rng.choice(areas)),
        district=district,
        division=division,
        urbanicity=urbanicity,
        history_days=history_days,
        base_sales=base,
        growth=float(rng.normal(0.06, 0.12)),
        volatility=params["vol"] + float(rng.uniform(0, 0.12)) + (1 - discipline) * 0.08,
        margin=margin,
        mobile_share=mobile,
        nagad_share=float(rng.uniform(0.1, 0.45)),
        baki_share=baki,
        collect_rate=collect_rate,
        supplier_on_time=_clip(0.55 + 0.42 * discipline + rng.normal(0, 0.05), 0.3, 0.99),
        buffer_days=_clip(0.8 + 4 * discipline + rng.normal(0, 0.7), 0.2, 8),
        drawings_rate=_clip(0.5 + 0.4 * (1 - discipline) + rng.normal(0, 0.08), 0.2, 1.1),
        misc_expense_rate=float(rng.uniform(0.008, 0.02)),
        discipline=discipline,
        shock=shock,
        opening={"cash": int(base * 0.6), "bkash": int(base * mobile * 1.5), "nagad": int(base * mobile * 0.4),
                 "bank": int(base * 1.5 * discipline)},
        suppliers=suppliers,
        customers=customers,
        expenses=expenses,
        owner_phone=number(),
        bkash_number=number(),
        nagad_number=number(),
        bank_account=f"{rng.integers(10**12, 10**13)}",
    )


def credit_label(spec: ShopSpec, result: SimResult, rng: np.random.Generator) -> tuple[float, int]:
    """Hidden truth: would this shop default on a 12-month working-capital loan?"""
    z = (
        -2.7
        + 2.6 * (0.65 - spec.discipline)
        + 2.2 * (spec.volatility - 0.22)
        - 1.6 * spec.growth
        + 1.1 * (spec.shock is not None)
        + 0.7 * (result.stats["informal_events"] > 0)
        + 6.0 * (0.95 - spec.collect_rate)
        + 1.2 * (spec.drawings_rate - 0.6)
        - 0.05 * (spec.history_days / 30 - 9)
        + rng.normal(0, 0.45)
    )
    pd_true = 1 / (1 + math.exp(-z))
    return pd_true, int(rng.random() < pd_true)


# --------------------------------------------------------------------------- outputs

def shop_row(spec: ShopSpec, pd_true: float | None, defaulted: int | None) -> dict[str, Any]:
    row = {
        "shop_id": spec.shop_id, "name": spec.name, "owner_name": spec.owner_name, "shop_type": spec.shop_type,
        "area": spec.area, "district": spec.district, "division": spec.division, "urbanicity": spec.urbanicity,
        "history_days": spec.history_days, "first_day": (AS_OF - timedelta(spec.history_days - 1)).isoformat(),
        "base_sales": round(spec.base_sales), "growth": round(spec.growth, 4),
        "volatility": round(spec.volatility, 4), "margin": round(spec.margin, 4),
        "mobile_share": round(spec.mobile_share, 4), "baki_share": round(spec.baki_share, 4),
        "collect_rate": round(spec.collect_rate, 4), "supplier_on_time": round(spec.supplier_on_time, 4),
        "buffer_days": round(spec.buffer_days, 3), "drawings_rate": round(spec.drawings_rate, 4),
        "discipline": round(spec.discipline, 4), "had_shock": int(spec.shock is not None),
        "is_hero": int(spec.shop_id == HERO_SHOP_ID),
        "true_pd": None if pd_true is None else round(pd_true, 4),
        "defaulted": defaulted,
    }
    return row


def generate(n_shops: int = DEFAULT_SHOPS, seed: int = DEFAULT_SEED, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    shop_rows, daily_frames = [], []
    for index in range(n_shops):
        spec = make_shop(index, seed)
        result = ShopSimulator(spec, np.random.default_rng([seed, 2, index])).run()
        pd_true, defaulted = credit_label(spec, result, np.random.default_rng([seed, 3, index]))
        shop_rows.append(shop_row(spec, pd_true, defaulted))
        frame = pd.DataFrame(result.daily)
        frame.insert(0, "shop_id", spec.shop_id)
        daily_frames.append(frame)

    hero = hero_spec(HERO_HISTORY_DAYS)
    hero_result = ShopSimulator(hero, np.random.default_rng([seed, 2, 999_999]), detailed=True).run()
    shop_rows.append(shop_row(hero, None, None))  # the hero's label is what we want to predict
    hero_daily = pd.DataFrame(hero_result.daily)
    hero_daily.insert(0, "shop_id", hero.shop_id)
    daily_frames.append(hero_daily)

    shops = pd.DataFrame(shop_rows)
    daily = pd.concat(daily_frames, ignore_index=True).fillna(0)
    int_columns = [c for c in daily.columns if c not in ("shop_id", "date")]
    daily[int_columns] = daily[int_columns].astype("int64")
    shops.to_csv(out_dir / "shops.csv", index=False)
    daily.to_csv(out_dir / "daily.csv", index=False)
    pd.DataFrame(hero_result.entries).to_csv(out_dir / "hero_ledger.csv", index=False)

    parties = {
        "shop": {k: v for k, v in asdict(hero).items() if k not in ("suppliers", "customers", "expenses")},
        "suppliers": [asdict(s) for s in hero.suppliers],
        "customers": [asdict(c) for c in hero.customers],
        "expenses": [asdict(e) for e in hero.expenses],
    }
    (out_dir / "hero_parties.json").write_text(json.dumps(parties, indent=2, ensure_ascii=False), encoding="utf-8")

    upcoming_bills = []
    for offset in range(1, 32):
        day = AS_OF + timedelta(offset)
        for bill in hero.expenses:
            if day.day == bill_day(day.year, day.month, bill.day_of_month):
                upcoming_bills.append({"name": bill.name, "payee": bill.payee, "category": bill.category,
                                       "amount": bill.amount, "due_on": day.isoformat(), "method": bill.method})
    obligations = {
        "as_of": AS_OF.isoformat(),
        "balances": hero_result.end_balances,
        "supplier_payables": hero_result.open_payables,
        "upcoming_bills": upcoming_bills,
        "open_baki": hero_result.open_baki,
    }
    (out_dir / "hero_obligations.json").write_text(json.dumps(obligations, indent=2, ensure_ascii=False), encoding="utf-8")

    labelled = shops[shops["defaulted"].notna()]
    meta = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "as_of": AS_OF.isoformat(),
        "shops": int(len(shops)),
        "training_shops": int(len(labelled)),
        "default_rate": round(float(labelled["defaulted"].mean()), 4),
        "daily_rows": int(len(daily)),
        "hero_ledger_entries": len(hero_result.entries),
        "hero_end_balances": hero_result.end_balances,
        "synthetic": True,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shops", type=int, default=DEFAULT_SHOPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    meta = generate(args.shops, args.seed, args.out)
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
