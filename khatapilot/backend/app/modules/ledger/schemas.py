from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EntryKind = Literal[
    "sale",  # cash or mobile-money sale
    "baki_sale",  # sale on customer credit: no money moves yet
    "baki_repayment",  # a customer paying back baki
    "purchase",  # goods from a supplier (paid now, or on supplier credit)
    "supplier_payment",  # settling supplier credit
    "expense",
    "owner_drawing",
    "transfer",  # between the shop's own cash, wallets and bank
    "other_in",
    "other_out",
    "opening_balance",
]
Method = Literal["cash", "bkash", "nagad", "bank", "credit"]
LIQUID_METHODS: tuple[str, ...] = ("cash", "bkash", "nagad", "bank")
# Kinds that are real money in or out of the shop (transfers and opening balances are not).
CASHFLOW_EXCLUDED_KINDS = {"transfer", "opening_balance"}


class NewLedgerEntry(BaseModel):
    shop_id: str
    occurred_at: datetime
    kind: EntryKind
    direction: Literal["in", "out"]
    method: Method
    amount_paisa: int = Field(gt=0)
    category: str
    counterparty_name: str | None = None
    counterparty_number: str | None = None
    note: str | None = None
    due_date: date | None = None
    days_late: int | None = None
    trx_id: str | None = None
    source: Literal["seed", "owner_text", "sms", "invoice", "agent", "sandbox"]
    source_ref: str | None = None


class LedgerEntryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    occurred_at: datetime
    local_date: date
    kind: str
    direction: str
    method: str
    amount_paisa: int
    category: str
    counterparty_name: str | None
    counterparty_number: str | None
    note: str | None
    trx_id: str | None
    source: str


class DayTotals(BaseModel):
    day: date
    sales_paisa: int  # cash + mobile money sales (baki excluded)
    baki_given_paisa: int
    baki_collected_paisa: int
    money_in_paisa: int
    money_out_paisa: int
    net_paisa: int
    entries: int


class CustomerBalance(BaseModel):
    name: str
    number: str | None
    outstanding_paisa: int
    oldest_open_since: date | None
    days_outstanding: int | None


class ObligationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    counterparty_name: str
    counterparty_number: str | None
    method: str | None
    category: str
    amount_paisa: int
    due_date: date
    status: str
    note: str | None


class NewObligation(BaseModel):
    shop_id: str
    kind: Literal["supplier_payable", "bill"]
    counterparty_name: str
    counterparty_number: str | None = None
    method: str | None = None
    category: str
    amount_paisa: int = Field(gt=0)
    due_date: date
    note: str | None = None
    source_ref: str | None = None


class DailyFlow(BaseModel):
    """Money that actually moved on one day, split the way the cash forecast needs it."""

    sales_in: int = 0  # cash and mobile-money sales (baki excluded)
    baki_given: int = 0
    baki_collected: int = 0
    other_in: int = 0
    routine_out: int = 0  # cash-on-delivery stock, small costs, other payments
    owner_drawings: int = 0
    supplier_payments: int = 0  # settling supplier credit (forecast from payables instead)
    bills_paid: int = 0  # recurring bills (forecast from the bill schedule instead)


class BookDay(BaseModel):
    """One day of the books in the shape the credit model learns from (the generator's daily.csv).
    Amounts in paisa; the counts are payments to suppliers, on time or late."""

    day: date
    sales_cash: int = 0
    sales_mobile: int = 0
    sales_baki: int = 0
    baki_collected: int = 0
    purchases: int = 0  # all deliveries, paid on delivery or on credit
    expenses: int = 0
    drawings: int = 0
    supplier_paid: int = 0
    supplier_payments_on_time: int = 0
    supplier_payments_late: int = 0
    informal_borrowed: int = 0
    informal_repaid: int = 0
    baki_outstanding: int = 0  # at the end of the day
    payables_outstanding: int = 0
    closing_balance: int = 0


class RepaymentProfile(BaseModel):
    name: str
    number: str | None
    given_paisa: int
    repaid_paisa: int
    outstanding_paisa: int
    oldest_open_since: date | None
    average_days_to_repay: float | None  # amount-weighted, first-in-first-out
    repaid_share: float  # of credit given at least 45 days ago, how much came back


class SupplierCycle(BaseModel):
    name: str
    number: str | None
    method: str | None
    cycle_days: int
    usual_paisa: int
    terms_days: int
    last_delivery: date


class DuplicateSms(Exception):
    """This TrxID is already in the books."""


class BookedSms(BaseModel):
    kind: str
    entries: list[LedgerEntryView]
    counterparty: str | None
    customer_outstanding_paisa: int | None = None
    settled_payables: list[str] = []


class RecurringBillView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    payee: str
    category: str
    amount_paisa: int
    day_of_month: int
    method: str
