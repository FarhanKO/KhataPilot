from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

CONSENT_VERSION = "2026-10-v1"
CONSENT_TEXT = (
    "KhataPilot will read your shop's books (sales, baki, supplier payments, cash and mobile-money balances) "
    "to build a credit profile: a risk band, a probability and the main reasons behind it. The profile stays "
    "in KhataPilot. It is sent to a lender only as part of a loan application that you approve, one "
    "application at a time. You can withdraw this consent at any time; your stored profiles and application "
    "drafts are then deleted. Shop type and location are never used to score you."
)


class Reason(BaseModel):
    feature: str
    label: str
    value: float
    typical: float  # the median shop in the training data
    impact: float  # log-odds of default; negative lowers the risk
    direction: Literal["helps", "hurts"]
    text: str


class FeatureView(BaseModel):
    name: str
    label: str
    value: float
    typical: float
    unit: str
    scored: bool


class CreditProfile(BaseModel):
    id: str
    created_at: datetime
    model_version: str
    probability_of_default: float
    band: str
    band_words: str
    summary: str
    helps: list[Reason]
    hurts: list[Reason]
    features: list[FeatureView]
    history_days: int


class ConsentView(BaseModel):
    granted: bool
    version: str
    text: str
    granted_at: datetime | None = None


class ConsentRequired(Exception):
    """The owner has not agreed to a credit profile being built."""


class NotEnoughHistory(Exception):
    """Too few days of records for a meaningful profile."""
