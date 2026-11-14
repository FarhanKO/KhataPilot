from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

ReportKind = Literal["owner", "lender"]


class Line(BaseModel):
    label: str
    paisa: int
    note: str | None = None


class ReportMonth(BaseModel):
    month: str  # "2026-09"
    label: str  # "September 2026"
    finished: bool


class ShopFacts(BaseModel):
    name: str
    owner_name: str
    shop_type: str
    place: str  # area, district, division
    trading_since: date
    accounts: list[str]  # masked: "bKash 017XXXX0222"


class MonthFigures(BaseModel):
    """One month of the books, worked out the same way as the savings plan's profit."""

    month: date
    label: str
    sales_paisa: int  # cash and mobile-money sales
    sales_mobile_paisa: int
    baki_given_paisa: int  # goods given on credit
    baki_back_paisa: int  # baki paid back
    other_in_paisa: int
    stock_bought_paisa: int  # every delivery, paid or on credit
    stock_paid_paisa: int  # paid on delivery plus paid to suppliers for earlier deliveries
    running_costs_paisa: int
    costs: list[Line]  # running costs by category, biggest first
    household_paisa: int
    savings_paisa: int
    cash_profit_paisa: int
    gross_margin_pct: float | None  # 1 - stock bought / goods sold (baki sales included)
    closing_cash_paisa: int  # cash and accounts at the end of the month, in the books
    baki_outstanding_paisa: int  # at the end of the month
    payables_paisa: int  # owed to suppliers at the end of the month
    supplier_payments_on_time: int
    supplier_payments_late: int
    days: int  # days covered (fewer for a month still running)


class Week(BaseModel):
    label: str
    money_in_paisa: int
    money_out_paisa: int
    profit_paisa: int


class OwnerStatement(BaseModel):
    kind: Literal["owner"] = "owner"
    id: str
    generated_at: datetime
    month: str
    title: str
    period: str
    shop: ShopFacts
    headline: str
    figures: MonthFigures
    previous: MonthFigures | None
    money_in: list[Line]
    money_out: list[Line]
    own_money: list[Line]
    weeks: list[Week]
    baki: dict
    bills: dict
    payments: dict
    savings: dict
    forecast: str
    growth: dict | None
    notes: list[str]
    security: dict = {}  # filled from the audit log by the app
    integrity: dict = {}


class LenderStatement(BaseModel):
    kind: Literal["lender"] = "lender"
    id: str
    generated_at: datetime
    month: str
    title: str
    period: str
    shop: ShopFacts
    headline: str
    months: list[MonthFigures]
    averages: dict
    customer_credit: dict
    obligations: dict
    capacity: dict
    credit_profile: dict
    savings_record: dict
    notes: list[str]
    declaration: str
    integrity: dict = {}  # filled by the app: books against the provider, the audit chain, a fingerprint


class NoSuchMonth(Exception):
    """The month asked for has no books (before the shop's records start, or in the future)."""
