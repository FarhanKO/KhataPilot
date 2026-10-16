"""The pre-filled loan application: built from the shop's profile, credit profile and forecast.

The payload is what the lender receives. It carries only what a loan officer needs; the phone
number is masked and no customer or supplier names are included.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.actions import canonical_json, sha256_hex
from app.core.masking import mask_phone
from app.core.money import fmt_taka
from app.modules.credit.schemas import ConsentView, CreditProfile
from app.modules.loans.schemas import PURPOSE_WORDS, Affordability, ApplicationDraft, LoanMatch, Purpose
from app.modules.shop.schemas import ShopProfile


def _taka(paisa: int) -> int:
    return round(paisa / 100)


def build(*, shop: ShopProfile, profile: CreditProfile, consent: ConsentView, match: LoanMatch, purpose: Purpose,
          afford: Affordability, forecast_headline: str | None, now: datetime) -> ApplicationDraft:
    features = {f.name: f.value for f in profile.features}
    product = match.product
    payload: dict[str, Any] = {
        "simulated": True,
        "applicant": {
            "shop_name": shop.name, "owner_name": shop.owner_name, "shop_type": shop.shop_type.replace("_", " "),
            "area": shop.area, "district": shop.district, "phone": mask_phone(shop.owner_phone),
            "wallet_account": shop.bkash_account,
        },
        "request": {
            "product_id": product.id, "product_name": product.name, "lender_type": product.lender_type,
            "amount_taka": _taka(match.amount_paisa), "tenure_months": match.tenure_months,
            "instalment_taka": _taka(match.monthly_instalment_paisa),
            "repaid_in_one_go": bool(product.flat_fee_rate), "total_cost_taka": _taka(match.total_cost_paisa),
            "purpose": PURPOSE_WORDS[purpose],
        },
        "credit_profile": {
            "profile_id": profile.id, "model": profile.model_version, "band": profile.band,
            "probability_of_default": profile.probability_of_default, "summary": profile.summary,
            "strengths": [r.text for r in profile.helps], "concerns": [r.text for r in profile.hurts],
        },
        "key_figures": {
            "average_monthly_sales_taka": round(features["avg_daily_sales"] * 30),
            "months_of_records": features["months_of_history"],
            "supplier_bills_on_time": round(features["supplier_on_time"], 3),
            "customer_credit_repaid": round(min(features["baki_collection_rate"], 1.0), 3),
            "cash_buffer_days": round(features["cash_buffer_days"], 1),
            "monthly_business_income_taka": _taka(afford.monthly_business_income_paisa),
        },
        "cash_forecast": forecast_headline,
        "consent": {"version": consent.version, "granted_at": consent.granted_at.isoformat() if consent.granted_at
                    else None},
        "prepared_at": now.isoformat(),
        "prepared_by": "KhataPilot, sent only after the owner approved it",
    }
    repay = (f"repaid in one go after {match.tenure_months} month" if product.flat_fee_rate
             else f"{fmt_taka(match.monthly_instalment_paisa)} a month for {match.tenure_months} months")
    return ApplicationDraft(
        product_id=product.id, product_name=product.name, amount_paisa=match.amount_paisa,
        tenure_months=match.tenure_months, monthly_instalment_paisa=match.monthly_instalment_paisa,
        total_cost_paisa=match.total_cost_paisa, purpose=purpose, applicant_account=shop.bkash_account,
        shop_name=shop.name, payload=payload, payload_sha256=sha256_hex(canonical_json(payload)),
        summary=(f"Apply for a {product.name}: {fmt_taka(match.amount_paisa)}, {repay} "
                 f"(total cost {fmt_taka(match.total_cost_paisa)}), sharing your credit profile (band {profile.band})"),
    )
