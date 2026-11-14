"""The growth arithmetic: pure functions, so every number is easy to test and explain.

- A month of the books (`MonthBooks`) uses the same cash profit as the savings plan: sales in cash and
  mobile money plus baki coming back, minus stock paid for and running costs.
- Gross margin is 1 - stock bought / goods sold over three months (baki sales and credit purchases
  included, so it does not jump with the timing of payments).
- The trend is a straight line through six months of sales, as a share of the average, capped at 3%
  a month either way so one Eid month cannot promise the moon.
- What kind of shop it is comes from two things the books already measure: how much a day swings
  around the usual for its weekday (the safety buffer's measure) and that trend. A steadier shop can
  put more of its profit back into stock; a choppy one should keep more aside.
- Ramadan and Eid use the forecast's own stated multipliers on sales: the extra (or missing) sales of a
  season month earn the shop's margin, so history is stripped of them before the base and the trend are
  measured, and they are added back into the months ahead.
- Stock put back into the shop earns the shop's own margin when it sells. We assume it sells once a
  month and half of it is new sales (the rest would have sold anyway): a deliberately modest rule.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.core.money import fmt_taka
from app.modules.forecast.service import sales_factor
from app.modules.growth.schemas import FlowPart, Lever, ScenarioMonth, Stage, Trend
from app.modules.ledger.schemas import SAVINGS_CATEGORY

SELL_THROUGH = 0.5  # of each extra taka of stock, the part that becomes new sales each month
COLLECT_SHARE = 0.5  # the collect-baki path assumes half of the overdue baki comes back
TREND_CAP = 0.03
Z_80 = 1.2816
OVERDUE_DAYS = 14
REINVEST_BY_STAGE: dict[Stage, int] = {"ready_to_grow": 25, "steady": 20, "up_and_down": 15, "choppy": 10,
                                       "shrinking": 0, "cushion_first": 0}
STAGE_LABEL: dict[Stage, str] = {
    "ready_to_grow": "Steady and growing", "steady": "Steady", "up_and_down": "Goes up and down",
    "choppy": "Choppy", "shrinking": "Sales are falling", "cushion_first": "Cushion first",
}
MAX_COST_PARTS = 4


def whole(paisa: float) -> int:
    """Whole hundreds of taka: these are estimates, so no false precision."""
    return int(round(paisa / 100_00)) * 100_00


def add_months(month: date, n: int) -> date:
    y, m = divmod(month.month - 1 + n, 12)
    return date(month.year + y, m + 1, 1)


def month_end(month: date) -> date:
    return add_months(month, 1) - timedelta(days=1)


@dataclass
class MonthBooks:
    month: date
    sales: int = 0  # cash and mobile money
    baki_sales: int = 0
    baki_back: int = 0
    stock_cash: int = 0  # stock paid for on delivery
    supplier_paid: int = 0  # supplier credit paid off
    purchases: int = 0  # all stock bought, credit included
    costs: dict[str, int] = field(default_factory=dict)  # running costs by category
    drawings: int = 0
    savings: int = 0

    @classmethod
    def from_rows(cls, month: date, rows: list[tuple[str, str, str, int]]) -> MonthBooks:
        books = cls(month=month)
        for kind, method, category, amount in rows:
            if kind == "sale" and method != "credit":
                books.sales += amount
            elif kind == "baki_sale":
                books.baki_sales += amount
            elif kind == "baki_repayment":
                books.baki_back += amount
            elif kind == "purchase":
                books.purchases += amount
                if method != "credit":
                    books.stock_cash += amount
            elif kind == "supplier_payment":
                books.supplier_paid += amount
            elif kind in ("expense", "other_out"):
                label = category or "Other"
                books.costs[label] = books.costs.get(label, 0) + amount
            elif kind == "owner_drawing":
                if category == SAVINGS_CATEGORY:
                    books.savings += amount
                else:
                    books.drawings += amount
        return books

    @property
    def money_in(self) -> int:
        return self.sales + self.baki_back

    @property
    def stock_out(self) -> int:
        return self.stock_cash + self.supplier_paid

    @property
    def running_costs(self) -> int:
        return sum(self.costs.values())

    @property
    def profit(self) -> int:
        return self.money_in - self.stock_out - self.running_costs

    @property
    def goods_sold(self) -> int:
        return self.sales + self.baki_sales


# ---------------------------------------------------------------- measures

def gross_margin(months: list[MonthBooks]) -> float:
    sold = sum(m.goods_sold for m in months)
    if sold <= 0:
        return 0.0
    return min(0.6, max(0.0, 1 - sum(m.purchases for m in months) / sold))


def trend(values: list[int]) -> float:
    """Monthly growth from a least-squares line, as a share of the average, capped at +-3%."""
    n = len(values)
    mean = statistics.mean(values) if values else 0
    if n < 3 or mean <= 0:
        return 0.0
    xs = range(n)
    x_mean = (n - 1) / 2
    slope = sum((x - x_mean) * (v - mean) for x, v in zip(xs, values)) / sum((x - x_mean) ** 2 for x in xs)
    return max(-TREND_CAP, min(TREND_CAP, slope / mean))


def spread(values: list[int]) -> float:
    """A robust spread (1.4826 x MAD), so one odd month does not dominate."""
    if len(values) < 2:
        return 0.0
    middle = statistics.median(values)
    mad = statistics.median([abs(v - middle) for v in values])
    return 1.4826 * mad if mad else statistics.pstdev(values)


def trend_word(monthly: float) -> Trend:
    return "growing" if monthly >= 0.01 else "shrinking" if monthly <= -0.01 else "flat"


def stage_of(*, steadiness: str | None, monthly_trend: float, cushion_ok: bool) -> Stage:
    if not cushion_ok:
        return "cushion_first"
    if trend_word(monthly_trend) == "shrinking":
        return "shrinking"
    if steadiness == "steady":
        return "ready_to_grow" if trend_word(monthly_trend) == "growing" else "steady"
    return "choppy" if steadiness == "choppy" else "up_and_down"


def stage_text(stage: Stage, *, monthly_trend: float, swing_pct: float | None, lowest: int, buffer: int,
               lowest_day: date) -> str:
    pace = f"{abs(monthly_trend) * 100:.1f}% a month"
    swing = f" (a usual day lands about {swing_pct:.0f}% away from normal)" if swing_pct is not None else ""
    return {
        "cushion_first": (f"Cash is forecast to dip to {fmt_taka(lowest)} on {lowest_day:%a %d %b}, below your "
                          f"{fmt_taka(buffer)} safety buffer. Build that cushion first; grow once it holds."),
        "shrinking": (f"Sales have been falling about {pace}. Before putting more into stock, find out why: costs, "
                      "prices, baki and the household's share come first."),
        "ready_to_grow": (f"Your takings are steady{swing} and sales are growing about {pace}. A shop like this "
                          "can safely put a bigger part of its profit back into stock."),
        "steady": (f"Your takings are steady{swing}, and sales are flat. Steady shops can put a good part of "
                   "profit back into stock to grow."),
        "up_and_down": (f"Your takings go up and down a fair bit{swing}. Grow in steps, and keep a bit more aside "
                        "for slow weeks."),
        "choppy": (f"Your takings go up and down a lot{swing}. Keep a bigger cushion and put back only a small "
                   "part of profit at a time."),
    }[stage]


# ---------------------------------------------------------------- the money flow

def flow_parts(books: MonthBooks) -> tuple[list[FlowPart], list[FlowPart], list[FlowPart]]:
    money_in = [FlowPart(key="sales", label="Sales (cash and mobile)", amount_paisa=books.sales),
                FlowPart(key="baki_back", label="Baki coming back", amount_paisa=books.baki_back)]
    ordered = sorted(books.costs.items(), key=lambda kv: -kv[1])
    shown, rest = ordered[:MAX_COST_PARTS], sum(v for _, v in ordered[MAX_COST_PARTS:])
    costs = [FlowPart(key="stock", label="Stock", amount_paisa=books.stock_out)]
    costs += [FlowPart(key=f"cost:{name}", label=name, amount_paisa=amount) for name, amount in shown]
    if rest:
        costs.append(FlowPart(key="cost:other", label="Other costs", amount_paisa=rest))
    kept = books.profit - books.drawings - books.savings
    uses = [FlowPart(key="household", label="Household", amount_paisa=books.drawings),
            FlowPart(key="savings", label="Your savings", amount_paisa=books.savings),
            FlowPart(key="kept", label="Kept in the shop" if kept >= 0 else "Taken from the shop's cash",
                     amount_paisa=kept)]
    return money_in, costs, uses


def flow_headline(books: MonthBooks, label: str) -> str:
    if books.money_in <= 0:
        return f"Nothing came in during {label}."
    if books.profit <= 0:
        return (f"In {label} the shop paid out {fmt_taka(-books.profit)} more than came in: stock and costs took "
                "everything.")
    per = lambda v: round(v * 100 / books.money_in)  # noqa: E731
    text = (f"Of every ৳100 that came in during {label}, ৳{per(books.stock_out)} went on stock, "
            f"৳{per(books.running_costs)} on running costs and ৳{per(books.profit)} was profit.")
    taken = books.drawings + books.savings
    if taken > books.profit:
        text += (f" The household and savings took {fmt_taka(taken)}, {fmt_taka(taken - books.profit)} more than the "
                 "shop made, so its own cash went down.")
    return text


# ---------------------------------------------------------------- seasons

def month_factor(shop_type: str, month: date) -> float:
    """The month's average sales multiplier (1.0 for an ordinary month)."""
    days = [month + timedelta(days=i) for i in range((month_end(month) - month).days + 1)]
    return statistics.mean(sales_factor(shop_type, d)[0] for d in days)


def season_of(shop_type: str, month: date) -> str | None:
    days = [month + timedelta(days=i) for i in range((month_end(month) - month).days + 1)]
    reasons = [r for _, r in (sales_factor(shop_type, d) for d in days) if r]
    if not reasons:
        return None
    return "Ramadan and Eid" if "Ramadan" in reasons and any("Eid" in r for r in reasons) else \
        "Ramadan" if "Ramadan" in reasons else "Eid"


TREND_DAMPING = 0.5  # half the measured trend: backtested to beat the full trend and no trend


@dataclass
class Everyday:
    """A shop's ordinary month, with the seasons taken out."""

    base: int  # profit
    trend: float  # monthly sales growth (damped)
    sold: int  # goods sold
    band: float  # the spread of a new month's profit around the base (for the 80% range)


def everyday(months: list[MonthBooks], factors: list[float], margin: float) -> Everyday:
    """`months` oldest first (up to six), `factors` their month_factor. A season month's extra sales, at the
    shop's margin, are taken out of its profit first. The base is the average of those months, the band a
    prediction interval from their spread (sample deviation x sqrt(1 + 1/n)). Both choices were picked by
    the backtest (scripts/evaluate_growth.py): the average of up to six months beat the median of three, and
    this band holds about 80% of the time where the robust spread of raw profits held about 62%."""
    sold = [m.goods_sold / f for m, f in zip(months, factors)]
    profit = [m.profit - (f - 1) * s * margin for m, f, s in zip(months, factors, sold)]
    n = len(profit)
    band = statistics.stdev(profit) * math.sqrt(1 + 1 / n) if n > 1 else 0.0
    return Everyday(base=int(statistics.mean(profit)), trend=TREND_DAMPING * trend([int(s) for s in sold]),
                    sold=int(statistics.median(sold[-3:])), band=band)


def seasonal_extras(shop_type: str, first: date, months: int, usual: Everyday, margin: float) -> list[float]:
    """The profit a season adds (Ramadan, the week before Eid) or takes away (Eid closures), month by month."""
    return [(month_factor(shop_type, add_months(first, m - 1)) - 1) * usual.sold * (1 + usual.trend) ** m * margin
            for m in range(1, months + 1)]


# ---------------------------------------------------------------- the paths ahead

def project(*, base: int, monthly_trend: float, margin: float, reinvest_pct: int, start_capital: int,
            months: int, band: float, first: date, seasonal: list[float] | None = None,
            seasons: list[str | None] | None = None) -> list[ScenarioMonth]:
    """Profit month by month: the ordinary month along the trend, plus the season's effect, plus what extra
    stock (put back from profit, or freed by collecting baki) earns on the half that becomes new sales."""
    capital, out = float(start_capital), []
    for m in range(1, months + 1):
        organic = base * (1 + monthly_trend) ** m + (seasonal[m - 1] if seasonal else 0.0)
        profit = organic + capital * margin * SELL_THROUGH
        month = add_months(first, m - 1)
        out.append(ScenarioMonth(month=month, label=f"{month:%b} ’{month:%y}", profit_paisa=whole(profit),
                                 low_paisa=whole(profit - Z_80 * band), high_paisa=whole(profit + Z_80 * band),
                                 season=seasons[m - 1] if seasons else None))
        capital += reinvest_pct / 100 * max(0.0, profit)
    return out


def savings_on_the_way(share_pct: int, last_profit: int, path: list[ScenarioMonth]) -> int:
    """Each month saves the share of the month before's profit."""
    before = [last_profit] + [m.profit_paisa for m in path[:-1]]
    return whole(sum(max(0, p) for p in before) * share_pct / 100)


# ---------------------------------------------------------------- what would help most

def household_lever(recent: list[MonthBooks]) -> Lever | None:
    taken = statistics.mean(m.drawings for m in recent)
    profit = statistics.mean(m.profit for m in recent)
    gap = taken - max(0, profit)
    if gap < 500_00 or taken <= 0:
        return None
    return Lever(kind="household", title="Take out no more than the shop makes",
                 text=(f"Over the last three months the household took about {fmt_taka(whole(taken))} a month and "
                       f"the shop made about {fmt_taka(whole(profit))}. Keeping withdrawals within profit (savings "
                       f"first) stops the shop's cash shrinking by about {fmt_taka(whole(gap))} a month."),
                 cash_paisa=0, monthly_paisa=whole(gap), tab="savings")


def baki_lever(overdue_paisa: int, customers: int, margin: float) -> Lever | None:
    if overdue_paisa < 500_00:
        return None
    monthly = overdue_paisa * margin * SELL_THROUGH
    return Lever(kind="collect_baki", title=f"Collect {fmt_taka(whole(overdue_paisa))} of overdue baki",
                 text=(f"{customers} customer{'s have' if customers != 1 else ' has'} owed "
                       f"{fmt_taka(whole(overdue_paisa))} for {OVERDUE_DAYS} days or more. Collected and put into "
                       f"stock, that money could earn about {fmt_taka(whole(monthly))} more profit a month at your "
                       f"margin. Tap Prepare reminders in the Ledger tab."),
                 cash_paisa=whole(overdue_paisa), monthly_paisa=whole(monthly), tab="ledger")


def supplier_lever(name: str, usual_paisa: int, cycle_days: int, terms_days: int, margin: float) -> Lever | None:
    freed = usual_paisa * 7 / max(1, cycle_days)
    if freed < 500_00:
        return None
    return Lever(kind="supplier_terms", title=f"Ask {name} for a week more credit",
                 text=(f"{name} delivers about every {cycle_days} days (usually {fmt_taka(whole(usual_paisa))}) and "
                       f"gives {terms_days} days to pay. Seven more days would keep about {fmt_taka(whole(freed))} "
                       "in your cash for good, without borrowing."),
                 cash_paisa=whole(freed), monthly_paisa=whole(freed * margin * SELL_THROUGH), tab="chat")


def cost_levers(recent: list[MonthBooks], earlier: list[MonthBooks]) -> list[Lever]:
    levers = []
    for name in {k for m in recent for k in m.costs}:
        now = statistics.mean(m.costs.get(name, 0) for m in recent)
        before = statistics.mean(m.costs.get(name, 0) for m in earlier) if earlier else 0
        if before > 0 and now >= before * 1.15 and now - before >= 500_00:
            levers.append(Lever(kind="rising_cost", title=f"{name} is up {round((now / before - 1) * 100)}%",
                                text=(f"{name} cost about {fmt_taka(whole(now))} a month lately, against "
                                      f"{fmt_taka(whole(before))} the three months before. Bringing it back would "
                                      f"add about {fmt_taka(whole(now - before))} a month to profit."),
                                cash_paisa=0, monthly_paisa=whole(now - before), tab="ledger"))
    return sorted(levers, key=lambda lv: -lv.monthly_paisa)[:2]


def margin_lever(recent: list[MonthBooks], earlier: list[MonthBooks]) -> Lever | None:
    if not earlier:
        return None
    now, before = gross_margin(recent), gross_margin(earlier)
    if before - now < 0.015:
        return None
    sold = statistics.mean(m.goods_sold for m in recent)
    monthly = (before - now) * sold
    return Lever(kind="margin", title=f"Your margin slipped from {before:.0%} to {now:.0%}",
                 text=(f"Stock is costing more for what you charge. Check the prices of goods whose cost went up; "
                       f"getting back to {before:.0%} is worth about {fmt_taka(whole(monthly))} a month."),
                 cash_paisa=0, monthly_paisa=whole(monthly), tab="ledger")


def reinvest_lever(reinvest_pct: int, margin: float, extra_year: int, last_month_gain: int) -> Lever | None:
    if reinvest_pct <= 0 or extra_year < 500_00:
        return None
    return Lever(kind="reinvest", title=f"Put {reinvest_pct}% of profit back into stock",
                 text=(f"At your {margin:.0%} margin, putting {reinvest_pct}% of each month's profit back into stock "
                       f"could add about {fmt_taka(last_month_gain)} a month to profit by month 12, "
                       f"{fmt_taka(extra_year)} over the year. The rest is still yours to save and take home."),
                 cash_paisa=0, monthly_paisa=whole(extra_year / 12), tab="chat")


def rank(levers: list[Lever], margin: float, *, limit: int = 5) -> list[Lever]:
    """Cushion and household first (growth is pointless while they leak), then by value: monthly profit
    plus what one-off cash would earn as stock."""
    first = {"cushion": 0, "household": 1}
    value = lambda lv: lv.monthly_paisa + lv.cash_paisa * margin * SELL_THROUGH  # noqa: E731
    return sorted(levers, key=lambda lv: (first.get(lv.kind, 2), -value(lv)))[:limit]


def safe_ratio(value: float | None) -> float | None:
    return None if value is None or math.isnan(value) else round(value * 100, 1)
