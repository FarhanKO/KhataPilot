"""The advisor's facts: what the shop's books say this week, gathered by code and read-only.

The model may turn these into advice in its own words (in chat, or the weekly advice in Alerts), but every
number it may use is here, worked out by KhataPilot's code: the cash forecast, overdue baki, bills due, the
savings plan and the growth plan's top moves. `template_tips` writes the same advice without a model, for
offline mode, a spent AI budget, or a model answer that used a number not in the facts.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.core.clock import local_date
from app.core.money import fmt_taka

OVERDUE_DAYS = 14


def advice_facts(guard) -> dict[str, Any]:
    shop = guard.shop_id
    today = local_date(guard.clock.now())
    forecast = guard.forecasts.forecast(guard.forecast_days)
    balances = guard.ledger.balances(shop)
    debtors = guard.ledger.baki_by_customer(shop)
    overdue = sorted((d for d in debtors if (d.days_outstanding or 0) >= OVERDUE_DAYS),
                     key=lambda d: -d.outstanding_paisa)
    bills = [b for b in guard.ledger.open_obligations(shop) if b.due_date <= today + timedelta(days=7)]
    savings = guard.savings.status(forecast)
    try:
        moves = [{"what": lv.title, "why": lv.text} for lv in guard.growth.plan().levers[:3]]
    except ValueError:  # not enough history for a growth plan yet
        moves = []
    fixes = [s.text for s in forecast.suggestions if getattr(s, "closes_gap", False)][:1]
    return {
        "today": f"{today:%a %d %b %Y}",
        "cash_on_hand": fmt_taka(sum(balances.values())),
        "cash_forecast": {
            "headline": forecast.headline,
            "lowest": fmt_taka(forecast.lowest_balance_paisa),
            "lowest_day": f"{forecast.lowest_day:%a %d %b}",
            "safety_buffer": fmt_taka(forecast.buffer_paisa),
            "below_buffer": forecast.lowest_balance_paisa < forecast.buffer_paisa,
            "fix": fixes[0] if fixes else None,
        },
        "baki": {
            "total_owed": fmt_taka(sum(d.outstanding_paisa for d in debtors)),
            "overdue_customers": len(overdue),
            "overdue_owed": fmt_taka(sum(d.outstanding_paisa for d in overdue)),
            "biggest_overdue": [{"customer": d.name, "owes": fmt_taka(d.outstanding_paisa),
                                 "days": d.days_outstanding} for d in overdue[:3]],
        },
        "bills_next_7_days": {
            "count": len(bills),
            "total": fmt_taka(sum(b.amount_paisa for b in bills)),
            "first": ({"to": bills[0].counterparty_name, "amount": fmt_taka(bills[0].amount_paisa),
                       "due": f"{bills[0].due_date:%a %d %b}"} if bills else None),
        },
        "savings": savings.headline,
        "growth_moves": moves,
    }


def template_tips(facts: dict[str, Any], limit: int = 3) -> list[dict[str, str]]:
    """The advice in fixed words, most pressing first."""
    tips = []
    cash = facts["cash_forecast"]
    if cash["below_buffer"]:
        tips.append({"title": "Cash gets tight",
                     "text": (f"Cash is forecast to drop to {cash['lowest']} on {cash['lowest_day']}, below your "
                              f"{cash['safety_buffer']} safety buffer. " + (cash["fix"] or "Hold back any big buy "
                                                                           "until then."))})
    baki = facts["baki"]
    if baki["overdue_customers"]:
        top = baki["biggest_overdue"][0]
        tips.append({"title": "Collect old baki",
                     "text": (f"{baki['overdue_customers']} customers owe {baki['overdue_owed']} for {OVERDUE_DAYS} days "
                              f"or more; {top['customer']} owes {top['owes']}. Say \"remind them\": nothing is sent "
                              "until you approve.")})
    bills = facts["bills_next_7_days"]
    if bills["count"]:
        first = bills["first"]
        tips.append({"title": "Bills this week",
                     "text": (f"{bills['count']} bill{'s' if bills['count'] != 1 else ''} due in the next 7 days, "
                              f"{bills['total']} in all; first {first['to']}, {first['amount']} on {first['due']}.")})
    if facts["growth_moves"]:
        move = facts["growth_moves"][0]
        tips.append({"title": move["what"], "text": move["why"]})
    if facts["savings"]:
        tips.append({"title": "Your savings", "text": facts["savings"]})
    return tips[:limit]
