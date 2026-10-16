"""Tables owned by the guard. Nothing outside `app.guard` reads or writes them."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class PolicyLimits(Base):
    __tablename__ = "guard_limits"

    shop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    per_txn_paisa: Mapped[int]
    daily_paisa: Mapped[int]
    fraud_medium: Mapped[float]
    fraud_high: Mapped[float]
    split_window_hours: Mapped[int]
    velocity_max_per_hour: Mapped[int]
    updated_at: Mapped[datetime]


class TrustedPayee(Base):
    __tablename__ = "guard_trusted_payees"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    number: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | removed
    added_via: Mapped[str] = mapped_column(String(16))  # seed | owner_pin
    added_at: Mapped[datetime]


class BlockedNumber(Base):
    __tablename__ = "guard_blocked_numbers"

    shop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    number: Mapped[str] = mapped_column(String(32), primary_key=True)
    reason: Mapped[str] = mapped_column(Text)
    added_at: Mapped[datetime]


class OwnerSecurity(Base):
    __tablename__ = "guard_owner_security"

    shop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pin_hash: Mapped[str | None] = mapped_column(String(256), default=None)
    failed_attempts: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(default=None)
    updated_at: Mapped[datetime]


class KillSwitchState(Base):
    __tablename__ = "guard_kill_switch"

    shop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    frozen: Mapped[bool] = mapped_column(default=False)
    since: Mapped[datetime | None] = mapped_column(default=None)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    actor: Mapped[str | None] = mapped_column(String(64), default=None)


class OwnerIntentRecord(Base):
    """A payment the owner asked for in their own words. Each authorizes at most one payment."""

    __tablename__ = "guard_owner_intents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64))
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(64))
    intent_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    consumed_by: Mapped[str | None] = mapped_column(String(48), default=None)


class PendingAction(Base):
    """An action waiting for the owner: Approve (tier 1) or confirm plus PIN (tier 3)."""

    __tablename__ = "guard_pending_actions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    decision_id: Mapped[str] = mapped_column(String(48))
    request_id: Mapped[str] = mapped_column(String(64))
    conversation_id: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str] = mapped_column(String(48))
    kind: Mapped[str] = mapped_column(String(24))
    action_json: Mapped[str] = mapped_column(Text)
    action_hash: Mapped[str] = mapped_column(String(64))
    tier: Mapped[int]
    requires_pin: Mapped[bool]
    summary: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | executed | rejected | expired | failed
    result_json: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)

