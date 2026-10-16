from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        Index("ix_ledger_shop_day", "shop_id", "local_date"),
        # A forwarded SMS can only be booked once: its TrxID is unique per shop.
        Index("uq_ledger_trx", "shop_id", "trx_id", unique=True, sqlite_where=text("trx_id IS NOT NULL")),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime]
    local_date: Mapped[date]
    kind: Mapped[str] = mapped_column(String(24))
    direction: Mapped[str] = mapped_column(String(4))  # in | out
    method: Mapped[str] = mapped_column(String(8))  # cash | bkash | nagad | bank | credit
    amount_paisa: Mapped[int]
    category: Mapped[str] = mapped_column(String(64))
    counterparty_name: Mapped[str | None] = mapped_column(String(128), default=None)
    counterparty_number: Mapped[str | None] = mapped_column(String(32), default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    due_date: Mapped[date | None] = mapped_column(default=None)
    days_late: Mapped[int | None] = mapped_column(default=None)
    trx_id: Mapped[str | None] = mapped_column(String(32), default=None)
    source: Mapped[str] = mapped_column(String(16))  # seed | owner_text | sms | invoice | agent | sandbox
    source_ref: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime]


class Obligation(Base):
    """Money the shop has to pay on a date: a supplier payable or a bill."""

    __tablename__ = "ledger_obligations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # supplier_payable | bill
    counterparty_name: Mapped[str] = mapped_column(String(128))
    counterparty_number: Mapped[str | None] = mapped_column(String(32), default=None)
    method: Mapped[str | None] = mapped_column(String(8), default=None)
    category: Mapped[str] = mapped_column(String(64))
    amount_paisa: Mapped[int]
    due_date: Mapped[date]
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | settled | cancelled
    note: Mapped[str | None] = mapped_column(Text, default=None)
    source_ref: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime]
    settled_at: Mapped[datetime | None] = mapped_column(default=None)


class RecurringBill(Base):
    __tablename__ = "ledger_recurring_bills"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(64))
    payee: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64))
    amount_paisa: Mapped[int]
    day_of_month: Mapped[int]
    method: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime]
