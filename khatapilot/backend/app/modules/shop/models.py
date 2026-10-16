from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    owner_name: Mapped[str] = mapped_column(String(128))
    shop_type: Mapped[str] = mapped_column(String(32))
    area: Mapped[str] = mapped_column(String(64))
    district: Mapped[str] = mapped_column(String(64))
    division: Mapped[str] = mapped_column(String(64))
    urbanicity: Mapped[str] = mapped_column(String(16))
    owner_phone: Mapped[str] = mapped_column(String(16))
    bkash_account: Mapped[str] = mapped_column(String(64))
    nagad_account: Mapped[str] = mapped_column(String(64))
    bank_account: Mapped[str] = mapped_column(String(64))
    data_since: Mapped[date]
    created_at: Mapped[datetime]


class Party(Base):
    __tablename__ = "shop_parties"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # supplier | customer
    name: Mapped[str] = mapped_column(String(128))
    number: Mapped[str | None] = mapped_column(String(32), default=None)
    method: Mapped[str | None] = mapped_column(String(16), default=None)
    category: Mapped[str | None] = mapped_column(String(64), default=None)
    terms_days: Mapped[int | None] = mapped_column(default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime]
