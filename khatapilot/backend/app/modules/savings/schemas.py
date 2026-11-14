from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

MIN_SHARE_PCT, MAX_SHARE_PCT = 1, 50
SavingsBasis = Literal["last_month", "three_months"]
SavingsState = Literal["no_plan", "no_profit", "no_account", "done", "ready", "partial", "wait"]


class SavingsAccount(BaseModel):
    """Where savings go: the owner's own account, saved in the app with their PIN."""

    name: str
    number: str  # unmasked: only the guard and the payment use it; views show `number_masked`
    number_masked: str
    method: Literal["bkash", "nagad", "bank"]
    set_at: datetime


class SavingsMonth(BaseModel):
    month: date  # the first day of the month
    label: str  # "Oct 2026"
    profit_paisa: int
    target_paisa: int  # the share of the month before's profit
    saved_paisa: int
    so_far: bool = False  # the current month, still running


class ScaleUp(BaseModel):
    """A suggestion to save a bigger share once profit has grown for a while."""

    from_pct: int
    to_pct: int
    growth_pct: int
    new_target_paisa: int
    text: str


GoalKind = Literal["custom", "cushion", "eid_stock", "grow_shop"]
MAX_GOALS = 8


class GoalDraft(BaseModel):
    """A starter goal worked out from the books: the name, the amount and why, all from code."""

    kind: GoalKind
    name: str
    target_paisa: int
    by_month: date | None = None
    why: str


class GoalView(BaseModel):
    id: str
    name: str
    kind: GoalKind
    set_by: Literal["owner", "khatapilot"]
    target_paisa: int
    saved_paisa: int  # savings counted toward it (goals fill in order)
    remaining_paisa: int
    pct: int
    by_month: date | None
    months_left: int | None  # at this month's savings pace; None when nothing is being saved
    on_track: bool | None  # with a date: will this pace meet it?
    reached: bool
    why: str | None
    text: str  # one line, ready to show or say
    created_at: datetime


class GoalNotFound(Exception):
    """No goal (or more than one) matches the name the owner gave."""


class SavingsJump(BaseModel):
    """Last month's profit was far from the shop's usual month, so the target does not just follow it."""

    direction: Literal["up", "down"]
    last_profit_paisa: int  # last month's profit
    usual_profit_paisa: int  # the middle of the three months before it
    base_paisa: int  # what this month's target is a share of
    text: str


class SavingsStatus(BaseModel):
    as_of: datetime
    state: SavingsState
    headline: str
    share_pct: int | None
    account: SavingsAccount | None
    month: date  # this month
    based_on: date  # last month (with basis three_months, the last of the three)
    basis: SavingsBasis = "last_month"
    basis_text: str = ""  # what the target is a share of, in words
    last_month_profit_paisa: int  # the profit the target is a share of
    target_paisa: int
    saved_paisa: int  # moved this month
    pending_paisa: int  # scheduled or paused, not moved yet
    remaining_paisa: int
    safe_now_paisa: int  # what can move today with cash staying above the safety buffer
    suggested_paisa: int  # what KhataPilot would move today
    buffer_paisa: int
    lowest_day: date
    lowest_balance_paisa: int
    history: list[SavingsMonth]
    scale_up: ScaleUp | None
    method: list[str]
    saved_total_paisa: int = 0  # everything moved to savings so far, which the goals share
    goals: list[GoalView] = []
    goals_headline: str = ""
    jump: SavingsJump | None = None  # last month's profit jumped: how the target was kept steady


class NoSavingsPlan(Exception):
    """There is nothing to move: no plan, no account, no profit, done, or cash too tight."""
