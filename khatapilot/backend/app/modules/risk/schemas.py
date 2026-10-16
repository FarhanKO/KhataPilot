from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PaymentFeatures(BaseModel):
    """What the risk model knows about one proposed payment. Built from the shop's own books and
    payment history, never from anything the LLM said."""

    model_config = ConfigDict(frozen=True)

    amount_paisa: int
    payee_trusted: bool
    payee_history_count: int  # past payments to this number
    payee_median_paisa: int | None
    average_daily_sales_paisa: int
    local_hour: int
    payments_last_hour: int
    payments_last_day: int
    new_payees_last_day: int = 0  # distinct untrusted payees paid in the last 24 hours


class RiskScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: float = Field(ge=0.0, le=1.0)
    reasons: list[str]
    model: str
    layer1_percentile: float | None = None  # how unusual versus the shop's normal payments (Fraud Sentinel layer 1)
    layer2_probability: float | None = None  # calibrated fraud probability (Fraud Sentinel layer 2)
