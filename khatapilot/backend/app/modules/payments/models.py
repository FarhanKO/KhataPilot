from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class PaymentRecord(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    payee_name: Mapped[str | None] = mapped_column(String(128), default=None)
    payee_number: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(8))
    amount_paisa: Mapped[int]
    execute_on: Mapped[date]
    status: Mapped[str] = mapped_column(String(12))  # scheduled | paused | executing | completed | failed | cancelled
    decision_id: Mapped[str] = mapped_column(String(48))
    request_id: Mapped[str] = mapped_column(String(64))
    approved_tier: Mapped[int]
    approved_with_pin: Mapped[bool] = mapped_column(default=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    sandbox_txn_id: Mapped[str | None] = mapped_column(String(32), default=None)
    failure: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime]
    executed_at: Mapped[datetime | None] = mapped_column(default=None)
