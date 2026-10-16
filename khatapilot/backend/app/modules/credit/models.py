from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class CreditConsent(Base):
    """One row per grant; withdrawing sets `revoked_at`. The text version shows what was agreed to."""

    __tablename__ = "credit_consents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(32))
    granted_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)


class CreditProfileRecord(Base):
    __tablename__ = "credit_profiles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime]
    model_version: Mapped[str] = mapped_column(String(32))
    probability: Mapped[float]
    band: Mapped[str] = mapped_column(String(1))
    profile_json: Mapped[str] = mapped_column(Text)
