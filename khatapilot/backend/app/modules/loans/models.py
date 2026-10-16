from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class LoanApplicationRecord(Base):
    """An application the owner approved and KhataPilot sent to the (simulated) lender."""

    __tablename__ = "loan_applications"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(64))
    product_name: Mapped[str] = mapped_column(String(128))
    amount_paisa: Mapped[int]
    tenure_months: Mapped[int]
    purpose: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(12))  # submitted | failed
    sandbox_ref: Mapped[str | None] = mapped_column(String(32), default=None)
    failure: Mapped[str | None] = mapped_column(Text, default=None)
    decision_id: Mapped[str] = mapped_column(String(48))
    submitted_at: Mapped[datetime]
