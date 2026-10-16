"""Financing-option matching: who would lend, how much it costs, and whether the shop can carry it.

Pure functions. For each product: eligibility (band, history and the product's own rules), the
amount it can lend towards the request, the shortest term whose instalment the shop's income can
carry (shorter is cheaper), the total cost in taka, and whether it covers the shortfall the cash
forecast found. Products that are eligible, affordable and cover the request are ranked by total
cost; the cheapest is recommended.
"""

from __future__ import annotations

import math

from app.core.money import fmt_taka
from app.modules.loans.schemas import Affordability, LoanMatch, LoanProduct, LoanOptions, Purpose

DEBT_SERVICE_SHARE = 0.40  # at most this share of monthly business income may go to instalments


def repayment(product: LoanProduct, principal: int, months: int) -> tuple[int, int]:
    """(Monthly instalment, total cost of interest and fees), in paisa rounded to whole taka. A flat-fee
    product is repaid in one go."""
    fee = round(principal * product.fee_rate / 100) * 100
    if product.flat_fee_rate:
        charge = round(principal * product.flat_fee_rate * months / 100) * 100
        return principal + charge, charge + fee
    r = product.annual_rate / 12
    payment = principal / months if r == 0 else principal * r / (1 - (1 + r) ** -months)
    payment = math.ceil(payment / 100) * 100
    return payment, payment * months - principal + fee


def largest_affordable(product: LoanProduct, max_instalment: int) -> int:
    """The biggest loan from `product` whose repayment fits `max_instalment` (paisa, before rounding)."""
    if product.flat_fee_rate:
        best = max_instalment / (1 + product.flat_fee_rate * max(product.tenures))
    else:
        r, n = product.annual_rate / 12, max(product.tenures)
        best = max_instalment * n if r == 0 else max_instalment * (1 - (1 + r) ** -n) / r
    return min(int(best), product.max_taka * 100)


def ineligible_reasons(product: LoanProduct, facts: dict[str, float], band: str) -> list[str]:
    reasons = []
    if band not in product.bands:
        reasons.append(f"lends to bands {', '.join(product.bands)} (yours is {band})")
    if facts["months_of_history"] < product.min_months:
        reasons.append(f"needs {product.min_months:.0f} months of records (you have {facts['months_of_history']:.0f})")
    if product.min_mobile_share is not None and facts["mobile_money_share"] < product.min_mobile_share:
        reasons.append(f"needs at least {product.min_mobile_share:.0%} of sales through bKash/Nagad "
                       f"(yours: {facts['mobile_money_share']:.0%})")
    if product.min_supplier_on_time is not None and facts["supplier_on_time"] < product.min_supplier_on_time:
        reasons.append(f"needs supplier bills paid on time {product.min_supplier_on_time:.0%} of the time "
                       f"(yours: {facts['supplier_on_time']:.0%})")
    if product.min_monthly_sales_taka is not None and facts["avg_daily_sales"] * 30 < product.min_monthly_sales_taka:
        reasons.append(f"needs monthly sales of ৳{product.min_monthly_sales_taka:,} "
                       f"(yours: about ৳{facts['avg_daily_sales'] * 30:,.0f})")
    return reasons


def match_one(product: LoanProduct, requested: int, facts: dict[str, float], band: str,
              afford: Affordability) -> LoanMatch:
    reasons = ineligible_reasons(product, facts, band)
    amount = min(max(requested, product.min_taka * 100), product.max_taka * 100)
    covers = amount >= requested
    if amount < requested:
        reasons_partial = [f"lends at most {fmt_taka(product.max_taka * 100)}"]
    elif amount > requested:
        reasons_partial = [f"lends at least {fmt_taka(product.min_taka * 100)}"]
    else:
        reasons_partial = []
    options = [(months, *repayment(product, amount, months)) for months in sorted(product.tenures)]
    fitting = [o for o in options if o[1] <= afford.max_instalment_paisa]
    months, instalment, cost = fitting[0] if fitting else options[-1]
    affordable = bool(fitting)
    covers_shortfall = afford.shortfall_gap_paisa > 0 and amount >= afford.shortfall_gap_paisa
    eligible = not reasons
    if not eligible:
        explanation = f"Not yet: {product.name} " + "; ".join(reasons) + "."
    else:
        pay = (f"repaid in one go after {months} month{'s' if months > 1 else ''}: {fmt_taka(instalment)}"
               if product.flat_fee_rate else f"{fmt_taka(instalment)} a month for {months} months")
        explanation = f"{fmt_taka(amount)}, {pay}, total cost {fmt_taka(cost)}."
        if reasons_partial:
            explanation += f" Note: it {reasons_partial[0]}."
        if not affordable:
            explanation += (f" That is more than your income can safely carry "
                            f"(about {fmt_taka(afford.max_instalment_paisa)} a month).")
    return LoanMatch(product=product, eligible=eligible, reasons_not=reasons + reasons_partial, amount_paisa=amount,
                     covers_request=covers, tenure_months=months, monthly_instalment_paisa=instalment,
                     total_cost_paisa=cost, affordable=affordable, covers_shortfall=covers_shortfall,
                     explanation=explanation)


def match(products: list[LoanProduct], *, requested: int, amount_basis: str, purpose: Purpose,
          facts: dict[str, float], band: str, probability: float, afford: Affordability) -> LoanOptions:
    matches = [match_one(p, requested, facts, band, afford) for p in products]
    good = sorted((m for m in matches if m.eligible and m.affordable and m.covers_request),
                  key=lambda m: m.total_cost_paisa)
    partial = sorted((m for m in matches if m.eligible and m.affordable and not m.covers_request),
                     key=lambda m: -m.amount_paisa)
    for rank, m in enumerate(good + partial, start=1):
        m.rank = rank
    matches.sort(key=lambda m: (m.rank is None, m.rank or 0))
    best = (good + partial)[0] if good or partial else None
    if best is None:
        blockers = "; ".join(f"{m.product.name}: {m.reasons_not[0] if m.reasons_not else 'instalment too high'}"
                             for m in matches)
        recommendation = (f"None of the sample products fits a {fmt_taka(requested)} request right now ({blockers}). "
                          "A smaller amount, or a few more months of records, would open more options.")
    else:
        share = best.monthly_instalment_paisa / afford.monthly_business_income_paisa \
            if afford.monthly_business_income_paisa > 0 else 1.0
        why = ("the cheapest option you qualify for that your income can carry" if best in good
               else "no product covers the full amount; this one covers the most of it")
        recommendation = (f"Recommended: {best.product.name}. {best.explanation} It is {why}"
                          + (f": the instalment is {share:.0%} of your monthly business income "
                             f"(about {fmt_taka(afford.monthly_business_income_paisa)})."
                             if not best.product.flat_fee_rate else "."))
        if best.covers_shortfall and afford.shortfall_day:
            recommendation += f" It also covers the cash shortfall the forecast shows for {afford.shortfall_day}."
    return LoanOptions(requested_paisa=requested, amount_basis=amount_basis, purpose=purpose, band=band,
                       probability_of_default=probability, affordability=afford, matches=matches,
                       recommended=best.product.id if best else None, recommendation=recommendation)
