"""Public API of the savings module: the owner's plan, each month's profit, and what to save now.

Nothing here moves money. A transfer is a payment the guard proposes from `status()` and the owner
confirms with their PIN; once paid it is booked as an owner withdrawal in the "Personal savings"
category, so it is not a shop cost and the next month's profit is not lowered by it.

Goals are what the savings are for. Everything saved so far fills them in order; each shows how long it
will take at this month's pace. When a plan starts, three starter goals are worked out from the books (a
month's cushion, the next Eid's stock, a bulk stock order); the owner keeps, changes or removes them.
"""

from __future__ import annotations

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock, local_date
from app.core.ids import new_id
from app.core.masking import mask_number
from app.core.money import fmt_taka
from app.modules.forecast.schemas import CashForecast
from app.modules.forecast.service import ForecastService, next_stock_up
from app.modules.ledger.service import LedgerService
from app.modules.payments.service import PaymentsService
from app.modules.savings import plan as P
from app.modules.savings.products import CHECKED as PRODUCTS_CHECKED
from app.modules.savings.products import fits as product_fits
from app.modules.savings.models import SavingsGoal, SavingsPlan
from app.modules.savings.schemas import (
    MAX_GOALS,
    MAX_SHARE_PCT,
    MIN_SHARE_PCT,
    GoalDraft,
    GoalNotFound,
    GoalView,
    SavingsAccount,
    SavingsMonth,
    SavingsStatus,
)
from app.modules.shop.service import ShopService

HISTORY_MONTHS = 6


class SavingsService:
    def __init__(self, session: Session, clock: Clock, shop_id: str, *, standard_buffer_paisa: int) -> None:
        self.session = session
        self.clock = clock
        self.shop_id = shop_id
        self.ledger = LedgerService(session, clock)
        self.payments = PaymentsService(session, clock, shop_id)
        self.shops = ShopService(session, clock)
        self.forecasts = ForecastService(session, clock, shop_id, standard_buffer_paisa=standard_buffer_paisa)
        self.starters_added: list[GoalView] = []  # set by set_share when a first plan adds the starter goals

    # ---------------------------------------------------------------- the plan

    def _row(self) -> SavingsPlan | None:
        return self.session.get(SavingsPlan, self.shop_id)

    def _row_or_new(self) -> SavingsPlan:
        row = self._row()
        if row is None:
            row = SavingsPlan(shop_id=self.shop_id)
            self.session.add(row)
        return row

    def share(self) -> int | None:
        row = self._row()
        return row.share_pct if row else None

    def account(self) -> SavingsAccount | None:
        row = self._row()
        if row is None or not row.account_number:
            return None
        return SavingsAccount(name=row.account_name or "My savings", number=row.account_number,
                              number_masked=mask_number(row.account_number), method=row.account_method or "bkash",
                              set_at=row.account_set_at)

    def set_share(self, share_pct: int | None) -> int | None:
        """The owner's share of each month's profit; None pauses the plan (the account is kept). The first
        time a plan starts, the starter goals are added (`starters_added` says which)."""
        if share_pct is not None and not MIN_SHARE_PCT <= share_pct <= MAX_SHARE_PCT:
            raise ValueError(f"the share must be between {MIN_SHARE_PCT}% and {MAX_SHARE_PCT}%")
        row = self._row_or_new()
        row.share_pct, row.share_set_at = share_pct, self.clock.now()
        self.session.flush()
        self.starters_added = self.add_starters() if share_pct and not self._goal_rows(every=True) else []
        return row.share_pct

    def basis(self) -> str:
        row = self._row()
        return row.basis if row and row.basis else "last_month"

    def set_basis(self, basis: str) -> str:
        if basis not in ("last_month", "three_months"):
            raise ValueError("basis is last_month or three_months")
        row = self._row_or_new()
        row.basis = basis
        self.session.flush()
        return basis

    REINVEST_RANGE = (5, 50)

    def reinvest_share(self) -> int | None:
        """The share of profit the owner chose to put back into stock; None lets the books decide."""
        row = self._row()
        return row.reinvest_pct if row else None

    def set_reinvest_share(self, pct: int | None) -> int | None:
        low, high = self.REINVEST_RANGE
        if pct is not None and not low <= pct <= high:
            raise ValueError(f"the share put back into stock must be between {low}% and {high}%")
        row = self._row_or_new()
        row.reinvest_pct, row.reinvest_set_at = pct, self.clock.now()
        self.session.flush()
        return pct

    def set_account(self, *, name: str, number: str, method: str) -> SavingsAccount:
        """Called only once the owner's PIN is checked (the Savings tab's route, or a chat proposal the owner
        confirmed with the PIN): it decides where money may go."""
        row = self._row_or_new()
        row.account_name, row.account_number, row.account_method = name.strip()[:64], number, method
        row.account_set_at = self.clock.now()
        self.session.flush()
        return self.account()

    # ---------------------------------------------------------------- goals

    def _goal_rows(self, *, every: bool = False) -> list[SavingsGoal]:
        query = select(SavingsGoal).where(SavingsGoal.shop_id == self.shop_id)
        if not every:
            query = query.where(SavingsGoal.status == "active")
        return list(self.session.scalars(query.order_by(SavingsGoal.position, SavingsGoal.created_at)))

    def starter_drafts(self) -> list[GoalDraft]:
        """The starter goals the books suggest now (the last six finished months)."""
        today = local_date(self.clock.now())
        shop = self.shops.get(self.shop_id)
        month, months = P.previous_month(P.month_start(today)), []
        for _ in range(6):
            start = max(month, shop.data_since) if shop else month
            if start <= P.month_end(month):
                rows = self.ledger.category_totals(self.shop_id, start, P.month_end(month))
                if rows:
                    months.insert(0, P.month_totals(rows))
            month = P.previous_month(month)
        return P.starter_goals(months, stock_up=next_stock_up(today), pace=self._pace(today),
                               this_month=P.month_start(today))

    def add_starters(self) -> list[GoalView]:
        """Add the starter goals not there yet (by kind). Returns the ones added."""
        have = {g.kind for g in self._goal_rows()}
        added = [self.add_goal(d.name, d.target_paisa, by_month=d.by_month, kind=d.kind, set_by="khatapilot",
                               why=d.why)
                 for d in self.starter_drafts() if d.kind not in have]
        return [g for g in self.goals() if g.id in {a.id for a in added}]

    def add_goal(self, name: str, target_paisa: int, *, by_month: date | None = None, kind: str = "custom",
                 set_by: str = "owner", why: str | None = None) -> SavingsGoal:
        """A new goal, or a new amount and date for the active goal of the same name."""
        name = re.sub(r"\s+", " ", re.sub(r"[^\w .,'&()/-]", "", name)).strip()[:48]
        if len(name) < 2:
            raise ValueError("give the goal a name, for example \"fridge\" or \"Eid stock\"")
        if target_paisa < 100_00:
            raise ValueError("a goal needs an amount of at least ৳100")
        rows = self._goal_rows()
        same = next((g for g in rows if g.name.lower() == name.lower()), None)
        if same is not None:
            same.target_paisa, same.by_month = target_paisa, P.month_start(by_month) if by_month else same.by_month
            if set_by == "owner":
                same.set_by, same.why = "owner", why or same.why
            self.session.flush()
            return same
        if len(rows) >= MAX_GOALS:
            raise ValueError(f"at most {MAX_GOALS} goals at a time: remove one first")
        row = SavingsGoal(id=new_id("goal"), shop_id=self.shop_id, name=name, target_paisa=target_paisa,
                          by_month=P.month_start(by_month) if by_month else None, kind=kind, set_by=set_by, why=why,
                          position=max((g.position for g in rows), default=-1) + 1, status="active",
                          created_at=self.clock.now())
        self.session.add(row)
        self.session.flush()
        return row

    def find_goal(self, name: str) -> SavingsGoal:
        """The active goal the owner means: the same name, else the one goal sharing a word of 3+ letters
        with what they said ("remove the Eid goal" finds "Eid-ul-Fitr stock")."""
        rows = self._goal_rows()
        wanted = name.strip().lower()
        exact = [g for g in rows if g.name.lower() == wanted]
        if exact:
            return exact[0]
        filler = {"goal", "goals", "the", "my", "for", "savings", "saving", "remove", "delete"}
        words = {w for w in re.findall(r"[a-z]{3,}", wanted) if w not in filler}
        hits = [g for g in rows if words & set(re.findall(r"[a-z]{3,}", g.name.lower()))]
        if len(hits) == 1:
            return hits[0]
        names = ", ".join(f"\"{g.name}\"" for g in rows) or "none yet"
        if not hits:
            raise GoalNotFound(f"No savings goal matches \"{name}\". Your goals: {names}.")
        raise GoalNotFound(f"More than one goal matches \"{name}\" ({', '.join(g.name for g in hits)}). Which one?")

    def remove_goal(self, name: str) -> GoalView:
        row = self.find_goal(name)
        view = next(g for g in self.goals() if g.id == row.id)
        row.status, row.removed_at = "removed", self.clock.now()
        self.session.flush()
        return view

    def goals(self, *, pace: int | None = None) -> list[GoalView]:
        rows = self._goal_rows()
        if not rows:
            return []
        today = local_date(self.clock.now())
        saved_total, _ = self.payments.savings_between(date(2000, 1, 1), today)
        if pace is None:
            pace = self._pace(today)
        filled = P.fill_goals([(g.name, g.target_paisa, g.by_month) for g in rows], saved_total=saved_total,
                              pace=pace, this_month=P.month_start(today))
        return [GoalView(id=g.id, name=g.name, kind=g.kind, set_by=g.set_by, target_paisa=g.target_paisa,
                         saved_paisa=f["saved"], remaining_paisa=f["remaining"], pct=f["pct"], by_month=g.by_month,
                         months_left=f["months_left"], on_track=f["on_track"], reached=f["reached"], why=g.why,
                         text=f["text"], created_at=g.created_at)
                for g, f in zip(rows, filled, strict=True)]

    def _pace(self, today: date) -> int:
        """What is being saved a month: this month's target, the basis the plan uses."""
        share = self.share()
        if not share:
            return 0
        months = [P.previous_month(P.month_start(today))]
        for _ in range(2):
            months.insert(0, P.previous_month(months[0]))
        base = P.basis_profit(self.basis(), [self.month_profit(m, today) for m in months])
        return P.target_for(share, base) if base > 0 else 0

    def products(self, status: SavingsStatus | None = None) -> dict:
        """Real savings products and which fit this shop: a monthly DPS at what a lean recent month saves,
        government certificates for a lump sum (savings/products.py; figures dated and sourced)."""
        status = status or self.status()
        recent = [m.target_paisa for m in status.history if not m.so_far and m.target_paisa > 0][-3:]
        lean = min(recent) if recent else status.target_paisa
        return {"checked": PRODUCTS_CHECKED, "lean_month_paisa": lean, "saved_total_paisa": status.saved_total_paisa,
                "products": product_fits(lean, status.saved_total_paisa),
                "note": ("Rates as published on " + PRODUCTS_CHECKED + "; they change, so check with the provider "
                         "before you open one. KhataPilot does not open or pay into any of them: your savings move "
                         "only to the account you saved with your PIN.")}

    @staticmethod
    def goals_headline(goals: list[GoalView], saved_total: int) -> str:
        if not goals:
            return ("No goals yet. Say what you are saving for (\"goal 35,000 for a fridge by March\") or ask "
                    "KhataPilot to suggest some.")
        open_goals = [g for g in goals if not g.reached]
        if not open_goals:
            return f"Every goal is reached: {fmt_taka(saved_total)} saved."
        first = open_goals[0]
        return f"{fmt_taka(saved_total)} saved so far. Next: {first.text}"

    # ---------------------------------------------------------------- profit

    def month_profit(self, month: date, today: date) -> int:
        """Cash profit of a calendar month, up to yesterday for the month still running."""
        start, end = P.month_start(month), min(P.month_end(month), today)
        shop = self.shops.get(self.shop_id)
        if shop is not None:
            start = max(start, shop.data_since)
        if end < start:
            return 0
        return P.cash_profit(self.ledger.daily_flows(self.shop_id, start, end))

    # ---------------------------------------------------------------- what to save now

    def status(self, forecast: CashForecast | None = None) -> SavingsStatus:
        now = self.clock.now()
        today = local_date(now)
        this_month = P.month_start(today)
        last_month = P.previous_month(this_month)
        share, account = self.share(), self.account()
        forecast = forecast or self.forecasts.forecast()

        # Seven finished months: six to show, plus the one before the first (it sets that month's target).
        months = [this_month]
        for _ in range(HISTORY_MONTHS + 1):
            months.insert(0, P.previous_month(months[0]))
        profits = {m: self.month_profit(m, today) for m in months}
        basis = self.basis()
        before_now = [profits[m] for m in months[:-1]]
        last_profit = P.basis_profit(basis, before_now)
        basis_text, nothing_text = P.basis_words(basis, last_month, last_profit)
        jump = P.steady_base(before_now)[1] if basis == "last_month" else None
        if jump and jump.direction == "up":
            basis_text = P.steady_words(last_month, jump.base_paisa)
        saved, pending = self.payments.savings_between(this_month, P.month_end(this_month))
        safe_now = max(0, forecast.lowest_balance_paisa - forecast.buffer_paisa)

        state, target, remaining, suggested = P.decide(
            share_pct=share, has_account=account is not None, last_profit=last_profit, saved=saved, pending=pending,
            safe_now=safe_now)
        text = P.headline(state, share_pct=share, basis_text=basis_text, nothing_text=nothing_text,
                          target=target, saved=saved, pending=pending, remaining=remaining, suggested=suggested,
                          buffer=forecast.buffer_paisa, lowest=forecast.lowest_balance_paisa,
                          lowest_day=forecast.lowest_day)
        history = []
        for i, month in enumerate(months[1:], start=1):
            done, _ = self.payments.savings_between(month, P.month_end(month))
            base = P.basis_profit(basis, [profits[m] for m in months[:i]])
            history.append(SavingsMonth(
                month=month, label=P.month_label(month), profit_paisa=profits[month], saved_paisa=done,
                target_paisa=P.target_for(share, base) if share and base > 0 else 0,
                so_far=month == this_month))
        finished = [profits[m] for m in months[1:-1]]  # the last six finished months, oldest first
        scale = P.scale_up(share_pct=share, profits=finished, safe_now=safe_now, saved=saved, pending=pending) \
            if state in ("ready", "done", "no_account") else None
        saved_total, _ = self.payments.savings_between(date(2000, 1, 1), today)
        goals = self.goals(pace=target)
        return SavingsStatus(
            as_of=now, state=state, headline=text, share_pct=share, account=account, month=this_month,
            based_on=last_month, basis=basis, basis_text=basis_text, last_month_profit_paisa=last_profit,
            target_paisa=target, saved_paisa=saved,
            pending_paisa=pending, remaining_paisa=remaining, safe_now_paisa=P.round_down(safe_now),
            suggested_paisa=suggested, buffer_paisa=forecast.buffer_paisa, lowest_day=forecast.lowest_day,
            lowest_balance_paisa=forecast.lowest_balance_paisa, history=history, scale_up=scale,
            method=self._method(share, last_month, forecast, basis), saved_total_paisa=saved_total, goals=goals,
            goals_headline=self.goals_headline(goals, saved_total), jump=jump)

    @staticmethod
    def _method(share: int | None, last_month: date, forecast: CashForecast, basis: str) -> list[str]:
        return [
            "Profit is cash profit for a calendar month: money from sales (cash and mobile money) and baki coming "
            "back, minus what the shop paid for stock, bills and running costs. Household withdrawals and earlier "
            "savings are your own money, not costs, so they never lower it.",
            (f"This month's savings are {share}% of "
             + ("the middle of the last three months' profit, so one odd month moves it less." if basis == "three_months"
                else f"{P.month_label(last_month)}'s profit: a finished month, so the amount is known, not guessed.")
             + " A good month saves more, a lean month less, a loss nothing."
             if share else "Pick a share between 1% and 50%; most owners start with 10%."),
            (f"What can move today is capped by the cash forecast: a transfer lowers every day ahead by the same "
             f"amount, so at most the lowest point ({fmt_taka(forecast.lowest_balance_paisa)} on "
             f"{forecast.lowest_day:%a %d %b}) minus your {fmt_taka(forecast.buffer_paisa)} safety buffer."),
            "Money only moves to the savings account you saved with your PIN, and every transfer waits for your PIN.",
            "After three months of growing profit, KhataPilot suggests saving five points more (up to 30%), only when "
            "the bigger amount still fits this month. It never changes your share by itself.",
        ]
