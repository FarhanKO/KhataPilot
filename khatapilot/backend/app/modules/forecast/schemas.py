from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

ItemKind = Literal["bill", "supplier_bill", "expected_supplier_bill", "scheduled_payment"]


class ForecastItem(BaseModel):
    """A specific amount expected on a specific day (as opposed to the everyday baseline)."""

    key: str
    label: str
    amount_paisa: int
    direction: Literal["in", "out"]
    day: date
    kind: ItemKind
    certain: bool  # a bill or a scheduled payment, versus an estimate
    overdue: bool = False
    counterparty: str | None = None


class ForecastDay(BaseModel):
    day: date
    expected_in_paisa: int
    expected_out_paisa: int
    items: list[ForecastItem]
    balance_paisa: int
    low_paisa: int
    high_paisa: int
    below_buffer: bool


class Shortfall(BaseModel):
    day: date
    balance_paisa: int
    short_by_paisa: int  # how far below the safety buffer
    cause: str


class Suggestion(BaseModel):
    kind: Literal["collect_baki", "move_payment", "pause_withdrawals", "loan"]
    text: str
    amount_paisa: int
    new_lowest_paisa: int
    closes_gap: bool


class CashForecast(BaseModel):
    as_of: datetime
    days_ahead: int
    start_balance_paisa: int
    buffer_paisa: int
    days: list[ForecastDay]
    first_shortfall: Shortfall | None
    lowest_day: date
    lowest_balance_paisa: int
    suggestions: list[Suggestion]
    headline: str
    method: list[str]
