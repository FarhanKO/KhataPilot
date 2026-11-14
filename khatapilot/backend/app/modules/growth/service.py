"""Public API of the growth module: a read-only plan for growing the shop, worked out from its own books.

Nothing here acts. The moves it suggests point at things the owner already does in the app (baki
reminders, the savings plan, a word with a supplier), each of which goes through the guard as usual.
"""

from __future__ import annotations

import statistics

from sqlalchemy.orm import Session

from app.core.clock import Clock, local_date
from app.core.money import fmt_taka
from app.modules.forecast.service import ForecastService
from app.modules.growth import plan as G
from app.modules.growth.schemas import GrowthPlan, Lever, MoneyFlow, Scenario, ShopProfile
from app.modules.ledger.service import LedgerService
from app.modules.savings.service import SavingsService
from app.modules.shop.service import ShopService

HISTORY_MONTHS = 6
HORIZON_MONTHS = 12
DEFAULT_SHARE_PCT = 10  # the savings line when the owner has no plan yet


class GrowthService:
    def __init__(self, session: Session, clock: Clock, shop_id: str, *, standard_buffer_paisa: int) -> None:
        self.session = session
        self.clock = clock
        self.shop_id = shop_id
        self.ledger = LedgerService(session, clock)
        self.shops = ShopService(session, clock)
        self.forecasts = ForecastService(session, clock, shop_id, standard_buffer_paisa=standard_buffer_paisa)
        self.savings = SavingsService(session, clock, shop_id, standard_buffer_paisa=standard_buffer_paisa)

    def _months(self, this_month) -> list[G.MonthBooks]:
        """The last six finished months that have books, oldest first."""
        shop = self.shops.get(self.shop_id)
        since = shop.data_since if shop else None
        books = []
        for back in range(HISTORY_MONTHS, 0, -1):
            month = G.add_months(this_month, -back)
            start = max(month, since) if since else month
            if start > G.month_end(month):
                continue
            rows = self.ledger.category_totals(self.shop_id, start, G.month_end(month))
            month_books = G.MonthBooks.from_rows(month, rows)
            if month_books.money_in > 0:
                books.append(month_books)
        return books

    def plan(self) -> GrowthPlan:
        now = self.clock.now()
        today = local_date(now)
        this_month = today.replace(day=1)
        forecast = self.forecasts.forecast()
        buffer = forecast.buffer
        months = self._months(this_month)
        if not months:
            raise ValueError("there is not a finished month in the books yet")
        recent, earlier = months[-3:], months[:-3]
        last = months[-1]
        margin = G.gross_margin(recent)
        shop = self.shops.get(self.shop_id)
        shop_type = shop.shop_type if shop else "grocery"
        usual = G.everyday(months, [G.month_factor(shop_type, m.month) for m in months], margin)
        monthly_trend, base = usual.trend, usual.base
        seasonal = G.seasonal_extras(shop_type, this_month, HORIZON_MONTHS, usual, margin)
        seasons = [G.season_of(shop_type, G.add_months(this_month, i)) for i in range(HORIZON_MONTHS)]
        profits = [m.profit for m in months]
        band = usual.band
        middle = abs(statistics.median(profits)) or 1
        cushion_ok = forecast.lowest_balance_paisa >= forecast.buffer_paisa
        stage = G.stage_of(steadiness=buffer.steadiness, monthly_trend=monthly_trend, cushion_ok=cushion_ok)
        swing_pct = G.safe_ratio(buffer.swing_ratio)
        profile = ShopProfile(
            stage=stage, stage_label=G.STAGE_LABEL[stage],
            stage_text=G.stage_text(stage, monthly_trend=monthly_trend, swing_pct=swing_pct,
                                    lowest=forecast.lowest_balance_paisa, buffer=forecast.buffer_paisa,
                                    lowest_day=forecast.lowest_day),
            steadiness=buffer.steadiness, daily_swing_pct=swing_pct, trend=G.trend_word(monthly_trend),
            monthly_trend_pct=round(monthly_trend * 100, 1), profit_swing_pct=round(band / middle * 100),
            reinvest_pct=G.REINVEST_BY_STAGE[stage])

        # Three paths. A shop that should not reinvest yet still sees the gentle path, labelled as later.
        pct = profile.reinvest_pct or 10
        later = "" if profile.reinvest_pct else " once the cushion holds and sales steady"
        overdue = [b for b in self.ledger.baki_by_customer(self.shop_id, today)
                   if b.outstanding_paisa > 0 and (b.days_outstanding or 0) >= G.OVERDUE_DAYS]
        overdue_paisa = sum(b.outstanding_paisa for b in overdue)
        share = self.savings.share() or DEFAULT_SHARE_PCT

        def path(key, label, assumption, reinvest, capital) -> Scenario:
            months_ahead = G.project(base=base, monthly_trend=monthly_trend, margin=margin, reinvest_pct=reinvest,
                                     start_capital=capital, months=HORIZON_MONTHS, band=band, first=this_month,
                                     seasonal=seasonal, seasons=seasons)
            return Scenario(key=key, label=label, assumption=assumption, months=months_ahead,
                            total_profit_paisa=sum(m.profit_paisa for m in months_ahead),
                            savings_paisa=G.savings_on_the_way(share, last.profit, months_ahead),
                            extra_vs_now_paisa=0)

        trend_text = f"{monthly_trend * 100:+.1f}% a month"
        as_now = path("as_now", "Keep going as now",
                      f"Profit starts from an ordinary month ({fmt_taka(G.whole(base))}, the average of the last "
                      f"{len(months)} with Ramadan and Eid taken out), follows half your sales trend ({trend_text}), "
                      "and adds the seasons back in.", 0, 0)
        reinvest = path("reinvest", f"Put {pct}% of profit back into stock{later}",
                        f"As now, plus {pct}% of each month's profit put back into stock, earning your "
                        f"{margin:.0%} margin on the half that becomes new sales.", pct, 0)
        collected = int(overdue_paisa * G.COLLECT_SHARE)
        collect = path("collect_and_reinvest", "Collect overdue baki and put it into stock too",
                       f"As the stock path, starting with half of the {fmt_taka(G.whole(overdue_paisa))} owed for "
                       f"{G.OVERDUE_DAYS} days or more ({fmt_taka(G.whole(collected))}) collected into stock.",
                       pct, collected)
        for s in (reinvest, collect):
            s.extra_vs_now_paisa = s.total_profit_paisa - as_now.total_profit_paisa

        levers: list[Lever] = []
        if stage == "cushion_first":
            levers.append(Lever(kind="cushion", title="Build the cushion first", text=profile.stage_text,
                                cash_paisa=0, monthly_paisa=0, tab="forecast"))
        for lever in (G.household_lever(recent), G.baki_lever(overdue_paisa, len(overdue), margin),
                      G.margin_lever(recent, earlier)):
            if lever:
                levers.append(lever)
        cycles = self.ledger.supplier_credit_cycles(self.shop_id, today)
        if cycles:
            biggest = max(cycles, key=lambda c: c.usual_paisa / max(1, c.cycle_days))
            if lever := G.supplier_lever(biggest.name, biggest.usual_paisa, biggest.cycle_days, biggest.terms_days,
                                         margin):
                levers.append(lever)
        levers += G.cost_levers(recent, earlier)
        if lever := G.reinvest_lever(profile.reinvest_pct, margin, reinvest.extra_vs_now_paisa,
                                     reinvest.months[-1].profit_paisa - as_now.months[-1].profit_paisa):
            levers.append(lever)
        levers = G.rank(levers, margin)

        money_in, costs, uses = G.flow_parts(last)
        label = f"{last.month:%B}"
        flow = MoneyFlow(month=last.month, label=label, money_in=money_in, costs=costs, profit_paisa=last.profit,
                         uses=uses, gross_margin_pct=round(margin * 100, 1), headline=G.flow_headline(last, label))
        growth_moves = [lv for lv in levers if lv.kind != "cushion"]
        then = "Once it holds" if stage == "cushion_first" else "First move"
        headline = f"{profile.stage_label}. " + (f"{then}: {growth_moves[0].title}." if growth_moves else
                                                 "Keep doing what you are doing.")
        return GrowthPlan(as_of=now, headline=headline, flow=flow, profile=profile, base_profit_paisa=G.whole(base),
                          scenarios=[as_now, reinvest, collect], levers=levers,
                          method=self._method(len(months), margin, share))

    @staticmethod
    def _method(months: int, margin: float, share: int) -> list[str]:
        return [
            f"Everything comes from your own books: the last {months} finished months.",
            "Profit is cash profit, the same as your savings plan uses: sales in cash and mobile money plus baki "
            "coming back, minus stock paid for and running costs.",
            f"Margin ({margin:.0%}) is 1 minus stock bought over goods sold in the last three months, baki sales and "
            "credit purchases included.",
            "The trend is a straight line through six months of sales, capped at 3% a month either way, with "
            "Ramadan and Eid taken out first. They are added back into the months ahead with the same stated "
            "multipliers the cash forecast uses (busier Ramadan and pre-Eid weeks, closed Eid days).",
            "What kind of shop it is comes from how far a usual day swings around normal (the same measure as your "
            "safety buffer) and from that trend. Steadier shops can put more of their profit back into stock.",
            "Extra stock earns your margin on the half of it that becomes new sales each month: a deliberately "
            "modest rule. The shaded band is an 80% range from how much your monthly profit has moved.",
            "Backtested on 400 synthetic shops (docs/results/growth_backtest.md): the as-now path's monthly profit "
            "was closer than \"the last three months' average\" more often than not, and the 80% band held about "
            "80% of the time. The stock and baki paths cannot be backtested: they need an owner who changes course.",
            f"Savings on each path are {share}% of the month before's profit, as your savings plan works.",
            "These are estimates to plan with, not promises. KhataPilot suggests; nothing here moves money.",
        ]
