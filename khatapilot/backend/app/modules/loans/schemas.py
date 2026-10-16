from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

Purpose = Literal["stock", "eid_stock", "working_capital", "cover_shortfall", "equipment", "shop_repair", "other"]
PURPOSE_WORDS: dict[str, str] = {
    "stock": "buying stock", "eid_stock": "extra stock before Eid", "working_capital": "working capital",
    "cover_shortfall": "covering a cash shortfall", "equipment": "equipment (fridge, shelves)",
    "shop_repair": "shop repairs", "other": "other business needs",
}


class LoanProduct(BaseModel):
    id: str
    name: str
    lender_type: str
    min_taka: int
    max_taka: int
    tenures: list[int]  # months
    annual_rate: float  # on the declining balance
    flat_fee_rate: float = 0.0  # a flat charge for the whole term (supplier credit)
    fee_rate: float  # one-off processing fee
    bands: list[str]
    min_months: float
    min_mobile_share: float | None = None
    min_supplier_on_time: float | None = None
    min_monthly_sales_taka: int | None = None
    speed: str
    collateral: str
    notes: str
    illustrative: bool = True


class Affordability(BaseModel):
    monthly_business_income_paisa: int  # sales minus stock and running costs, before household withdrawals
    max_instalment_paisa: int  # the share of that income a lender would let go to instalments
    shortfall_gap_paisa: int  # what the cash forecast says is missing to stay above the buffer (0 if none)
    shortfall_day: str | None


class LoanMatch(BaseModel):
    product: LoanProduct
    eligible: bool
    reasons_not: list[str]  # why not eligible, or why only partly
    amount_paisa: int  # what this product would lend towards the request
    covers_request: bool
    tenure_months: int | None
    monthly_instalment_paisa: int | None
    total_cost_paisa: int | None  # interest and fees over the term
    affordable: bool
    covers_shortfall: bool
    rank: int | None = None
    explanation: str


class LoanOptions(BaseModel):
    requested_paisa: int
    amount_basis: str  # how the amount was chosen when the owner did not say
    purpose: Purpose
    band: str
    probability_of_default: float
    affordability: Affordability
    matches: list[LoanMatch]
    recommended: str | None  # product id
    recommendation: str


class ApplicationDraft(BaseModel):
    """What would be sent to the lender. The guard binds its hash, so the owner approves exactly this."""

    product_id: str
    product_name: str
    amount_paisa: int
    tenure_months: int
    monthly_instalment_paisa: int
    total_cost_paisa: int
    purpose: Purpose
    applicant_account: str
    shop_name: str
    payload: dict[str, Any]
    payload_sha256: str
    summary: str


class LoanApplicationView(BaseModel):
    id: str
    product_id: str
    product_name: str
    amount_paisa: int
    tenure_months: int
    purpose: str
    status: Literal["submitted", "failed"]
    sandbox_ref: str | None
    failure: str | None
    submitted_at: datetime
    decision_id: str
    payload: dict[str, Any]


class NotEligible(Exception):
    """The request does not fit the product (eligibility, amount or tenure)."""
