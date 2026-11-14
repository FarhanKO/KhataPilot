"""Background jobs: the payment scheduler loop, and once a simulated day a look ahead at cash (naming a
loan that would cover a shortfall), at overdue baki (offering reminders) and at the owner's monthly
savings (saying when they can move). Payments go through the
guard's scheduler, which re-checks every due payment before paying it. The daily checks only tell the
owner: they prepare, send and pay nothing."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import socket
from datetime import date, datetime, timedelta

from app.agent.advisor import write_tips
from app.core.clock import local_date, to_local
from app.core.ids import new_id
from app.core.money import fmt_taka
from app.guard.advice import advice_facts, template_tips
from app.guard.executor import ExecutionResult
from app.guard.service import Guard
from app.infra import backup
from app.infra.job_state import acquire_lease, mark_once, release_lease
from app.interface.notifications import classify
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
        if not mark_once(session, key):
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
        if not mark_once(session, key):
            return {"offered": False, "already_offered": True, "headline": headline}
        guard.recorder.alert(request_id=key, channel="system",
                             summary=f"Baki to collect: {n} customer{'s' if n != 1 else ''}, {fmt_taka(plan.total_paisa)}",
                             reason=(headline + ' Tap "Prepare reminders" in the Ledger tab or say "remind them"; '
                                     "nothing is sent until you approve."))
        return {"offered": True, "headline": headline, "customers": [d.customer_name for d in plan.drafts],
                "total": fmt_taka(plan.total_paisa)}


def check_savings(container) -> dict:
    """Once a day: when this month's savings can move (in full or, in a lean month, in part), tell the owner
    once that month. Nothing is prepared or moved from here; the owner asks, then confirms with the PIN."""
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        status = guard.savings.status()
        if status.state not in ("ready", "partial"):
            return {"alerted": False, "state": status.state, "headline": status.headline}
        key = f"savings-{status.month:%Y%m}"
        if not mark_once(session, key):
            return {"alerted": False, "already_alerted": True, "state": status.state, "headline": status.headline}
        guard.recorder.alert(request_id=key, channel="system",
                             summary=f"Savings ready: {fmt_taka(status.suggested_paisa)} for {status.month:%B}",
                             reason=(status.headline + ' Tap "Move to savings" in the Savings tab or say "move my '
                                     'savings"; nothing moves until you confirm with your PIN.'))
        return {"alerted": True, "state": status.state, "headline": status.headline,
                "amount": fmt_taka(status.suggested_paisa)}


def check_provider(container) -> dict:
    """Once a day: match the books against the provider (balances, KhataPilot's own payments, payments stuck
    half-way). A mismatch is told to the owner once a day; nothing is booked or paid from here."""
    today = local_date(container.clock.now())
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        match = guard.payments.match_provider()
        if not match.reachable:
            return {"matched": False, "reachable": False, "problems": []}
        problems = match.problems
        if not problems:
            return {"matched": True, "reachable": True, "problems": []}
        digest = hashlib.sha256("|".join(problems).encode()).hexdigest()[:12]
        if not mark_once(session, f"provider-{today:%Y%m%d}-{digest}"):
            return {"matched": False, "reachable": True, "problems": problems, "already_alerted": True}
        guard.recorder.alert(request_id=f"provider-{today:%Y%m%d}", channel="system",
                             summary="Books and provider do not match",
                             reason=" ".join(problems) + " Forward the missing SMS or record the entry; KhataPilot "
                                                         "changed nothing.")
        return {"matched": False, "reachable": True, "problems": problems, "alerted": True}


def make_backup(container) -> dict:
    """Once a day, when BACKUP_DAILY is on: a checked backup of the books and the audit log."""
    settings = container.settings
    if not settings.backup_daily:
        return {"made": False, "why": "BACKUP_DAILY is off"}
    today = local_date(container.clock.now())
    with container.db.session() as session:
        if not mark_once(session, f"backup-{today:%Y%m%d}"):
            return {"made": False, "why": "already made today"}
    report = backup.create(app_url=settings.db_url, audit_url=settings.audit_db_url, root=settings.backup_dir,
                           keep=settings.backup_keep, note="daily")
    return {"made": True, "name": report.name}


def weekly_advice(container, *, force: bool = False) -> dict:
    """Once a week, on ADVICE_WEEKDAY from ADVICE_HOUR: the advisor's tips for the week, in Alerts. The facts come
    from the books (guard.advice); the model only words them, and only when a paid model is on and this month's AI
    budget is not spent. A tip with a number that is not in the facts is dropped."""
    settings = container.settings
    now = to_local(container.clock.now())
    if not force and (not settings.advice_weekly or now.weekday() != settings.advice_weekday
                      or now.hour < settings.advice_hour):
        return {"posted": False, "why": "not the advice day yet"}
    year, week, _ = now.isocalendar()
    with container.db.session() as session:
        if not mark_once(session, f"advice-{year}-W{week:02d}") and not force:
            return {"posted": False, "why": "already posted this week"}
        guard = Guard(session, container.guard_deps)
        facts = advice_facts(guard)
    tips, by = None, "fixed words"
    budget = getattr(container, "budget", None)
    if settings.llm_mode != "mock" and not (budget and budget.capped()):
        tips = write_tips(container.provider, facts)
        by = "the AI advisor" if tips else by
    tips = tips or template_tips(facts)
    if not tips:
        return {"posted": False, "why": "nothing to say this week"}
    with container.db.session() as session:
        Guard(session, container.guard_deps).recorder.alert(
            request_id=f"advice-{year}-W{week:02d}", channel="system", summary=f"This week's advice: {tips[0]['title']}",
            reason=" ".join(f"{i}. {t['title']}: {t['text']}" for i, t in enumerate(tips, 1))
            + f" (Written by {by} from your books; ask \"any advice?\" in chat for more.)")
    return {"posted": True, "by": by, "tips": tips}


ALERT_SMS_WINDOW = timedelta(hours=6)  # older alerts are not texted (the app shows them)


def send_alert_sms(container) -> dict:
    """Every run: text the owner the urgent and important alerts they have not been texted yet (each alert at
    most once, at most `OWNER_ALERT_SMS_PER_DAY` texts a day, several alerts folded into one SMS)."""
    settings = container.settings
    if not (settings.owner_alert_sms and settings.owner_phone):
        return {"sent": 0, "why": "alert SMS are off"}
    now = container.clock.now()
    fresh = []
    for entry in container.audit.entries(event="alert", newest_first=True, limit=30):
        try:
            at = datetime.fromisoformat(str(entry.ts))
        except ValueError:
            continue
        notice = classify(entry)
        if now - at <= ALERT_SMS_WINDOW and notice and notice.priority in ("urgent", "important"):
            fresh.append((entry, notice))
    if not fresh:
        return {"sent": 0}
    today = local_date(now)
    with container.db.session() as session:
        new = [(e, n) for e, n in reversed(fresh) if mark_once(session, f"alert-sms-{e.seq}")]
        if not new:
            return {"sent": 0}
        if not any(mark_once(session, f"alert-sms-day-{today:%Y%m%d}-{i}")
                   for i in range(max(0, settings.owner_alert_sms_per_day))):
            return {"sent": 0, "why": "today's alert SMS are used up", "alerts": len(new)}
        guard = Guard(session, container.guard_deps)
        shop = guard.shops.get(settings.shop_id)
        if len(new) == 1:
            text = f"KhataPilot: {new[0][1].title}. {new[0][1].body}"
        else:
            text = (f"KhataPilot, {len(new)} alerts: " + " | ".join(n.title for _, n in new)
                    + ". Open the app for details.")
        ok, detail = guard.owner_sms.send(owner_number=settings.owner_phone, from_account=shop.bkash_account,
                                          text=text, request_id=new_id("alertsms"))
    return {"sent": int(ok), "alerts": len(new), "detail": detail}


LEASE = "scheduler"
HOLDER = f"{socket.gethostname()}-{os.getpid()}"


def hold_lease(container, interval_seconds: int) -> bool:
    """Only one process runs the jobs: the one holding the lease (renewed every run, lapses if it dies)."""
    with container.db.session() as session:
        return acquire_lease(session, LEASE, HOLDER, timedelta(seconds=max(30, 3 * interval_seconds)))


def drop_lease(container) -> None:
    with container.db.session() as session:
        release_lease(session, LEASE, HOLDER)


async def scheduler_loop(container, interval_seconds: int) -> None:
    checked: dict[str, date] = {}  # the last day each daily check ran
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                leader = await asyncio.to_thread(hold_lease, container, interval_seconds)
            except Exception:
                log.exception("could not take the scheduler lease")
                continue
            if not leader:
                continue  # another process runs the jobs
            await run_jobs(container, checked)
    finally:
        await asyncio.to_thread(drop_lease, container)


async def run_jobs(container, checked: dict[str, date]) -> None:
    """One round: due payments, then each daily check once per simulated day (independent, retried on failure)."""
    metrics = getattr(container, "metrics", None)
    try:
        await asyncio.to_thread(run_due_payments, container)
        if metrics:
            metrics.job_finished("due payments", True)
    except Exception:  # keep the loop alive; the failure is in the log and the next run retries
        log.exception("scheduled payment run failed")
        if metrics:
            metrics.job_finished("due payments", False)
    try:
        await asyncio.to_thread(weekly_advice, container)
    except Exception:  # advice is a nicety: never let it stop payments or alerts
        log.exception("weekly advice failed")
    try:
        await asyncio.to_thread(send_alert_sms, container)
    except Exception:  # an alert SMS that cannot go out is still in the app
        log.exception("alert SMS failed")
    today = local_date(container.clock.now())
    for name, job in (("cash forecast", check_cash), ("baki", check_baki), ("savings", check_savings),
                      ("provider match", check_provider), ("backup", make_backup)):
        if checked.get(name) == today:
            continue
        try:
            await asyncio.to_thread(job, container)
            checked[name] = today
            if metrics:
                metrics.job_finished(name, True)
        except Exception:  # each check is independent and retried on the next run
            log.exception("%s check failed", name)
            if metrics:
                metrics.job_finished(name, False)
