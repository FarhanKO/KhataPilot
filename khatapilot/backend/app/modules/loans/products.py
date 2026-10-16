"""Sample loan products. ILLUSTRATIVE ONLY: invented terms in the style of what Bangladeshi micro-shops
can get, not offers from any real lender. Rates are yearly, on the declining balance, unless the
product charges a flat fee for the term.
"""

from __future__ import annotations

from app.modules.loans.schemas import LoanProduct

PRODUCTS: list[LoanProduct] = [
    LoanProduct(
        id="wallet-nano", name="Mobile-wallet nano-loan", lender_type="Bank lending through a mobile wallet",
        min_taka=1_000, max_taka=30_000, tenures=[1, 2, 3], annual_rate=0.09, fee_rate=0.01,
        bands=["A", "B", "C"], min_months=6, min_mobile_share=0.25,
        speed="Minutes, in the wallet app", collateral="None",
        notes="Small amounts only; repaid automatically from the wallet each month.",
    ),
    LoanProduct(
        id="supplier-credit", name="Supplier credit extension", lender_type="One of your regular suppliers",
        min_taka=10_000, max_taka=100_000, tenures=[1], annual_rate=0.0, flat_fee_rate=0.02, fee_rate=0.0,
        bands=["A", "B", "C"], min_months=6, min_supplier_on_time=0.85,
        speed="Same week", collateral="None",
        notes="Extra stock now, paid back in one go after 30 days (2% on top).",
    ),
    LoanProduct(
        id="mfi-working-capital", name="Microfinance working-capital loan", lender_type="Microfinance institution",
        min_taka=20_000, max_taka=300_000, tenures=[6, 12], annual_rate=0.24, fee_rate=0.01,
        bands=["A", "B", "C", "D"], min_months=6,
        speed="About a week", collateral="None (group or personal guarantee)",
        notes="Monthly instalments; the most common option for shops without bank history.",
    ),
    LoanProduct(
        id="bank-sme", name="Bank SME loan", lender_type="Commercial bank (SME desk)",
        min_taka=100_000, max_taka=1_000_000, tenures=[12, 24, 36], annual_rate=0.12, fee_rate=0.01,
        bands=["A", "B"], min_months=12, min_monthly_sales_taka=300_000,
        speed="2 to 4 weeks", collateral="Trade licence and a guarantor",
        notes="Cheapest per taka for larger amounts, but needs a year of records.",
    ),
]
BY_ID = {p.id: p for p in PRODUCTS}
