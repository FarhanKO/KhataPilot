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
