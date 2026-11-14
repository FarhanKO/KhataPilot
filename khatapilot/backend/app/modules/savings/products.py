"""Real places to keep the owner's savings in Bangladesh, and which fit this shop's pace. Read-only.

The figures are as published by the sources below on the date in `CHECKED`; rates change (Sanchayapatra rates
are set every six months, a DPS rate by each partner bank), so the app always shows the source and the date and
tells the owner to confirm before opening one. KhataPilot does not open, sell or pay into any of them: the
owner's monthly savings move only to the account they saved with their PIN (their own bKash number, say, from
which a bKash DPS takes its instalment).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.money import fmt_taka

CHECKED = "14 Nov 2026"


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    provider: str
    kind: str  # monthly | lump_sum
    what: str
    rate_text: str
    rate_pct: float | None  # a rate to illustrate with, when one is published for everyone; else None
    tenure_text: str
    min_monthly_paisa: int | None = None
    max_monthly_paisa: int | None = None
    step_paisa: int = 100_00
    how: str = ""
    notes: list[str] = field(default_factory=list)
    sources: list[tuple[str, str]] = field(default_factory=list)


PRODUCTS: tuple[Product, ...] = (
    Product(
        id="bkash-monthly-dps", name="Monthly DPS in the bKash app", provider="bKash with a partner bank or finance "
        "company (BRAC Bank, City Bank, Dhaka Bank, Eastern Bank, IDLC Finance, Mutual Trust Bank)", kind="monthly",
        what=("A deposit pension scheme: the same amount is taken from your bKash account every month, and at the "
              "end you get it back with profit."),
        rate_text=("Set by the partner you choose and shown in the bKash app before you open it (reported examples for "
                   "a one-year DPS: 9% to 10% a year)."),
        rate_pct=None, tenure_text="6, 12, 24, 36 or 48 months",
        min_monthly_paisa=500_00, max_monthly_paisa=20_000_00, step_paisa=500_00,
        how=("bKash app > Savings > Monthly DPS. Set your own bKash number as KhataPilot's savings account, so each "
             "month's savings land where the instalment is taken from."),
        notes=["Islamic (Shariah) DPS is offered by City Bank and Dhaka Bank.",
               "Government AIT and excise duty are taken from the profit.",
               "Keep the instalment small enough for a lean month: a missed instalment can cost profit."],
        sources=[("bKash: Monthly DPS", "https://www.bkash.com/en/products-services/savings/monthly-dps"),
                 ("The Financial Express, May 2026: over 6 million DPS accounts through bKash",
                  "https://today.thefinancialexpress.com.bd/print/bkash-app-reshapes-savings-culture-as-over-6-million-"
                  "dps-accounts-opened-in-four-years-1777819733"),
                 ("Prothom Alo: how to open a DPS from home", "https://en.prothomalo.com/business/local/zz8nd5fs8i")],
    ),
    Product(
        id="sanchayapatra-5yr", name="5-year Bangladesh Sanchayapatra", provider="Government of Bangladesh (National "
        "Savings Directorate), bought at banks and post offices", kind="lump_sum",
        what="A government savings certificate: a lump sum for five years at a fixed rate. The safest place for money "
             "a goal will not need for years.",
        rate_text="11.83% a year for the first slab (up to ৳7.5 lakh), 11.80% above it (July to December 2026)",
        rate_pct=11.83, tenure_text="5 years",
        how="With your NID at a bank or post office that sells savings certificates, once a goal's money has built up.",
        notes=["Tax is cut at source from the profit (reported: 5% when your total investment is up to ৳5 lakh, "
               "10% above).", "Cashing it before five years pays a lower rate."],
        sources=[("The Daily Star: national savings rates unchanged for July-December 2026",
                  "https://www.thedailystar.net/business/news/govt-keeps-national-savings-rates-unchanged-jul-dec-4214081"),
                 ("Prothom Alo: profit rates of savings certificates will not be reduced",
                  "https://en.prothomalo.com/business/local/ymdhmvp3q2")],
    ),
    Product(
        id="sanchayapatra-3month", name="3-month profit-based Sanchayapatra", provider="Government of Bangladesh "
        "(National Savings Directorate), bought at banks and post offices", kind="lump_sum",
        what="A government savings certificate that pays its profit every three months: income from savings without "
             "touching the amount saved.",
        rate_text="11.82% a year for the first slab (up to ৳7.5 lakh), 11.77% above it (July to December 2026)",
        rate_pct=11.82, tenure_text="3 years, profit every 3 months",
        how="With your NID at a bank or post office that sells savings certificates.",
        notes=["Tax is cut at source from the profit (reported: 5% up to ৳5 lakh in total, 10% above)."],
        sources=[("The Daily Star: national savings rates unchanged for July-December 2026",
                  "https://www.thedailystar.net/business/news/govt-keeps-national-savings-rates-unchanged-jul-dec-4214081")],
    ),
    Product(
        id="paribar-sanchayapatra", name="Paribar (Family) Sanchayapatra", provider="Government of Bangladesh "
        "(National Savings Directorate)", kind="lump_sum",
        what="A government savings certificate with monthly profit, for the family members it is open to (check "
             "eligibility where you buy it).",
        rate_text="11.93% a year for the first slab (up to ৳7.5 lakh), 11.80% above it (July to December 2026)",
        rate_pct=11.93, tenure_text="5 years, profit every month",
        how="With your NID at a bank or post office; ask whether you or a family member qualify.",
        sources=[("The Daily Star: national savings rates unchanged for July-December 2026",
                  "https://www.thedailystar.net/business/news/govt-keeps-national-savings-rates-unchanged-jul-dec-4214081")],
    ),
)


def instalment_for(product: Product, monthly_paisa: int) -> int:
    """A DPS instalment the shop can keep up in a lean month: the pace rounded down to the product's step,
    within its limits (0 when even the smallest instalment is too much)."""
    if product.min_monthly_paisa is None:
        return 0
    amount = monthly_paisa // product.step_paisa * product.step_paisa
    if amount < product.min_monthly_paisa:
        return 0
    return min(amount, product.max_monthly_paisa or amount)


def fits(lean_month_paisa: int, saved_total_paisa: int) -> list[dict]:
    """Each product with whether it fits this shop: a monthly DPS at what the leanest recent month could save,
    a certificate once there is a lump sum to put in."""
    out = []
    for p in PRODUCTS:
        if p.kind == "monthly":
            instalment = instalment_for(p, lean_month_paisa)
            fit = (f"Fits: {fmt_taka(instalment)} a month, what your leaner recent months could save."
                   if instalment else
                   f"Not yet: the smallest instalment is {fmt_taka(p.min_monthly_paisa)}, more than a lean month "
                   "saves now.")
        else:
            instalment = 0
            fit = (f"For a lump sum: the {fmt_taka(saved_total_paisa)} saved so far could go in once a goal does not "
                   "need it for years." if saved_total_paisa >= 1_000_00 else
                   "Later: for a lump sum, once savings have built up.")
        out.append({"id": p.id, "name": p.name, "provider": p.provider, "kind": p.kind, "what": p.what,
                    "rate": p.rate_text, "rate_pct": p.rate_pct, "tenure": p.tenure_text, "how": p.how,
                    "notes": p.notes, "fit": fit, "fits": bool(instalment) if p.kind == "monthly" else None,
                    "instalment_paisa": instalment or None,
                    "sources": [{"title": t, "url": u} for t, u in p.sources]})
    return out
