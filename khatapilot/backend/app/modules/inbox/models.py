from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class PhoneSms(Base):
    """One money SMS seen on the owner's phone. Other SMS (chats, OTPs, offers) are never stored, only
    counted, so the owner's private messages do not end up in the shop's books."""

    __tablename__ = "phone_sms"
    __table_args__ = (UniqueConstraint("shop_id", "body_sha256", name="uq_phone_sms_body"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    body_sha256: Mapped[str] = mapped_column(String(64))
    sender: Mapped[str] = mapped_column(String(40))  # the provider's sender ID, or a masked personal number
    sender_kind: Mapped[str] = mapped_column(String(12))  # provider | personal | other
    source: Mapped[str] = mapped_column(String(12))  # webhook | pull | import | share
    body: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(default=None)
    seen_at: Mapped[datetime]
    provider: Mapped[str | None] = mapped_column(String(8), default=None)
    trx_id: Mapped[str | None] = mapped_column(String(20), default=None, index=True)
    direction: Mapped[str | None] = mapped_column(String(4), default=None)
    amount_paisa: Mapped[int | None] = mapped_column(default=None)
    # booked | asked | review | duplicate | fake_sender | not_booked | ignored
    status: Mapped[str] = mapped_column(String(12))
    detail: Mapped[str] = mapped_column(Text, default="")
    decision_id: Mapped[str | None] = mapped_column(String(48), default=None)
    question_id: Mapped[str | None] = mapped_column(String(40), default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)


class PhoneInboxState(Base):
    """Per shop: when the phone was last read, and how many SMS were skipped as not about money."""

    __tablename__ = "phone_inbox_state"

    shop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_pull_at: Mapped[datetime | None] = mapped_column(default=None)
    last_seen_at: Mapped[datetime | None] = mapped_column(default=None)
    newest_received_at: Mapped[datetime | None] = mapped_column(default=None)
    skipped: Mapped[int] = mapped_column(default=0)
