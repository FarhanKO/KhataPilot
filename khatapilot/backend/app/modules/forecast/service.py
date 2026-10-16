"""Public API of the forecast module: a daily cash projection for the next two weeks.

Explainable by construction. Every taka in the projection comes from one of:
- the everyday baseline: weekday medians of the last 8 weeks (sales in cash and mobile money,
  routine spending, owner withdrawals), with seasonal effects (calendar.py) divided out of history
  and multiplied back into the days ahead;
- known amounts on known days: open bills and supplier credit, recurring bills, payments the owner
  has scheduled (netted against the bills they pay), and each regular supplier's next delivery
  projected from its own cycle and credit terms;
- baki coming back: the shop's average daily collections over the last 4 weeks. Customers pay back
  in a steady trickle, so a flow forecasts better than guessing each customer's day; per-customer
  repayment histories are used for the "chase this baki" suggestion and to name doubtful debts.
The band is an 80% range from the day-to-day spread of the baseline and the uncertainty of each
estimated amount.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core.clock import Clock, local_date
from app.core.money import fmt_taka
from app.modules.forecast.calendar import PRE_EID_FACTOR, sales_factor  # noqa: F401  (PRE_EID_FACTOR: public)
from app.modules.forecast.schemas import CashForecast, ForecastDay, ForecastItem, Shortfall, Suggestion
from app.modules.ledger.schemas import DailyFlow
from app.modules.ledger.service import LedgerService
from app.modules.payments.service import PaymentsService
from app.modules.shop.service import ShopService

HISTORY_DAYS = 56
DOUBTFUL_DAYS = 45
Z_80 = 1.2816
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _whole(paisa: float) -> int:
    """Round an estimate to whole taka: a forecast quoting paisa is false precision."""
    return int(round(paisa / 100)) * 100


def _robust(values: list[float]) -> tuple[float, float]:
    """Median and a robust spread (1.4826 x MAD), so one-off days (a wedding gift) do not dominate."""
    if not values:
        return 0.0, 0.0
    middle = statistics.median(values)
    mad = statistics.median([abs(v - middle) for v in values])
    spread = 1.4826 * mad if mad else (statistics.pstdev(values) if len(values) > 1 else 0.0)
    return middle, spread


@dataclass
class Baseline:
    sales_in: list[float]
    sales_spread: list[float]
    routine_out: list[float]
    routine_spread: list[float]
    drawings: list[float]
    baki_in: float
    baki_spread: float
    # How unsure the baseline level itself is (standard error of the median/mean). Unlike day-to-day
    # noise, this error repeats every day, so it adds up linearly across the horizon.
    level_se: list[float]


@dataclass
class Scenario:
    """A what-if: used to test each suggestion before offering it."""

    extra_in: dict[date, int] = field(default_factory=dict)
    moved: dict[str, date] = field(default_factory=dict)
    no_withdrawals_until: date | None = None


def build_baseline(flows: dict[date, DailyFlow], shop_type: str, today: date) -> Baseline:
    """The everyday part of the forecast, from the days before `today` (pure, so the backtest runs it too)."""
    # One-off withdrawals (a wedding, a hospital bill) are capped at three times the usual one,
    # so a single big day does not make every future Thursday look expensive.
    withdrawals = [f.owner_drawings for f in flows.values() if f.owner_drawings]
    cap = 3 * statistics.median(withdrawals) if withdrawals else 0
    per_day: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for day, flow in flows.items():
        factor, _ = sales_factor(shop_type, day)
        bucket = per_day[day.weekday()]
        bucket["in"].append(flow.sales_in / factor)
        bucket["out"].append(flow.routine_out)
        bucket["draw"].append(min(flow.owner_drawings, cap))
    sales, routine = [_robust(per_day[w]["in"]) for w in range(7)], [_robust(per_day[w]["out"]) for w in range(7)]
    collected = [f.baki_collected for d, f in flows.items() if d >= today - timedelta(days=28)]
    baki_se = statistics.pstdev(collected) / math.sqrt(len(collected)) if len(collected) > 1 else 0.0
    return Baseline(
        sales_in=[s[0] for s in sales], sales_spread=[s[1] for s in sales],
        routine_out=[r[0] for r in routine], routine_spread=[r[1] for r in routine],
        drawings=[statistics.mean(per_day[w]["draw"]) if per_day[w]["draw"] else 0.0 for w in range(7)],
        baki_in=statistics.mean(collected) if collected else 0.0,
        baki_spread=statistics.pstdev(collected) if len(collected) > 1 else 0.0,
        level_se=[1.2533 * sales[w][1] / math.sqrt(max(1, len(per_day[w]["in"]))) + baki_se for w in range(7)],
    )


def expected_inflow(baseline: Baseline, shop_type: str, day: date) -> float:
    """Everyday money in on `day`: sales in cash and mobile money, plus baki coming back."""
    return baseline.sales_in[day.weekday()] * sales_factor(shop_type, day)[0] + baseline.baki_in


def _uncertainty(item: ForecastItem) -> float:
    return 0.0 if item.certain else item.amount_paisa * 0.15


def _possessive(name: str | None) -> str:
    name = name or "the payee"
    return f"{name}'" if name.endswith("s") else f"{name}'s"


class ForecastService:
    def __init__(self, session: Session, clock: Clock, shop_id: str, *, buffer_paisa: int) -> None:
        self.clock = clock
        self.shop_id = shop_id
        self.buffer = buffer_paisa
        self.ledger = LedgerService(session, clock)
        self.payments = PaymentsService(session, clock, shop_id)
        self.shops = ShopService(session, clock)

    # ---------------------------------------------------------------- inputs

    def _baseline(self, today: date, shop_type: str, since: date, bill_categories: set[str]) -> Baseline:
        start = max(since, today - timedelta(days=HISTORY_DAYS))
        flows = self.ledger.daily_flows(self.shop_id, start, today - timedelta(days=1), bill_categories)
        return build_baseline(flows, shop_type, today)

    @staticmethod
    def _doubtful(profile, today: date) -> bool:
        """Baki the forecast does not count on: owed for over 45 days and over twice the customer's usual time."""
        if profile.outstanding_paisa <= 0 or profile.oldest_open_since is None:
            return False
        owed = (today - profile.oldest_open_since).days
        return owed > max(DOUBTFUL_DAYS, 2 * (profile.average_days_to_repay or 0))

    def _items(self, today: date, end: date) -> list[ForecastItem]:
        items: list[ForecastItem] = []
        open_obligations = self.ledger.open_obligations(self.shop_id)
        for o in open_obligations:
            overdue = o.due_date < today
            kind = "supplier_bill" if o.kind == "supplier_payable" else "bill"
            label = (f"{o.counterparty_name}: {o.note or o.category}" if kind == "bill"
                     else f"{o.counterparty_name}: bill due {o.due_date:%a %d %b}" + (" (overdue)" if overdue else ""))
            day = today if overdue else o.due_date
            if day <= end:
                items.append(ForecastItem(key=f"obl:{o.id}", label=label, amount_paisa=o.amount_paisa, direction="out",
                                          day=day, kind=kind, certain=True, overdue=overdue,
                                          counterparty=o.counterparty_name))

        # Recurring bills not already on the books for that month.
        month_start = today.replace(day=1)
        paid = self.ledger.categories_paid(self.shop_id, month_start, today)
        for bill in self.ledger.recurring_bills(self.shop_id):
            day = today
            while day <= end:
                last = (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
                occurrence = day.replace(day=min(bill.day_of_month, last.day))
                same_month_open = any(o.kind == "bill" and o.category == bill.category
                                      and o.due_date.month == occurrence.month for o in open_obligations)
                already_paid = occurrence.month == today.month and bill.category in paid
                if today <= occurrence <= end and not same_month_open and not already_paid:
                    items.append(ForecastItem(key=f"rbill:{bill.id}:{occurrence}", label=f"{bill.payee}: {bill.name}",
                                              amount_paisa=bill.amount_paisa, direction="out", day=occurrence,
                                              kind="bill", certain=True, counterparty=bill.payee))
                day = last + timedelta(days=1)

        # Each regular credit supplier's next deliveries, and the bills they will bring inside the window.
        for cycle in self.ledger.supplier_credit_cycles(self.shop_id, today):
            delivery = cycle.last_delivery + timedelta(days=cycle.cycle_days)
            while delivery < today:
                delivery += timedelta(days=cycle.cycle_days)
            while delivery <= end:
                due = delivery + timedelta(days=cycle.terms_days)
                if due <= end:
                    usual = _whole(cycle.usual_paisa)
                    items.append(ForecastItem(
                        key=f"cycle:{cycle.name}:{delivery}", amount_paisa=usual, direction="out", day=due,
                        kind="expected_supplier_bill", certain=False, counterparty=cycle.name,
                        label=(f"{cycle.name}: expected bill for the {delivery:%a %d %b} delivery (usually "
                               f"{fmt_taka(usual)}, {cycle.terms_days}-day credit)")))
                delivery += timedelta(days=cycle.cycle_days)

        # Payments the owner already scheduled; they settle the bills they pay, so net them off.
        for payment in self.payments.list(limit=200):
            if payment.status not in ("scheduled", "paused") or not (today <= payment.execute_on <= end):
                continue
            items.append(ForecastItem(key=f"sched:{payment.id}", direction="out", day=payment.execute_on,
                                      amount_paisa=payment.amount_paisa, kind="scheduled_payment", certain=True,
                                      counterparty=payment.payee_name or f"****{payment.payee_number[-4:]}",
                                      label=f"Scheduled payment to {payment.payee_name or 'payee'}"))
            remaining = payment.amount_paisa
            for item in sorted(items, key=lambda i: i.day):
                if remaining <= 0:
                    break
                if item.kind in ("supplier_bill", "expected_supplier_bill") and payment.payee_name and \
                        (item.counterparty or "").lower() == payment.payee_name.lower():
                    take = min(remaining, item.amount_paisa)
                    remaining -= take
                    items[items.index(item)] = item.model_copy(update={"amount_paisa": item.amount_paisa - take})
            items = [i for i in items if i.amount_paisa > 0]
        return items

    # ---------------------------------------------------------------- projection

    def _simulate(self, *, today: date, days: int, start: int, baseline: Baseline, shop_type: str,
                  today_actual: DailyFlow, items: list[ForecastItem], scenario: Scenario) -> list[ForecastDay]:
        by_day: dict[date, list[ForecastItem]] = defaultdict(list)
        for item in items:
            day = scenario.moved.get(item.key, item.day)
            by_day[day].append(item if day == item.day else item.model_copy(update={"day": day}))
        balance, variance, level, result = float(start), 0.0, 0.0, []
        for offset in range(days):
            day = today + timedelta(days=offset)
            weekday = day.weekday()
            factor, _ = sales_factor(shop_type, day)
            sales = baseline.sales_in[weekday] * factor
            routine = baseline.routine_out[weekday]
            drawings = 0.0 if scenario.no_withdrawals_until and day <= scenario.no_withdrawals_until \
                else baseline.drawings[weekday]
            baki = baseline.baki_in
            if offset == 0:  # part of today has already happened and is in the books
                sales = max(0.0, sales - today_actual.sales_in)
                routine = max(0.0, routine - today_actual.routine_out)
                drawings = max(0.0, drawings - today_actual.owner_drawings)
                baki = max(0.0, baki - today_actual.baki_collected)
            day_items = by_day.get(day, [])
            money_in = sales + baki + scenario.extra_in.get(day, 0) + sum(
                i.amount_paisa for i in day_items if i.direction == "in")
            money_out = routine + drawings + sum(i.amount_paisa for i in day_items if i.direction == "out")
            balance += money_in - money_out
            variance += (baseline.sales_spread[weekday] ** 2 + baseline.routine_spread[weekday] ** 2
                         + baseline.baki_spread ** 2 + sum(_uncertainty(i) ** 2 for i in day_items))
            level += baseline.level_se[weekday]
            spread = Z_80 * math.sqrt(variance + level ** 2)
            result.append(ForecastDay(
                day=day, expected_in_paisa=_whole(money_in), expected_out_paisa=_whole(money_out),
                items=sorted(day_items, key=lambda i: -i.amount_paisa), balance_paisa=_whole(balance),
                low_paisa=_whole(balance - spread), high_paisa=_whole(balance + spread),
                below_buffer=balance < self.buffer,
            ))
        return result

    # ---------------------------------------------------------------- the forecast

    def forecast(self, days: int = 14) -> CashForecast:
        now = self.clock.now()
        today = local_date(now)
        end = today + timedelta(days=days - 1)
        shop = self.shops.get(self.shop_id)
        shop_type = shop.shop_type if shop else "grocery"
        since = shop.data_since if shop else today - timedelta(days=HISTORY_DAYS)
        bill_categories = {b.category for b in self.ledger.recurring_bills(self.shop_id)}
        baseline = self._baseline(today, shop_type, since, bill_categories)
        items = self._items(today, end)
        profiles = self.ledger.repayment_profiles(self.shop_id, today)
        start = sum(self.ledger.balances(self.shop_id).values())
        today_actual = self.ledger.daily_flows(self.shop_id, today, today, bill_categories)[today]

        def run(scenario: Scenario) -> list[ForecastDay]:
            return self._simulate(today=today, days=days, start=start, baseline=baseline, shop_type=shop_type,
                                  today_actual=today_actual, items=items, scenario=scenario)

        projection = run(Scenario())
        lowest = min(projection, key=lambda d: d.balance_paisa)
        first = next((d for d in projection if d.below_buffer), None)
        shortfall = None
        if first is not None:
            shortfall = Shortfall(day=first.day, balance_paisa=first.balance_paisa,
                                  short_by_paisa=self.buffer - first.balance_paisa,
                                  cause=self._cause(projection, first.day))
        doubtful = [p for p in profiles if self._doubtful(p, today)]
        suggestions = self._suggest(today, end, projection, shortfall, lowest, profiles, baseline, run) \
            if shortfall else []
        return CashForecast(
            as_of=now, days_ahead=days, start_balance_paisa=start, buffer_paisa=self.buffer, days=projection,
            first_shortfall=shortfall, lowest_day=lowest.day, lowest_balance_paisa=lowest.balance_paisa,
            suggestions=suggestions, headline=self._headline(days, shortfall, lowest),
            method=self._method(baseline, items, doubtful),
        )

    @staticmethod
    def _cause(projection: list[ForecastDay], day: date) -> str:
        """The biggest single payment in the three days up to the shortfall, in words."""
        recent = [i for d in projection if day - timedelta(days=3) <= d.day <= day for i in d.items
                  if i.direction == "out"]
        biggest = max(recent, key=lambda i: i.amount_paisa, default=None)
        if biggest is None:
            return "as everyday spending runs ahead of sales"
        when = "" if biggest.day == day else f" on {biggest.day:%a %d %b}"  # the headline already names the day
        amount, who = fmt_taka(biggest.amount_paisa), biggest.counterparty
        if biggest.kind == "scheduled_payment":
            return f"after your scheduled {amount} payment to {who}{when}"
        if biggest.kind == "expected_supplier_bill":
            return f"when {_possessive(who)} next bill (usually {amount}) falls due{when}"
        return f"when {_possessive(who)} {amount} bill falls due{when}"

    def _headline(self, days: int, shortfall: Shortfall | None, lowest: ForecastDay) -> str:
        if shortfall is None:
            return (f"You have enough cash for the next {days} days. Your lowest point is about "
                    f"{fmt_taka(lowest.balance_paisa)} on {lowest.day:%A %d %b} "
                    f"(safety buffer {fmt_taka(self.buffer)}).")
        if shortfall.balance_paisa < 0:
            text = (f"You could run out of cash on {shortfall.day:%A %d %b}, {shortfall.cause}: about "
                    f"{fmt_taka(shortfall.balance_paisa)}, {fmt_taka(shortfall.short_by_paisa)} below your "
                    f"{fmt_taka(self.buffer)} safety buffer.")
        else:
            text = (f"Cash gets tight on {shortfall.day:%A %d %b}, {shortfall.cause}: about "
                    f"{fmt_taka(shortfall.balance_paisa)} left, {fmt_taka(shortfall.short_by_paisa)} below your "
                    f"{fmt_taka(self.buffer)} safety buffer.")
        if lowest.day != shortfall.day:
            text += f" Lowest point: {fmt_taka(lowest.balance_paisa)} on {lowest.day:%a %d %b}."
        return text

    def _suggest(self, today, end, projection, shortfall, lowest, profiles, baseline, run) -> list[Suggestion]:
        """Each suggestion is re-run through the same projection, so its effect is computed, not guessed."""
        suggestions: list[Suggestion] = []
        gap = self.buffer - lowest.balance_paisa

        def outcome(scenario: Scenario) -> int:
            return min(d.balance_paisa for d in run(scenario))

        # Overdue baki: first what the forecast does not count on at all, then the biggest late payers.
        owed = [p for p in profiles if p.outstanding_paisa > 0 and p.oldest_open_since
                and (today - p.oldest_open_since).days >= 14]
        owed.sort(key=lambda p: (not self._doubtful(p, today), -p.outstanding_paisa))
        chosen, total = [], 0
        for p in owed:
            if total >= gap or len(chosen) == 3:
                break
            chosen.append(p)
            total += p.outstanding_paisa
        if chosen:
            new_low = outcome(Scenario(extra_in={today + timedelta(days=1): total}))
            names = ", ".join(f"{p.name} {fmt_taka(p.outstanding_paisa)} ({(today - p.oldest_open_since).days} days)"
                              for p in chosen)
            suggestions.append(Suggestion(kind="collect_baki", amount_paisa=total, new_lowest_paisa=new_low,
                                          closes_gap=new_low >= self.buffer,
                                          text=f"Collect {fmt_taka(total)} of overdue baki: {names}."))

        # Ask the biggest supplier bill before the shortfall to wait a week. Beyond the window it is
        # simulated on the window's last day: conservative, so "closes the gap" is never overstated.
        movable = [i for d in projection if d.day <= shortfall.day for i in d.items
                   if i.direction == "out" and i.kind in ("supplier_bill", "expected_supplier_bill")]
        if movable:
            item = max(movable, key=lambda i: i.amount_paisa)
            later = item.day + timedelta(days=7)
            new_low = outcome(Scenario(moved={item.key: min(later, end)}))
            suggestions.append(Suggestion(
                kind="move_payment", amount_paisa=item.amount_paisa, new_lowest_paisa=new_low,
                closes_gap=new_low >= self.buffer,
                text=(f"Ask {item.counterparty} to take their {fmt_taka(item.amount_paisa)} on {later:%a %d %b} "
                      f"instead of {item.day:%a %d %b}.")))

        withdrawals = _whole(sum(baseline.drawings[d.day.weekday()] for d in projection if d.day <= shortfall.day))
        if withdrawals >= 500_00:
            new_low = outcome(Scenario(no_withdrawals_until=shortfall.day))
            suggestions.append(Suggestion(
                kind="pause_withdrawals", amount_paisa=withdrawals, new_lowest_paisa=new_low,
                closes_gap=new_low >= self.buffer,
                text=(f"Hold back household withdrawals (about {fmt_taka(withdrawals)}) "
                      f"until {shortfall.day:%a %d %b}.")))

        loan = int(math.ceil(gap / 5_000_00)) * 5_000_00
        suggestions.append(Suggestion(
            kind="loan", amount_paisa=loan, new_lowest_paisa=lowest.balance_paisa + loan, closes_gap=True,
            text=(f"A short working-capital loan of about {fmt_taka(loan)} would cover the gap. "
                  "Ask me \"can I get a loan?\" to see your options.")))
        return suggestions

    def _method(self, baseline: Baseline, items: list[ForecastItem], doubtful: list) -> list[str]:
        weekday_sales = ", ".join(f"{WEEKDAYS[w][:3]} {fmt_taka(_whole(baseline.sales_in[w]))}" for w in range(7))
        cycles = sorted({i.counterparty for i in items if i.kind == "expected_supplier_bill"})
        lines = [
            f"Sales in cash and mobile money: the median of each weekday over the last 8 weeks ({weekday_sales}), "
            "adjusted for Ramadan and Eid.",
            "Everyday spending (stock paid on delivery, small costs) and household withdrawals: weekday medians "
            "and averages over the same 8 weeks; one-off big withdrawals are capped.",
            "Known amounts: open bills and supplier credit, recurring bills, and payments you scheduled.",
            f"Baki coming back: the average of the last 4 weeks, about {fmt_taka(_whole(baseline.baki_in))} a day.",
            "The low/high band is an 80% range.",
        ]
        if cycles:
            lines.insert(3, "Regular credit suppliers' next bills projected from their own delivery cycle and credit "
                            f"terms: {', '.join(cycles)}.")
        if doubtful:
            total = sum(p.outstanding_paisa for p in doubtful)
            lines.insert(-1, f"Not counted: {fmt_taka(total)} of baki owed for over {DOUBTFUL_DAYS} days "
                             f"({', '.join(p.name for p in doubtful)}).")
        return lines
