"""Background jobs: the payment scheduler loop, and once a simulated day a look ahead at cash (naming a
loan that would cover a shortfall) and at overdue baki (offering reminders). Payments go through the
guard's scheduler, which re-checks every due payment before paying it. The daily checks only tell the
owner: they prepare, send and pay nothing."""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from app.core.clock import local_date
from app.core.ids import new_id
from app.core.money import fmt_taka
from app.guard.executor import ExecutionResult
from app.guard.service import Guard
from app.modules.credit.schemas import ConsentRequired, NotEnoughHistory
from app.modules.credit.service import CreditModelMissing
from app.modules.reminders.schemas import NothingToRemind
from app.modules.reminders.service import DEFAULT_MIN_DAYS

log = logging.getLogger(__name__)


def run_due_payments(container) -> list[ExecutionResult]:
    with container.db.session() as session:
        return Guard(session, container.guard_deps).scheduler.run_due(request_id=new_id("sched"))


def check_cash(container) -> dict:
    """Run the cash forecast; if money could run short, alert the owner (once per day per shortfall)."""
    today = local_date(container.clock.now())
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        result = guard.forecasts.forecast(guard.forecast_days)
        shortfall = result.first_shortfall
        if shortfall is None:
            return {"alerted": False, "headline": result.headline}
        key = f"cash-{today:%Y%m%d}-{shortfall.day:%Y%m%d}"
        recent = container.audit.entries(event="alert", limit=50, newest_first=True)
        if any(e.request_id == key for e in recent):
            return {"alerted": False, "already_alerted": True, "headline": result.headline}
        fixes = [s.text for s in result.suggestions if s.closes_gap][:2]
        loan = matched_loan(guard)
        reason = result.headline + (" Options: " + " ".join(fixes) if fixes else "") + " " + loan["text"]
        guard.recorder.alert(request_id=key, channel="system", summary=f"Cash warning for {shortfall.day:%a %d %b}",
                             reason=reason.strip())
        return {"alerted": True, "headline": result.headline, "options": fixes, "loan": loan}


# How the owner can ask for each product in their own words (the planner and the model both understand these).
ASK_FOR = {"wallet-nano": "the nano-loan", "supplier-credit": "supplier credit",
           "mfi-working-capital": "the microfinance loan", "bank-sme": "the bank loan"}


def matched_loan(guard: Guard) -> dict:
    """The loan that would cover the shortfall, read-only: nothing is prepared or sent from here. Without
    the owner's consent no profile is built, and the alert only says where to turn it on."""
    if not guard.credit.consent().granted:
        return {"matched": False, "text": "To see whether a loan could cover it, turn on the credit profile in the "
                                          "Credit tab."}
    try:
        options = guard.loans.options(purpose="cover_shortfall")
    except (ConsentRequired, NotEnoughHistory, CreditModelMissing):
        return {"matched": False, "text": ""}
    best = next((m for m in options.matches if m.product.id == options.recommended), None)
    if best is None or not best.monthly_instalment_paisa:
        return {"matched": False, "text": "None of the sample loans fits the shop's income right now."}
    repay = (f"repaid in one go after {best.tenure_months} month" if best.product.flat_fee_rate
             else f"{fmt_taka(best.monthly_instalment_paisa)} a month for {best.tenure_months} months")
    covers = "would cover it" if best.covers_shortfall else "would cover part of it"
    ask = ASK_FOR.get(best.product.id, best.product.name)
    return {"matched": True, "product_id": best.product.id, "product": best.product.name,
            "amount": fmt_taka(best.amount_paisa), "repayment": repay,
            "text": (f"A {best.product.name} of {fmt_taka(best.amount_paisa)} {covers} ({repay}, illustrative). "
                     f"Say \"apply for {ask} to cover the shortfall\" and it will be prepared for your approval.")}


def check_baki(container) -> dict:
    """Once a day: if customers have owed for two weeks or more and were not reminded this week, tell the
    owner and offer reminders. Nothing is prepared or sent from here; the owner asks, then approves."""
    today = local_date(container.clock.now())
    key = f"baki-{today:%Y%m%d}"
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        try:
            plan = guard.reminders.plan(min_days=DEFAULT_MIN_DAYS)
        except NothingToRemind as exc:
            return {"offered": False, "headline": str(exc)}
        n = len(plan.drafts)
        names = ", ".join(d.customer_name for d in plan.drafts[:3]) + (f" and {n - 3} more" if n > 3 else "")
        headline = (f"{n} customer{'s' if n != 1 else ''} {'owe' if n != 1 else 'owes'} {fmt_taka(plan.total_paisa)} "
                    f"for {DEFAULT_MIN_DAYS} days or more: {names}.")
        recent = container.audit.entries(event="alert", limit=50, newest_first=True)
        if any(e.request_id == key for e in recent):
            return {"offered": False, "already_offered": True, "headline": headline}
        guard.recorder.alert(request_id=key, channel="system",
                             summary=f"Baki to collect: {n} customer{'s' if n != 1 else ''}, {fmt_taka(plan.total_paisa)}",
                             reason=(headline + ' Tap "Prepare reminders" in the Ledger tab or say "remind them"; '
                                     "nothing is sent until you approve."))
        return {"offered": True, "headline": headline, "customers": [d.customer_name for d in plan.drafts],
                "total": fmt_taka(plan.total_paisa)}


async def scheduler_loop(container, interval_seconds: int) -> None:
    checked: dict[str, date] = {}  # the last day each daily check ran
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(run_due_payments, container)
        except Exception:  # keep the loop alive; the failure is in the log and the next run retries
            log.exception("scheduled payment run failed")
        today = local_date(container.clock.now())
        for name, job in (("cash forecast", check_cash), ("baki", check_baki)):
            if checked.get(name) == today:
                continue
            try:
                await asyncio.to_thread(job, container)
                checked[name] = today
            except Exception:  # each check is independent and retried on the next run
                log.exception("%s check failed", name)
