from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class ReminderRecord(Base):
    """A baki reminder the owner approved and KhataPilot sent (or tried to send) to a customer."""

    __tablename__ = "baki_reminders"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_name: Mapped[str] = mapped_column(String(128))
    to_number: Mapped[str] = mapped_column(String(16))
    owed_paisa: Mapped[int]
    days_outstanding: Mapped[int]
    language: Mapped[str] = mapped_column(String(12))
    body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(12))  # sent | failed
    sandbox_ref: Mapped[str | None] = mapped_column(String(32), default=None)
    failure: Mapped[str | None] = mapped_column(Text, default=None)
    decision_id: Mapped[str] = mapped_column(String(48))
    sent_at: Mapped[datetime]


class ReminderOptOut(Base):
    """A customer who gets no baki reminders: they replied STOP, or asked the owner, who recorded it.

    A customer's own STOP can only be lifted by the customer (replying START); the owner can lift only an
    opt-out the owner recorded. No row: reminders allowed."""

    __tablename__ = "reminder_opt_outs"

    shop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    number: Mapped[str] = mapped_column(String(16), primary_key=True)  # normalized 8801XXXXXXXXX
    customer_name: Mapped[str | None] = mapped_column(String(128), default=None)
    source: Mapped[str] = mapped_column(String(12))  # customer | owner
    words: Mapped[str | None] = mapped_column(String(160), default=None)  # what the customer replied
    opted_out_at: Mapped[datetime]
