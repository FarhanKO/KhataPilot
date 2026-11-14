"""Public API of the reports module: one month's statement for the owner, with everything, and a
six-month statement for a bank or NGO, with what a lender asks for and no customer's name or number.

Every figure comes from the books through the other modules' public APIs; nothing is estimated here
except where a section says "now" (today's position: who owes, open bills, the cash forecast). Cash
profit is worked out exactly as the savings plan does it: sales and baki coming back, minus what was
paid for stock, to suppliers and for running costs; household money and savings are not costs.
The app adds what only the guard knows (blocked attempts, the provider match, the audit chain).
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core.clock import Clock, local_date, to_local
from app.core.ids import new_id
from app.core.masking import mask_number
from app.core.money import fmt_taka
from app.modules.credit.service import CreditService
from app.modules.forecast.service import ForecastService
from app.modules.growth.service import GrowthService
from app.modules.ledger.service import LedgerService
from app.modules.loans.service import LoansService
from app.modules.payments.service import PaymentsService
from app.modules.reports.schemas import (
    LenderStatement,
    Line,
    MonthFigures,
    NoSuchMonth,
    OwnerStatement,
    ReportMonth,
    ShopFacts,
    Week,
)
from app.modules.savings.service import SavingsService
from app.modules.shop.service import ShopService

SAVINGS_CATEGORY = "Personal savings"
LENDER_MONTHS = 6


def month_start(day: date) -> date:
    return day.replace(day=1)


def month_end(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def previous_month(month: date) -> date:
    return (month.replace(day=1) - timedelta(days=1)).replace(day=1)


def whole(paisa: float) -> int:
    """An average rounded to whole taka, so it never shows paisa."""
    return round(paisa / 100) * 100


def day_span(start: date, end: date, *, year: bool = False) -> str:
    """"1–30 Sep", "29 Sep–5 Oct" (with the year when asked)."""
    tail = f" {end:%Y}" if year else ""
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}–{end.day} {end:%b}{tail}"
    return f"{start.day} {start:%b}–{end.day} {end:%b}{tail}"


def change_words(now: int, before: int) -> str:
    """How `now` compares with the month before, ready to put before that month's name."""
    if before <= 0:
        return "against a loss in" if before < 0 else "against no profit in"
    pct = round((now / before - 1) * 100)
    return "about the same as" if abs(pct) < 2 else f"{'up' if pct > 0 else 'down'} {abs(pct)}% on"


def steadiness(values: list[int]) -> tuple[str, float | None]:
    """How much monthly profit moves around its middle, in words."""
    if len(values) < 3 or statistics.mean(values) <= 0:
        return "too few profitable months to say", None
    swing = statistics.pstdev(values) / statistics.mean(values)
    words = "steady" if swing < 0.25 else "moderately up and down" if swing < 0.5 else "very up and down"
    return words, round(swing * 100, 1)


def trend_pct(values: list[int]) -> float | None:
    """Sales growth a month: a straight line through the months, as a share of their average."""
    n = len(values)
    if n < 3 or statistics.mean(values) <= 0:
        return None
    xs = range(n)
    mx, my = (n - 1) / 2, statistics.mean(values)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, values, strict=True)) / sum((x - mx) ** 2 for x in xs)
    return round(slope / my * 100, 1)


class ReportsService:
    def __init__(self, session: Session, clock: Clock, shop_id: str, *, savings: SavingsService,
                 growth: GrowthService, credit: CreditService, loans: LoansService, payments: PaymentsService,
                 forecasts: ForecastService) -> None:
        self.session = session
        self.clock = clock
        self.shop_id = shop_id
        self.ledger = LedgerService(session, clock)
        self.shops = ShopService(session, clock)
        self.savings = savings
        self.growth = growth
        self.credit = credit
        self.loans = loans
        self.payments = payments
        self.forecasts = forecasts

    # ---------------------------------------------------------------- months

    def _today(self) -> date:
        return local_date(self.clock.now())

    def _shop(self):
        shop = self.shops.get(self.shop_id)
        if shop is None:
            raise NoSuchMonth("The shop is not loaded yet.")
        return shop

    def months(self) -> list[ReportMonth]:
        """Months with books, newest first; this month (so far) included once a day of it is in."""
        today, since = self._today(), self._shop().data_since
        this = month_start(today)
        out, month = [], this
        while month >= month_start(since):
            finished = month < this
            if finished or today > month:
                out.append(ReportMonth(month=f"{month:%Y-%m}", label=f"{month:%B %Y}", finished=finished))
            month = previous_month(month)
        return out

    def _span(self, month: date) -> tuple[date, date, bool]:
        today, since = self._today(), self._shop().data_since
        start = max(month_start(month), since)
        finished = month_end(month) < today
        end = month_end(month) if finished else today - timedelta(days=1)
        if end < start:
            raise NoSuchMonth(f"There are no books for {month:%B %Y}.")
        return start, end, finished

    def _period(self, start: date, end: date, finished: bool) -> str:
        text = day_span(start, end, year=True)
        return text if finished else f"{text} (the month so far)"

    # ---------------------------------------------------------------- one month's figures

    def figures(self, month: date) -> MonthFigures:
        start, end, _ = self._span(month)
        rows = self.ledger.category_totals(self.shop_id, start, end)
        v = {k: 0 for k in ("sales", "mobile", "baki_given", "baki_back", "other_in", "stock_cash", "stock_credit",
                            "supplier", "household", "savings")}
        costs: dict[str, int] = {}
        for kind, method, category, amount in rows:
            if kind == "sale" and method != "credit":
                v["sales"] += amount
                if method in ("bkash", "nagad", "bank"):
                    v["mobile"] += amount
            elif kind == "baki_sale":
                v["baki_given"] += amount
            elif kind == "baki_repayment":
                v["baki_back"] += amount
            elif kind == "other_in":
                v["other_in"] += amount
            elif kind == "purchase":
                v["stock_cash" if method != "credit" else "stock_credit"] += amount
            elif kind == "supplier_payment":
                v["supplier"] += amount
            elif kind in ("expense", "other_out"):
                costs[category or "Other"] = costs.get(category or "Other", 0) + amount
            elif kind == "owner_drawing":
                v["savings" if category == SAVINGS_CATEGORY else "household"] += amount
        running = sum(costs.values())
        profit = v["sales"] + v["baki_back"] - v["stock_cash"] - v["supplier"] - running
        sold = v["sales"] + v["baki_given"]
        bought = v["stock_cash"] + v["stock_credit"]
        books = self.ledger.book_days(self.shop_id, start, end)
        last = books[-1] if books else None
        return MonthFigures(
            month=month_start(month), label=f"{month:%b %Y}", sales_paisa=v["sales"], sales_mobile_paisa=v["mobile"],
            baki_given_paisa=v["baki_given"], baki_back_paisa=v["baki_back"], other_in_paisa=v["other_in"],
            stock_bought_paisa=bought, stock_paid_paisa=v["stock_cash"] + v["supplier"], running_costs_paisa=running,
            costs=[Line(label=k, paisa=a) for k, a in sorted(costs.items(), key=lambda kv: -kv[1])],
            household_paisa=v["household"], savings_paisa=v["savings"], cash_profit_paisa=profit,
            gross_margin_pct=round((1 - bought / sold) * 100, 1) if sold > 0 else None,
            closing_cash_paisa=last.closing_balance if last else 0,
            baki_outstanding_paisa=last.baki_outstanding if last else 0,
            payables_paisa=last.payables_outstanding if last else 0,
            supplier_payments_on_time=sum(b.supplier_payments_on_time for b in books),
            supplier_payments_late=sum(b.supplier_payments_late for b in books),
            days=(end - start).days + 1)

    def _weeks(self, month: date) -> list[Week]:
        start, end, _ = self._span(month)
        flows = self.ledger.daily_flows(self.shop_id, start, end)
        weeks, day = [], start
        while day <= end:
            stop = min(end, day + timedelta(days=6 - (day.day - 1) % 7))
            span = [flows[day + timedelta(days=i)] for i in range((stop - day).days + 1)]
            money_in = sum(f.sales_in + f.baki_collected + f.other_in for f in span)
            out = sum(f.routine_out + f.supplier_payments + f.bills_paid for f in span)
            profit = sum(f.sales_in + f.baki_collected - f.routine_out - f.supplier_payments - f.bills_paid for f in span)
            weeks.append(Week(label=day_span(day, stop), money_in_paisa=money_in,
                              money_out_paisa=out, profit_paisa=profit))
            day = stop + timedelta(days=1)
        return weeks

    def _facts(self) -> ShopFacts:
        shop = self._shop()
        accounts = [f"{name} {mask_number(number)}" for name, number in
                    (("bKash", shop.bkash_account), ("Nagad", shop.nagad_account), ("Bank", shop.bank_account)) if number]
        place = list(dict.fromkeys(p for p in (shop.area, shop.district, shop.division) if p))  # "Dhaka, Dhaka" once
        return ShopFacts(name=shop.name, owner_name=shop.owner_name, shop_type=shop.shop_type.replace("_", " "),
                         place=", ".join(place),
                         trading_since=shop.data_since, accounts=accounts)

    # ---------------------------------------------------------------- the owner's statement

    def owner(self, month: date) -> OwnerStatement:
        start, end, finished = self._span(month)
        f = self.figures(month)
        try:
            prev = self.figures(previous_month(month))
        except NoSuchMonth:
            prev = None
        today = self._today()
        headline = (f"{f.label}: {fmt_taka(f.sales_paisa)} sold and {fmt_taka(f.baki_back_paisa)} of baki paid back; "
                    f"cash profit {fmt_taka(f.cash_profit_paisa)}"
                    + (f", {change_words(f.cash_profit_paisa, prev.cash_profit_paisa)} {prev.label}" if prev else "")
                    + (f". You moved {fmt_taka(f.savings_paisa)} to savings." if f.savings_paisa else "."))
        money_in = [Line(label="Sales in cash", paisa=f.sales_paisa - f.sales_mobile_paisa),
                    Line(label="Sales by mobile money and bank", paisa=f.sales_mobile_paisa),
                    Line(label="Baki paid back", paisa=f.baki_back_paisa)]
        if f.other_in_paisa:
            money_in.append(Line(label="Other money in", paisa=f.other_in_paisa,
                                 note="not counted in profit (loans, transfers, top-ups)"))
        money_out = [Line(label="Stock and suppliers paid", paisa=f.stock_paid_paisa,
                          note=f"{fmt_taka(f.stock_bought_paisa)} of stock came in, paid or on credit")]
        money_out += f.costs
        own = [Line(label="Household money taken out", paisa=f.household_paisa),
               Line(label="Moved to your savings", paisa=f.savings_paisa)]

        debtors = self.ledger.baki_by_customer(self.shop_id)
        baki = {"given_paisa": f.baki_given_paisa, "back_paisa": f.baki_back_paisa,
                "outstanding_end_paisa": f.baki_outstanding_paisa, "customers_owing_now": len(debtors),
                "owed_now_paisa": sum(d.outstanding_paisa for d in debtors),
                "over_60_days_now_paisa": sum(d.outstanding_paisa for d in debtors if (d.days_outstanding or 0) > 60),
                "top_now": [{"name": d.name, "owed_paisa": d.outstanding_paisa, "days": d.days_outstanding}
                            for d in sorted(debtors, key=lambda d: -d.outstanding_paisa)[:6]]}
        open_bills = self.ledger.open_obligations(self.shop_id)
        bills = {"payables_end_paisa": f.payables_paisa, "open_now_paisa": sum(o.amount_paisa for o in open_bills),
                 "open_now": [{"payee": o.counterparty_name, "amount_paisa": o.amount_paisa,
                               "due": o.due_date.isoformat(), "overdue": o.due_date < today, "priority": o.priority}
                              for o in sorted(open_bills, key=lambda o: o.due_date)[:8]]}
        made = [p for p in self.payments.list(limit=500) if start <= to_local(p.created_at).date() <= end]
        done = [p for p in made if p.status == "completed"]
        payments = {"count": len(done), "total_paisa": sum(p.amount_paisa for p in done),
                    "not_completed": len(made) - len(done),
                    "items": [{"day": to_local(p.created_at).date().isoformat(), "to": p.payee_name or "a payee",
                               "amount_paisa": p.amount_paisa, "status": p.status, "purpose": p.purpose}
                              for p in sorted(made, key=lambda p: p.created_at)[:12]]}
        status = self.savings.status()
        row = next((m for m in status.history if m.month == month_start(month)), None)
        savings = {"share_pct": status.share_pct, "target_paisa": row.target_paisa if row else None,
                   "saved_paisa": row.saved_paisa if row else f.savings_paisa, "saved_total_paisa": status.saved_total_paisa,
                   "goals": [{"name": g.name, "target_paisa": g.target_paisa, "saved_paisa": g.saved_paisa,
                              "text": g.text} for g in status.goals]}
        try:
            plan = self.growth.plan()
            moves = [lv for lv in plan.levers if lv.text != plan.profile.stage_text][:3]  # no line said twice
            growth = {"stage": plan.profile.stage_label, "text": plan.profile.stage_text,
                      "moves": [{"title": lv.title, "text": lv.text} for lv in moves]}
        except ValueError:
            growth = None
        return OwnerStatement(
            id=new_id("rpt"), generated_at=self.clock.now(), month=f"{month:%Y-%m}",
            title=f"Monthly statement: {month:%B %Y}", period=self._period(start, end, finished), shop=self._facts(),
            headline=headline, figures=f, previous=prev, money_in=money_in, money_out=money_out, own_money=own,
            weeks=self._weeks(month), baki=baki, bills=bills, payments=payments, savings=savings,
            forecast=self.forecasts.forecast().headline, growth=growth,
            notes=["Cash profit is money from sales (cash and mobile money) and baki paid back, minus what was paid "
                   "for stock, to suppliers and for running costs. Household money and savings are yours, not costs.",
                   "Sections marked \"now\" show today's position, not the month's end.",
                   "Every figure comes from the shop's own books in KhataPilot."])

    # ---------------------------------------------------------------- the lender's statement

    def lender(self, month: date) -> LenderStatement:
        _, _, finished = self._span(month)
        last = month_start(month) if finished else previous_month(month_start(month))
        window, m = [], last
        for _ in range(LENDER_MONTHS):
            try:
                window.insert(0, self.figures(m))
            except NoSuchMonth:
                break
            m = previous_month(m)
        if not window:
            raise NoSuchMonth("A lender statement needs at least one finished month of books.")
        first = window[0].month
        profits = [w.cash_profit_paisa for w in window]
        sales = [w.sales_paisa + w.baki_given_paisa for w in window]
        steady, swing = steadiness(profits)
        best = max(window, key=lambda w: w.cash_profit_paisa)
        worst = min(window, key=lambda w: w.cash_profit_paisa)
        margins = [w.gross_margin_pct for w in window if w.gross_margin_pct is not None]
        averages = {"months": len(window), "sales_paisa": whole(statistics.mean(sales)),
                    "cash_profit_paisa": whole(statistics.mean(profits)),
                    "gross_margin_pct": round(statistics.mean(margins), 1) if margins else None,
                    "best": {"month": best.label, "profit_paisa": best.cash_profit_paisa},
                    "worst": {"month": worst.label, "profit_paisa": worst.cash_profit_paisa},
                    "steadiness": steady, "profit_swing_pct": swing, "sales_trend_pct_a_month": trend_pct(sales)}

        given, back = sum(w.baki_given_paisa for w in window), sum(w.baki_back_paisa for w in window)
        debtors = self.ledger.baki_by_customer(self.shop_id)
        customer_credit = {"outstanding_end_paisa": window[-1].baki_outstanding_paisa, "given_paisa": given,
                           "paid_back_paisa": back, "paid_back_pct": round(back / given * 100) if given else None,
                           "customers_owing_now": len(debtors),
                           "over_60_days_now_paisa": sum(d.outstanding_paisa for d in debtors
                                                         if (d.days_outstanding or 0) > 60)}
        on_time = sum(w.supplier_payments_on_time for w in window)
        late = sum(w.supplier_payments_late for w in window)
        history = self.ledger.book_days(self.shop_id, self._shop().data_since, month_end(last))
        informal = sum(b.informal_borrowed - b.informal_repaid for b in history)
        applications = [{"lender": a.product_name, "amount_paisa": a.amount_paisa, "status": a.status,
                         "day": to_local(a.submitted_at).date().isoformat()} for a in self.loans.applications()]
        obligations = {"supplier_payables_end_paisa": window[-1].payables_paisa,
                       "supplier_payments_on_time": on_time, "supplier_payments_late": late,
                       "on_time_pct": round(on_time / (on_time + late) * 100) if on_time + late else None,
                       "informal_loans_outstanding_paisa": max(0, informal), "applications_through_khatapilot": applications}
        afford = self.loans.affordability()
        household = whole(statistics.mean(w.household_paisa for w in window))
        income, cap = afford.monthly_business_income_paisa, afford.max_instalment_paisa
        left = max(0, income - household)
        text = (f"Over the last 90 days the business brought in about {fmt_taka(income)} a month after stock and "
                f"running costs, and the household took about {fmt_taka(household)} a month, leaving about "
                f"{fmt_taka(left)}. ")
        text += (f"KhataPilot's rule of thumb puts an affordable instalment at up to {fmt_taka(cap)} a month."
                 if left >= cap else
                 f"KhataPilot's rule of thumb (a share of business income) allows up to {fmt_taka(cap)} a month, but "
                 "more than what is left after household money: an instalment would have to come out of household "
                 "spending or from higher profit.")
        capacity = {"monthly_business_income_paisa": income, "household_paisa": household,
                    "left_after_household_paisa": left, "max_instalment_paisa": cap, "fits": left >= cap,
                    "text": text}
        if not self.credit.consent().granted:
            credit_profile = {"included": False, "why": "The owner has not turned on the credit profile, so none is "
                                                        "included."}
        elif (profile := self.credit.latest()) is None:
            credit_profile = {"included": False, "why": "No credit profile has been built yet."}
        else:
            credit_profile = {"included": True, "band": profile.band, "band_words": profile.band_words,
                              "summary": profile.summary, "built": to_local(profile.created_at).date().isoformat(),
                              "model": profile.model_version, "history_days": profile.history_days,
                              "helps": [r.label for r in profile.helps[:3]], "hurts": [r.label for r in profile.hurts[:3]]}
        status = self.savings.status()
        in_window = [h for h in status.history if first <= h.month <= last]
        savings_record = {"share_pct": status.share_pct, "months_saved": sum(1 for h in in_window if h.saved_paisa > 0),
                          "months": len(in_window), "saved_paisa": sum(h.saved_paisa for h in in_window),
                          "saved_total_paisa": status.saved_total_paisa}
        facts = self._facts()
        span = f"{first:%b %Y}–{last:%b %Y}" if first != last else f"{last:%B %Y}"
        headline = (f"{facts.name}, {facts.shop_type} in {facts.place}: over {span} it sold about "
                    f"{fmt_taka(averages['sales_paisa'])} a month and made about "
                    f"{fmt_taka(averages['cash_profit_paisa'])} a month in cash profit ({steady}).")
        return LenderStatement(
            id=new_id("rpt"), generated_at=self.clock.now(), month=f"{last:%Y-%m}",
            title=f"Business statement for a loan: {span}", period=span, shop=facts, headline=headline,
            months=window, averages=averages, customer_credit=customer_credit, obligations=obligations,
            capacity=capacity, credit_profile=credit_profile, savings_record=savings_record,
            notes=["These are the shop's own records kept in KhataPilot, not audited accounts.",
                   "Cash profit is money from sales and baki paid back, minus what was paid for stock, to suppliers "
                   "and for running costs, before household money.",
                   "No customer's name or number is included."],
            declaration=(f"I, {facts.owner_name}, confirm that these figures are from my shop's records and that I "
                         "have not left out any loan or debt I know of."))
