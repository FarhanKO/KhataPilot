from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class SavingsPlan(Base):
    """The owner's savings plan. No row: no plan yet. The share and its basis are set in the app or in
    chat; the account money goes to always needs the owner's PIN (in the Savings tab, or proposed in
    chat and confirmed with the PIN in the app)."""

    __tablename__ = "savings_plans"

    shop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    share_pct: Mapped[int | None] = mapped_column(default=None)  # None: paused
    share_set_at: Mapped[datetime | None] = mapped_column(default=None)
    # "last_month" (the default) or "three_months": the middle of the last three, for a steadier amount
    basis: Mapped[str] = mapped_column(String(12), default="last_month", server_default="last_month")
    account_name: Mapped[str | None] = mapped_column(String(64), default=None)
    account_number: Mapped[str | None] = mapped_column(String(32), default=None)
    account_method: Mapped[str | None] = mapped_column(String(8), default=None)
    account_set_at: Mapped[datetime | None] = mapped_column(default=None)
    # The share of profit the owner chose to put back into stock (the growth plan's stock path); None: let
    # KhataPilot size it from the books each month. It moves no money.
    reinvest_pct: Mapped[int | None] = mapped_column(default=None)
    reinvest_set_at: Mapped[datetime | None] = mapped_column(default=None)


class SavingsGoal(Base):
    """Something the owner is saving toward. Savings fill goals in order (`position`), so the first goal
    fills first. Removed goals are kept (status "removed"), so starter goals are only suggested once."""

    __tablename__ = "savings_goals"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(48))
    target_paisa: Mapped[int]
    by_month: Mapped[date | None] = mapped_column(default=None)  # first day of the month it should be met by
    kind: Mapped[str] = mapped_column(String(12), default="custom")  # custom | cushion | eid_stock | grow_shop
    set_by: Mapped[str] = mapped_column(String(12), default="owner")  # owner | khatapilot (a starter goal)
    why: Mapped[str | None] = mapped_column(String(400), default=None)
    position: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | removed
    created_at: Mapped[datetime]
    removed_at: Mapped[datetime | None] = mapped_column(default=None)
