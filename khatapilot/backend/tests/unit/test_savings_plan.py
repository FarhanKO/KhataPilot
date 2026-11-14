"""The monthly savings arithmetic: a share of last month's profit, capped by the cash forecast."""

from datetime import date

from app.core.money import taka
from app.modules.ledger.schemas import DailyFlow
from app.modules.savings import plan as P


def decide(**overrides):
    args = dict(share_pct=10, has_account=True, last_profit=taka(23_053), saved=0, pending=0, safe_now=taka(50_000))
    return P.decide(**{**args, **overrides})


def test_cash_profit_counts_sales_and_baki_back_minus_costs_but_not_withdrawals_or_savings():
    flows = {
        date(2026, 9, 1): DailyFlow(sales_in=taka(5_000), baki_collected=taka(500), routine_out=taka(3_000),
                                    owner_drawings=taka(800), savings=taka(2_000)),
        date(2026, 9, 2): DailyFlow(sales_in=taka(4_000), supplier_payments=taka(1_500), bills_paid=taka(1_000),
                                    baki_given=taka(700)),
    }
    assert P.cash_profit(flows) == taka(5_000 + 500 - 3_000 + 4_000 - 1_500 - 1_000)


def test_the_amount_scales_with_last_months_profit():
    assert decide(last_profit=taka(23_053))[:4] == ("ready", taka(2_300), taka(2_300), taka(2_300))
    assert decide(last_profit=taka(46_000))[1] == taka(4_600)  # a better month saves more
    assert decide(last_profit=taka(9_999))[1] == taka(900)  # a lean one less, in whole hundreds
    assert decide(last_profit=-taka(12_339))[0] == "no_profit"  # a loss saves nothing


def test_a_tight_month_moves_less_or_waits():
    state, target, remaining, suggested = decide(safe_now=taka(1_250))
    assert (state, target, remaining, suggested) == ("partial", taka(2_300), taka(2_300), taka(1_200))
    assert decide(safe_now=0)[0] == "wait"
    assert decide(safe_now=-taka(7_000))[3] == 0


def test_what_was_moved_or_is_on_its_way_counts():
    assert decide(saved=taka(1_000))[2:] == (taka(1_300), taka(1_300))
    assert decide(saved=taka(1_000), pending=taka(1_300))[0] == "done"
    assert decide(saved=taka(2_250))[0] == "done"  # less than ৳100 left is done


def test_no_plan_and_no_account():
    assert decide(share_pct=None)[0] == "no_plan"
    assert decide(has_account=False)[0] == "no_account"


def test_scale_up_only_after_three_months_of_growth_and_only_if_it_fits():
    growing = [taka(20_000), taka(21_000), taka(19_000), taka(24_000), taka(25_000), taka(26_000)]
    up = P.scale_up(share_pct=10, profits=growing, safe_now=taka(50_000), saved=0, pending=0)
    assert up is not None and (up.from_pct, up.to_pct, up.new_target_paisa) == (10, 15, taka(3_900))
    assert up.growth_pct == 25
    flat = [taka(20_000)] * 6
    assert P.scale_up(share_pct=10, profits=flat, safe_now=taka(50_000), saved=0, pending=0) is None
    assert P.scale_up(share_pct=10, profits=growing, safe_now=taka(3_000), saved=0, pending=0) is None  # won't fit
    assert P.scale_up(share_pct=30, profits=growing, safe_now=taka(50_000), saved=0, pending=0) is None  # the cap
    dip = growing[:5] + [-taka(1_000)]
    assert P.scale_up(share_pct=10, profits=dip, safe_now=taka(50_000), saved=0, pending=0) is None


def test_months():
    assert P.previous_month(date(2026, 1, 15)) == date(2025, 12, 1)
    assert P.month_end(date(2028, 2, 1)) == date(2028, 2, 29)
    assert P.month_label(date(2026, 9, 1)) == "Sep 2026"


def test_a_steadier_basis_uses_the_middle_of_three_months():
    profits = [taka(26_917), -taka(12_339), taka(22_039), taka(33_172), taka(37_816), taka(23_053)]
    assert P.basis_profit("last_month", profits) == taka(23_053)
    assert P.basis_profit("three_months", profits) == taka(33_172)  # one weak September moves it less
    text, nothing = P.basis_words("three_months", date(2026, 9, 1), taka(33_172))
    assert text == "a usual month's ৳33,172 profit (the middle of Jul–Sep)"
    assert P.basis_words("last_month", date(2026, 9, 1), taka(23_053))[0] == "September's ৳23,053 profit"


# ---------------------------------------------------------------- goals

MONTHS = [{"costs": taka(40_000), "household": taka(22_000), "stock": taka(370_000)},
          {"costs": taka(39_000), "household": taka(23_000), "stock": taka(380_000)},
          {"costs": taka(41_000), "household": taka(22_500), "stock": taka(520_000)}]  # a busy month


def test_savings_fill_goals_in_order_and_say_how_long_the_rest_takes():
    goals = [("Eid stock", taka(9_000), date(2027, 1, 1)), ("Cushion", taka(14_000), None),
             ("Fridge", taka(35_000), date(2027, 3, 1))]
    first, second, third = P.fill_goals(goals, saved_total=taka(11_000), pace=taka(2_300), this_month=date(2026, 10, 1))
    assert (first["saved"], first["reached"], first["months_left"]) == (taka(9_000), True, None)
    assert (second["saved"], second["remaining"], second["months_left"]) == (taka(2_000), taka(12_000), 6)
    # the fridge waits for the cushion: 12,000 + 35,000 at 2,300 a month is 21 months, past March
    assert third["months_left"] == 21 and third["on_track"] is False and "later than you wanted" in third["text"]
    stalled = P.fill_goals(goals[:1], saved_total=0, pace=0, this_month=date(2026, 10, 1))[0]
    assert stalled["months_left"] is None and "no date yet" in stalled["text"]


def test_starter_goals_are_sized_to_what_the_owner_saves_and_the_dated_one_comes_first():
    goals = P.starter_goals(MONTHS, stock_up=("Eid-ul-Fitr", date(2027, 2, 8)), pace=taka(2_300),
                            this_month=date(2026, 10, 1))
    assert [g.kind for g in goals] == ["eid_stock", "cushion", "grow_shop"]
    eid, cushion, bulk = goals
    assert eid.by_month == date(2027, 1, 1) and eid.target_paisa == taka(9_000)  # four months of 2,300, rounded down
    assert cushion.target_paisa == taka(14_000) and cushion.why.startswith("About 1 week")  # six months of saving
    assert bulk.target_paisa == taka(28_000)  # about a year of saving
    assert all(g.target_paisa % taka(1_000) == 0 for g in goals)


def test_with_nothing_saved_yet_starter_goals_come_from_the_shops_costs():
    eid, cushion, bulk = P.starter_goals(MONTHS, stock_up=("Eid-ul-Fitr", date(2027, 2, 8)), pace=0,
                                         this_month=date(2026, 10, 1))
    assert cushion.target_paisa == taka(63_000) and cushion.why.startswith("One month")  # 40,000 + 22,500, rounded up
    assert eid.target_paisa == taka(140_000)  # the busy month's extra stock over a usual month
    assert bulk.target_paisa == taka(190_000)
    assert P.starter_goals([], stock_up=None, pace=0, this_month=date(2026, 10, 1)) == []


def test_a_jump_up_in_profit_is_capped_at_one_and_a_half_usual_months():
    from app.modules.savings.plan import steady_base

    base, jump = steady_base([20_000_00, 22_000_00, 18_000_00, 60_000_00])  # usual 20,000, then 60,000
    assert base == 30_000_00 and jump.direction == "up" and jump.usual_profit_paisa == 20_000_00
    assert "far above a usual month" in jump.text


def test_a_drop_is_followed_and_explained_and_ordinary_months_are_left_alone():
    from app.modules.savings.plan import basis_profit, steady_base

    base, jump = steady_base([20_000_00, 22_000_00, 18_000_00, 8_000_00])
    assert base == 8_000_00 and jump.direction == "down" and "usual month instead" in jump.text
    assert steady_base([20_000_00, 22_000_00, 18_000_00, 25_000_00]) == (25_000_00, None)
    assert steady_base([22_000_00, 60_000_00]) == (60_000_00, None), "too little history to know what is usual"
    assert basis_profit("three_months", [20_000_00, 22_000_00, 18_000_00, 60_000_00]) == 22_000_00
