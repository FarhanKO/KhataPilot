"""The growth arithmetic: money flow, what kind of shop it is, the paths ahead and the moves."""

from datetime import date

from app.core.money import taka
from app.modules.growth import plan as G


def month(m: int, *, sales=300_000, baki_back=40_000, baki_sales=50_000, stock_cash=100_000, supplier=200_000,
          purchases=320_000, rent=20_000, wages=9_000, drawings=20_000, savings=0) -> G.MonthBooks:
    rows = [("sale", "cash", "Sales", taka(sales)), ("baki_repayment", "cash", "Baki collected", taka(baki_back)),
            ("baki_sale", "credit", "Sales on credit (baki)", taka(baki_sales)),
            ("purchase", "cash", "Rice", taka(stock_cash)), ("purchase", "credit", "Rice", taka(purchases - stock_cash)),
            ("supplier_payment", "bank", "Rice", taka(supplier)), ("expense", "bkash", "Rent", taka(rent)),
            ("expense", "cash", "Wages", taka(wages)), ("owner_drawing", "cash", "Owner withdrawal", taka(drawings)),
            ("owner_drawing", "bkash", "Personal savings", taka(savings)), ("transfer", "cash", "Transfer", taka(9_999))]
    return G.MonthBooks.from_rows(date(2026, m, 1), [r for r in rows if r[3]])


def test_month_books_match_the_savings_cash_profit():
    books = month(9)
    assert books.money_in == taka(340_000) and books.stock_out == taka(300_000)
    assert books.running_costs == taka(29_000) and books.profit == taka(11_000)
    assert books.drawings == taka(20_000) and books.goods_sold == taka(350_000)
    assert G.gross_margin([books]) == 1 - 320_000 / 350_000


def test_money_flow_says_when_the_household_takes_more_than_the_shop_makes():
    books = month(9)
    money_in, costs, uses = G.flow_parts(books)
    assert [p.key for p in costs] == ["stock", "cost:Rent", "cost:Wages"]
    assert uses[-1].label == "Taken from the shop's cash" and uses[-1].amount_paisa == -taka(9_000)
    text = G.flow_headline(books, "September")
    assert text.startswith("Of every ৳100 that came in during September, ৳88 went on stock")
    assert "৳9,000 more than the shop made" in text


def test_trend_is_a_capped_straight_line():
    assert round(G.trend([100, 102, 104, 106, 108, 110]), 3) == 0.019
    assert G.trend([100, 200, 300, 400, 500, 600]) == G.TREND_CAP  # one boom does not promise the moon
    assert G.trend([100, 100, 100]) == 0 and G.trend([]) == 0


def test_what_kind_of_shop_decides_how_much_to_put_back():
    assert G.stage_of(steadiness="steady", monthly_trend=0.02, cushion_ok=True) == "ready_to_grow"
    assert G.stage_of(steadiness="steady", monthly_trend=0.0, cushion_ok=True) == "steady"
    assert G.stage_of(steadiness="moderate", monthly_trend=0.02, cushion_ok=True) == "up_and_down"
    assert G.stage_of(steadiness="choppy", monthly_trend=0.02, cushion_ok=True) == "choppy"
    assert G.stage_of(steadiness="steady", monthly_trend=-0.02, cushion_ok=True) == "shrinking"
    assert G.stage_of(steadiness="steady", monthly_trend=0.02, cushion_ok=False) == "cushion_first"
    shares = [G.REINVEST_BY_STAGE[s] for s in ("ready_to_grow", "steady", "up_and_down", "choppy", "shrinking")]
    assert shares == sorted(shares, reverse=True) and shares[-1] == 0


def test_how_much_to_put_back_is_sized_from_what_a_usual_month_leaves():
    # ৳40,000 profit, ৳20,000 to the household, 10% (৳4,000) to savings: ৳16,000 left, 40% of profit.
    room = G.ReinvestRoom(profit=taka(40_000), household=taka(20_000), savings=taka(4_000))
    pct, why = G.suggest_reinvest(room, "steady")
    assert pct == 20 and "at most" not in why and "(steady), 20% is the most" in why  # capped by the kind of shop
    pct, why = G.suggest_reinvest(G.ReinvestRoom(taka(40_000), taka(32_000), taka(4_000)), "steady")
    assert pct == 10 and "without taking from the household or your savings" in why  # ৳4,000 left: 10%
    pct, why = G.suggest_reinvest(G.ReinvestRoom(taka(23_000), taka(24_500), 0), "up_and_down")
    assert pct == 0 and "nothing is left over" in why and "Taking out less than the shop makes" in why
    assert G.suggest_reinvest(G.ReinvestRoom(0, taka(5_000), 0), "steady")[0] == 0


def test_the_room_changes_with_the_books_and_the_savings_plan():
    lean, good = month(7, drawings=20_000), month(8, sales=330_000, drawings=10_000)
    base = G.reinvest_room([lean, good], savings_share_pct=None)
    saving = G.reinvest_room([lean, good], savings_share_pct=20)
    assert saving.savings == int(base.profit * 0.2) and saving.left < base.left
    assert G.reinvest_room([good], None).left > G.reinvest_room([lean], None).left


def test_a_shop_waiting_for_its_cushion_is_sized_as_it_will_be():
    assert G.ceiling_stage("cushion_first", steadiness="steady", monthly_trend=0.02) == "ready_to_grow"
    assert G.ceiling_stage("cushion_first", steadiness="steady", monthly_trend=-0.02) == "choppy"
    assert G.ceiling_stage("up_and_down", steadiness="moderate", monthly_trend=0.0) == "up_and_down"


def test_moves_that_bring_cash_in_start_now_even_before_the_cushion_holds():
    baki = G.baki_lever(taka(41_800), 13, 0.17)
    stock = G.reinvest_lever(15, 0.17, taka(12_000), taka(1_500))
    assert G.lead_in("cushion_first", baki) == "Start now"
    assert G.lead_in("cushion_first", stock) == "Once it holds"
    assert G.lead_in("steady", stock) == "First move"
    assert "the share you chose" in G.reinvest_lever(15, 0.17, taka(12_000), taka(1_500), chosen_by_owner=True).text


def test_putting_profit_back_into_stock_compounds_modestly():
    common = dict(base=taka(30_000), monthly_trend=0.0, margin=0.2, months=12, band=taka(5_000), first=date(2026, 10, 1))
    flat = G.project(reinvest_pct=0, start_capital=0, **common)
    back = G.project(reinvest_pct=20, start_capital=0, **common)
    seeded = G.project(reinvest_pct=20, start_capital=taka(20_000), **common)
    assert {m.profit_paisa for m in flat} == {taka(30_000)}
    assert back[0].profit_paisa == taka(30_000)  # nothing is back in stock yet in the first month
    assert back[1].profit_paisa == taka(30_600)  # ৳6,000 of stock x 20% margin x half sold
    assert back[-1].profit_paisa > back[1].profit_paisa and seeded[0].profit_paisa == taka(32_000)
    assert flat[0].low_paisa < flat[0].profit_paisa < flat[0].high_paisa
    assert [m.label for m in flat[:4]] == ["Oct ’26", "Nov ’26", "Dec ’26", "Jan ’27"]
    assert G.savings_on_the_way(10, taka(30_000), flat) == taka(36_000)


def test_moves_are_found_from_the_books_and_ranked():
    recent = [month(m, drawings=40_000, rent=26_000) for m in (7, 8, 9)]
    earlier = [month(m, rent=20_000) for m in (4, 5, 6)]
    household = G.household_lever(recent)
    assert household and household.monthly_paisa == taka(35_000)  # ৳40,000 out against ৳5,000 profit
    rent = G.cost_levers(recent, earlier)
    assert [lv.title for lv in rent] == ["Rent is up 30%"] and rent[0].monthly_paisa == taka(6_000)
    baki = G.baki_lever(taka(41_800), 16, 0.2)
    assert baki and baki.cash_paisa == taka(41_800) and baki.monthly_paisa == taka(4_200)
    assert G.baki_lever(taka(300), 1, 0.2) is None
    supplier = G.supplier_lever("Rahim Traders", taka(37_700), 7, 7, 0.2)
    assert supplier and supplier.cash_paisa == taka(37_700)
    ranked = G.rank([*rent, baki, supplier, household], 0.2)
    assert ranked[0].kind == "household"  # a leak comes before growth
    assert [lv.kind for lv in ranked[1:]] == ["collect_baki", "supplier_terms", "rising_cost"]


def test_a_slipping_margin_is_a_move():
    recent = [month(m, purchases=330_000) for m in (7, 8, 9)]
    earlier = [month(m, purchases=300_000) for m in (4, 5, 6)]
    lever = G.margin_lever(recent, earlier)
    assert lever and lever.title == "Your margin slipped from 14% to 6%"
    assert G.margin_lever(earlier, earlier) is None


def test_ramadan_and_eid_are_taken_out_of_history_and_put_back_ahead():
    assert G.month_factor("grocery", date(2026, 10, 1)) == 1.0
    assert G.month_factor("grocery", date(2027, 2, 1)) > 1.0  # Ramadan starts 8 Feb 2027
    assert G.season_of("grocery", date(2027, 3, 1)) == "Ramadan and Eid"
    assert G.season_of("grocery", date(2026, 10, 1)) is None
    plain = [month(m) for m in (7, 8, 9)]
    # A season month sold 20% more (৳70,000), and the stock for it cost 80% of that at a 20% margin.
    busy = plain[:2] + [month(9, sales=370_000, supplier=256_000)]
    usual = G.everyday(busy, [1.0, 1.0, 1.2], 0.2)
    assert busy[2].profit - plain[2].profit == taka(14_000)
    assert usual.base == G.everyday(plain, [1.0, 1.0, 1.0], 0.2).base  # the season's extra is taken out
    extras = G.seasonal_extras("grocery", date(2026, 10, 1), 12, usual, 0.2)
    assert extras[0] == 0 and extras[4] > 0  # Oct ordinary, Feb 2027 busier
