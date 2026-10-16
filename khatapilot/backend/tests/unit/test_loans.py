"""Loan matching: repayment arithmetic, eligibility, ranking, and the application the guard binds."""

from datetime import datetime

import pytest

from app.core.actions import canonical_json, sha256_hex
from app.core.clock import BDT
from app.guard.capability import CapabilitySigner
from app.modules.credit.schemas import ConsentView, CreditProfile, FeatureView, Reason
from app.modules.loans import application, matcher
from app.modules.loans.products import BY_ID, PRODUCTS
from app.modules.loans.schemas import Affordability
from app.modules.loans.service import LOAN_KIND, LoansService, SubmissionRefused
from app.modules.shop.schemas import ShopProfile

NOW = datetime(2026, 10, 12, 10, 0, tzinfo=BDT)
FACTS = {"months_of_history": 11.3, "mobile_money_share": 0.49, "supplier_on_time": 0.94, "avg_daily_sales": 14_464.0,
         "gross_margin": 0.146, "baki_collection_rate": 0.96, "cash_buffer_days": 2.8}
AFFORD = Affordability(monthly_business_income_paisa=20_400_00, max_instalment_paisa=8_160_00,
                       shortfall_gap_paisa=12_706_00, shortfall_day="Tue 20 Oct")


def options(requested_taka: int, band: str = "B", facts: dict | None = None):
    return matcher.match(PRODUCTS, requested=requested_taka * 100, amount_basis="test", purpose="eid_stock",
                         facts=facts or FACTS, band=band, probability=0.06, afford=AFFORD)


# ---------------------------------------------------------------- arithmetic

def test_a_declining_balance_loan_costs_what_a_lender_would_quote():
    # ৳30,000 at 24% a year for 6 months: 2% a month, instalment 30,000 x 0.02 / (1 - 1.02^-6) = ৳5,355.78
    instalment, cost = matcher.repayment(BY_ID["mfi-working-capital"], 30_000_00, 6)
    assert instalment == 5_356_00  # rounded up to whole taka
    assert cost == 5_356_00 * 6 - 30_000_00 + 300_00  # interest plus the 1% fee


def test_supplier_credit_is_repaid_in_one_go():
    assert matcher.repayment(BY_ID["supplier-credit"], 50_000_00, 1) == (51_000_00, 1_000_00)


def test_the_largest_affordable_loan_fits_the_instalment_cap():
    mfi = BY_ID["mfi-working-capital"]
    most = matcher.largest_affordable(mfi, AFFORD.max_instalment_paisa)
    instalment, _ = matcher.repayment(mfi, most // 100 * 100, 12)
    assert instalment <= AFFORD.max_instalment_paisa + 100


# ---------------------------------------------------------------- eligibility and ranking

def test_eligibility_reasons_are_specific():
    bank = matcher.match_one(BY_ID["bank-sme"], 150_000_00, FACTS, "B", AFFORD)
    assert not bank.eligible
    assert bank.reasons_not == ["needs 12 months of records (you have 11)"]
    nano = matcher.match_one(BY_ID["wallet-nano"], 20_000_00, {**FACTS, "mobile_money_share": 0.1}, "D", AFFORD)
    assert nano.reasons_not[:2] == ["lends to bands A, B, C (yours is D)",
                                    "needs at least 25% of sales through bKash/Nagad (yours: 10%)"]


def test_the_cheapest_option_the_shop_can_carry_is_recommended():
    o = options(30_000)
    assert o.recommended == "mfi-working-capital"
    best = o.matches[0]
    assert (best.rank, best.tenure_months, best.monthly_instalment_paisa) == (1, 6, 5_356_00)
    assert "cheapest option you qualify for that your income can carry" in o.recommendation
    assert "covers the cash shortfall the forecast shows for Tue 20 Oct" in o.recommendation
    nano = next(m for m in o.matches if m.product.id == "wallet-nano")
    assert nano.eligible and not nano.affordable  # ৳10,151 a month over 3 months is more than ৳8,160


def test_a_small_request_goes_to_the_cheapest_small_loan():
    o = options(15_000)
    assert [m.product.id for m in o.matches if m.rank] == ["wallet-nano", "mfi-working-capital"]
    mfi = next(m for m in o.matches if m.product.id == "mfi-working-capital")
    assert mfi.amount_paisa == 20_000_00 and "lends at least ৳20,000" in mfi.explanation


def test_when_nothing_fits_it_says_why():
    o = options(90_000)
    assert o.recommended is None
    assert o.recommendation.startswith("None of the sample products fits a ৳90,000 request right now")
    assert "Bank SME loan: needs 12 months of records (you have 11)" in o.recommendation


def test_every_product_is_marked_illustrative():
    assert all(p.illustrative for p in PRODUCTS) and 3 <= len(PRODUCTS) <= 5


# ---------------------------------------------------------------- the application

def profile() -> CreditProfile:
    reason = Reason(feature="supplier_on_time", label="Supplier bills paid on time", value=0.94, typical=0.77,
                    impact=-0.3, direction="helps", text="You pay supplier bills on time 94% of the time.")
    return CreditProfile(
        id="cred_1", created_at=NOW, model_version="khata-credit-v1", probability_of_default=0.06, band="B",
        band_words="low risk", summary="Band B (low risk).", helps=[reason], hurts=[], history_days=345,
        features=[FeatureView(name=n, label=n, value=v, typical=v, unit="pct", scored=True) for n, v in FACTS.items()])


def shop() -> ShopProfile:
    return ShopProfile(id="rahman-store", name="Rahman Store", owner_name="Mizanur Rahman", shop_type="grocery",
                       area="Mohakhali", district="Dhaka", division="Dhaka", urbanicity="urban",
                       owner_phone="01711000111", bkash_account="bkash:01711000222", nagad_account="nagad:01811000222",
                       bank_account="bank:1701500012345", data_since=datetime(2025, 11, 1).date())


def draft():
    match = options(30_000).matches[0]
    return application.build(shop=shop(), profile=profile(), consent=ConsentView(granted=True, version="v1", text="",
                                                                                  granted_at=NOW),
                             match=match, purpose="eid_stock", afford=AFFORD, forecast_headline="Cash gets tight.",
                             now=NOW)


def test_the_application_is_prefilled_from_the_profile_and_masks_the_phone():
    d = draft()
    assert d.summary.startswith("Apply for a Microfinance working-capital loan: ৳30,000, ৳5,356 a month for 6 months")
    assert d.payload["applicant"]["phone"] == "017XXXX0111"
    assert d.payload["request"]["purpose"] == "extra stock before Eid"
    assert d.payload["credit_profile"]["band"] == "B" and d.payload["simulated"] is True
    assert d.payload_sha256 == sha256_hex(canonical_json(d.payload))
    assert "01711000111" not in canonical_json(d.payload).decode()


def test_submission_needs_a_token_that_binds_this_exact_application():
    d = draft()
    signer = CapabilitySigner(b"k" * 48)
    loans = LoansService(None, None, "rahman-store", credit=None, sandbox=None, buffer_paisa=0)
    bigger = d.model_copy(update={"amount_paisa": d.amount_paisa * 10})
    token_for_bigger = signer.mint(kind=LOAN_KIND, params=loans.auth_params(bigger), idempotency_key="k1", tier=1,
                                   decision_id="dec_1")
    with pytest.raises(SubmissionRefused, match="does not bind"):
        loans.submit(d, token_for_bigger, decision_id="dec_1")
    edited = d.model_copy(update={"payload": {**d.payload, "request": {**d.payload["request"], "amount_taka": 300_000}}})
    token = signer.mint(kind=LOAN_KIND, params=loans.auth_params(edited), idempotency_key="k2", tier=1,
                        decision_id="dec_1")
    with pytest.raises(SubmissionRefused, match="changed after it was prepared"):
        loans.submit(edited, token, decision_id="dec_1")
