from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Stage = Literal["cushion_first", "shrinking", "ready_to_grow", "steady", "up_and_down", "choppy"]
Trend = Literal["growing", "flat", "shrinking"]


class FlowPart(BaseModel):
    key: str
    label: str
    amount_paisa: int


class MoneyFlow(BaseModel):
    """One finished month: money in, where it went, and what happened to the profit."""

    month: date
    label: str
    money_in: list[FlowPart]  # sales (cash and mobile), baki coming back
    costs: list[FlowPart]  # stock, then running costs by category
    profit_paisa: int
    uses: list[FlowPart]  # household, savings, kept in the shop (negative: the shop's cash went down)
    gross_margin_pct: float  # over the last three months: 1 - stock bought / goods sold (baki sales included)
    headline: str


class ShopProfile(BaseModel):
    stage: Stage
    stage_label: str
    stage_text: str
    steadiness: Literal["steady", "moderate", "choppy"] | None
    daily_swing_pct: float | None  # how far a typical day lands from the usual for that weekday
    trend: Trend
    monthly_trend_pct: float  # sales growth per month, from a straight line through six months
    profit_swing_pct: float | None  # how much monthly profit moves around its middle
    reinvest_pct: int  # the share of profit to put back into stock now (0: not yet)
    reinvest_suggested_pct: int = 0  # what the books say this month, even while it has to wait
    reinvest_choice_pct: int | None = None  # the owner's own choice; None: KhataPilot sizes it
    reinvest_why: str = ""  # how the suggestion was worked out, in words
    reinvest_wait: str = ""  # why it has to wait, if it does ("once the cushion holds")


class ScenarioMonth(BaseModel):
    month: date
    label: str
    profit_paisa: int
    low_paisa: int
    high_paisa: int
    season: str | None = None  # "Ramadan", "Eid", "Ramadan and Eid"


class Scenario(BaseModel):
    key: Literal["as_now", "reinvest", "collect_and_reinvest"]
    label: str
    assumption: str
    months: list[ScenarioMonth]
    total_profit_paisa: int
    savings_paisa: int  # what the savings plan would put aside on the way
    extra_vs_now_paisa: int


class Lever(BaseModel):
    kind: Literal["household", "collect_baki", "supplier_terms", "rising_cost", "margin", "reinvest", "cushion"]
    title: str
    text: str
    cash_paisa: int  # freed once
    monthly_paisa: int  # more profit (or less loss) a month
    tab: Literal["ledger", "forecast", "savings", "chat"] = "chat"


class GrowthPlan(BaseModel):
    as_of: datetime
    headline: str
    flow: MoneyFlow
    profile: ShopProfile
    base_profit_paisa: int
    scenarios: list[Scenario]
    levers: list[Lever]
    method: list[str]
