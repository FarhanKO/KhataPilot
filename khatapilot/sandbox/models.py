"""Sandbox tables: accounts, transactions, outbound messages, loan applications, used
authorizations, the operation log and simulator state."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from sandbox.db import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. "bkash:01711000222"
    provider: Mapped[str] = mapped_column(String(16))  # bkash | nagad | bank
    number: Mapped[str] = mapped_column(String(32))
    holder_name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), default="personal")  # merchant | personal | bank
    balance_paisa: Mapped[int] = mapped_column(default=0)
    frozen: Mapped[bool] = mapped_column(default=False)
    frozen_reason: Mapped[str | None] = mapped_column(Text, default=None)
    frozen_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime]


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (Index("ix_txn_account_time", "account_id", "occurred_at"),)

    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trx_id: Mapped[str] = mapped_column(String(16), index=True)  # shared by both legs of a transfer
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    direction: Mapped[str] = mapped_column(String(8))  # debit | credit
    txn_type: Mapped[str] = mapped_column(String(24))
    amount_paisa: Mapped[int]
    fee_paisa: Mapped[int] = mapped_column(default=0)
    balance_after_paisa: Mapped[int]
    counterparty_number: Mapped[str | None] = mapped_column(String(32), default=None)
    counterparty_name: Mapped[str | None] = mapped_column(String(128), default=None)
    reference: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(16), default="completed")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), default=None)
    authorization_id: Mapped[str | None] = mapped_column(String(64), default=None)
    decision_id: Mapped[str | None] = mapped_column(String(64), default=None)
    sms_text: Mapped[str | None] = mapped_column(Text, default=None)
    occurred_at: Mapped[datetime]


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    from_account: Mapped[str] = mapped_column(String(64))
    to_number: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(16))  # sms | messenger
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="delivered")
    idempotency_key: Mapped[str] = mapped_column(String(128))
    authorization_id: Mapped[str] = mapped_column(String(64))
    decision_id: Mapped[str | None] = mapped_column(String(64), default=None)
    sent_at: Mapped[datetime]


class LoanApplication(Base):
    __tablename__ = "loan_applications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    applicant_account: Mapped[str] = mapped_column(String(64))
    shop_name: Mapped[str] = mapped_column(String(128))
    product_id: Mapped[str] = mapped_column(String(64))
    amount_paisa: Mapped[int]
    tenure_months: Mapped[int]
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="received")
    idempotency_key: Mapped[str] = mapped_column(String(128))
    authorization_id: Mapped[str] = mapped_column(String(64))
    decision_id: Mapped[str | None] = mapped_column(String(64), default=None)
    submitted_at: Mapped[datetime]


class UsedAuthorization(Base):
    """One row per executed guard authorization. Makes every token single-use and every
    idempotency key produce at most one side effect."""

    __tablename__ = "used_authorizations"

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    token_id: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    params_hash: Mapped[str] = mapped_column(String(64))
    decision_id: Mapped[str | None] = mapped_column(String(64), default=None)
    response_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]


class OpLogEntry(Base):
    """The provider's operation log: the execution evidence for every request, accepted or not."""

    __tablename__ = "oplog"

    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    at: Mapped[datetime]  # simulated time
    wall_at: Mapped[datetime]
    operation: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # ok | rejected | replayed
    code: Mapped[str] = mapped_column(String(48))
    detail: Mapped[str] = mapped_column(Text)
    account_id: Mapped[str | None] = mapped_column(String(64), default=None)
    counterparty: Mapped[str | None] = mapped_column(String(64), default=None)  # masked
    amount_paisa: Mapped[int | None] = mapped_column(default=None)
    ref_id: Mapped[str | None] = mapped_column(String(32), default=None)
    authorization_id: Mapped[str | None] = mapped_column(String(64), default=None)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), default=None)
    decision_id: Mapped[str | None] = mapped_column(String(64), default=None)


class SimState(Base):
    __tablename__ = "sim_state"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
