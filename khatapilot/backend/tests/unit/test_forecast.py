"""The cash forecast on a small shop whose numbers can be checked by hand.

The shop sells ৳10,000 a day and spends ৳9,000 a day on stock paid on delivery, so it gains ৳1,000 a
day. It starts today (Mon 12 Oct 2026) with ৳20,000 on hand and keeps a ৳5,000 safety buffer.
"""

from datetime import date, datetime, time, timedelta

import pytest

from app.bootstrap import register_models
from app.core.clock import BDT
from app.infra.db import Database
from app.modules.forecast.calendar import sales_factor
from app.modules.forecast.service import ForecastService, build_baseline
from app.modules.ledger.schemas import DailyFlow, NewLedgerEntry, NewObligation
from app.modules.ledger.service import LedgerService
from app.modules.payments.service import PaymentsService
from app.modules.shop.schemas import ShopProfile
from app.modules.shop.service import ShopService

SHOP = "test-shop"
TODAY = date(2026, 10, 12)
SINCE = TODAY - timedelta(days=56)
BUFFER = 5_000_00


def at(day: date, hour: int = 12) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=BDT)


def entry(day: date, kind: str, direction: str, amount_taka: int, method: str = "cash", category: str = "Sales",
          **extra) -> NewLedgerEntry:
    return NewLedgerEntry(shop_id=SHOP, occurred_at=at(day, extra.pop("hour", 12)), kind=kind, direction=direction,
                          method=method, amount_paisa=amount_taka * 100, category=category, source="seed", **extra)


@pytest.fixture
def db(tmp_path, clock):
    register_models()
    database = Database(f"sqlite:///{(tmp_path / 'forecast.db').as_posix()}")
    database.create_all()
    with database.session() as session:
        ShopService(session, clock).create_shop(ShopProfile(
            id=SHOP, name="Test Store", owner_name="Test Owner", shop_type="grocery", area="Mohakhali",
            district="Dhaka", division="Dhaka", urbanicity="urban", owner_phone="01711000111",
            bkash_account="01711000222", nagad_account="01811000222", bank_account="1701500012345",
            data_since=SINCE))
        ledger = LedgerService(session, clock)
        ledger.add_entry(entry(SINCE, "opening_balance", "in", 20_000, category="Opening balance", hour=7))
        for i in range(56):
            day = SINCE + timedelta(days=i)
            ledger.add_entry(entry(day, "sale", "in", 10_000))
            ledger.add_entry(entry(day, "purchase", "out", 9_000, category="Stock"))
        ledger.add_entry(entry(SINCE, "opening_balance", "out", 56_000, category="Opening balance", hour=8))
    yield database
    database.dispose()


def run(db, clock, days: int = 14):
    with db.session() as session:
        return ForecastService(session, clock, SHOP, buffer_paisa=BUFFER).forecast(days)


def add(db, clock, *entries: NewLedgerEntry) -> None:
    with db.session() as session:
        ledger = LedgerService(session, clock)
        for e in entries:
            ledger.add_entry(e)


def owe_supplier(db, clock, name: str, amount_taka: int, due: date) -> None:
    with db.session() as session:
        LedgerService(session, clock).add_obligation(NewObligation(
            shop_id=SHOP, kind="supplier_payable", counterparty_name=name, category="Stock",
            amount_paisa=amount_taka * 100, due_date=due))


def balance_on(result, day: date) -> int:
    return next(d.balance_paisa for d in result.days if d.day == day)


# ---------------------------------------------------------------- the arithmetic

def test_a_steady_shop_with_nothing_due_has_enough_cash(db, clock):
    result = run(db, clock)
    assert result.start_balance_paisa == 20_000_00
    assert [d.balance_paisa for d in result.days] == [(20_000 + 1_000 * (k + 1)) * 100 for k in range(14)]
    assert result.first_shortfall is None and result.suggestions == []
    assert result.headline.startswith("You have enough cash for the next 14 days")


def test_a_big_supplier_bill_shows_up_as_a_shortfall_on_its_due_day(db, clock):
    owe_supplier(db, clock, "Meghna Distributors", 30_000, date(2026, 10, 20))
    result = run(db, clock)
    # Tue 20 Oct is day 8: 20,000 + 9 x 1,000 - 30,000 = -1,000, which is 6,000 below the buffer.
    assert result.first_shortfall.day == date(2026, 10, 20)
    assert result.first_shortfall.balance_paisa == -1_000_00
    assert result.first_shortfall.short_by_paisa == 6_000_00
    assert result.first_shortfall.cause == "when Meghna Distributors' ৳30,000 bill falls due"
    assert result.headline.startswith("You could run out of cash on Tuesday 20 Oct")
    item = result.days[8].items[0]
    assert (item.kind, item.amount_paisa, item.certain) == ("supplier_bill", 30_000_00, True)


def test_an_overdue_bill_is_counted_today(db, clock):
    owe_supplier(db, clock, "Rahim Traders", 4_000, date(2026, 10, 9))
    result = run(db, clock)
    assert result.days[0].items[0].overdue
    assert result.days[0].balance_paisa == (20_000 + 1_000 - 4_000) * 100


def test_what_already_happened_today_is_not_counted_twice(db, clock):
    add(db, clock, entry(TODAY, "sale", "in", 6_000, hour=9))  # before 10:00, the clock's "now"
    result = run(db, clock)
    assert result.start_balance_paisa == 26_000_00
    assert result.days[0].balance_paisa == 26_000_00 + (10_000 - 6_000 - 9_000) * 100


# ---------------------------------------------------------------- known amounts

def test_a_scheduled_payment_settles_the_bill_it_pays(db, clock):
    owe_supplier(db, clock, "Meghna Distributors", 30_000, date(2026, 10, 20))
    with db.session() as session:
        PaymentsService(session, clock, SHOP).create(
            payee_name="Meghna Distributors", payee_number="2051334007812", method="bank", amount_paisa=30_000_00,
            execute_on=date(2026, 10, 19), decision_id="dec_1", request_id="req_1", tier=2, with_pin=True)
    result = run(db, clock)
    items = [i for d in result.days for i in d.items]
    assert [(i.kind, i.day, i.amount_paisa) for i in items] == [
        ("scheduled_payment", date(2026, 10, 19), 30_000_00)]
    assert result.first_shortfall.day == date(2026, 10, 19)


def test_a_recurring_bill_is_expected_unless_already_paid_this_month(db, clock):
    with db.session() as session:
        LedgerService(session, clock).add_recurring_bill(SHOP, "Shop rent", "Landlord", "Rent", 5_000_00, 15, "bank")
    bills = [i for d in run(db, clock).days for i in d.items]
    assert [(i.day, i.amount_paisa) for i in bills] == [(date(2026, 10, 15), 5_000_00)]

    add(db, clock, entry(date(2026, 10, 5), "expense", "out", 5_000, method="bank", category="Rent"))
    assert [i for d in run(db, clock).days for i in d.items] == []


def test_a_regular_credit_supplier_s_next_bill_is_projected(db, clock):
    # Rahim delivers every Saturday on 7-day credit; the last delivery (Sat 10 Oct) is still unpaid.
    saturdays = [date(2026, 10, 10) - timedelta(weeks=w) for w in range(6, -1, -1)]
    for day in saturdays:
        add(db, clock, entry(day, "purchase", "out", 20_000, method="credit", category="Rice",
                             counterparty_name="Rahim Traders", due_date=day + timedelta(days=7)))
        if day < date(2026, 10, 10):
            add(db, clock, entry(day + timedelta(days=7), "supplier_payment", "out", 20_000, method="bkash",
                                 category="Rice", counterparty_name="Rahim Traders"))
    owe_supplier(db, clock, "Rahim Traders", 20_000, date(2026, 10, 17))
    result = run(db, clock)
    items = [(i.kind, i.day, i.amount_paisa, i.certain) for d in result.days for i in d.items]
    assert items == [("supplier_bill", date(2026, 10, 17), 20_000_00, True),
                     ("expected_supplier_bill", date(2026, 10, 24), 20_000_00, False)]
    assert any("Rahim Traders" in line for line in result.method)
    # Paying Rahim in the past is not "everyday spending": the daily baseline is unchanged.
    assert result.days[1].expected_out_paisa == 9_000_00


# ---------------------------------------------------------------- baki and suggestions

def test_long_overdue_baki_is_not_counted_on_but_is_suggested(db, clock):
    add(db, clock, entry(date(2026, 8, 20), "baki_sale", "in", 7_000, method="credit",
                         counterparty_name="Babul Mia", counterparty_number="01933445566"))
    owe_supplier(db, clock, "Meghna Distributors", 30_000, date(2026, 10, 20))
    result = run(db, clock)
    assert any(line.startswith("Not counted: ৳7,000 of baki owed for over 45 days (Babul Mia)")
               for line in result.method)
    collect = next(s for s in result.suggestions if s.kind == "collect_baki")
    assert "Babul Mia ৳7,000 (53 days)" in collect.text
    assert collect.new_lowest_paisa == result.lowest_balance_paisa + 7_000_00
    assert collect.closes_gap  # -1,000 + 7,000 = 6,000, above the buffer


def test_moving_a_bill_is_simulated_and_never_overstated(db, clock):
    owe_supplier(db, clock, "Meghna Distributors", 30_000, date(2026, 10, 20))
    short = run(db, clock, days=14)
    move = next(s for s in short.suggestions if s.kind == "move_payment")
    assert "Ask Meghna Distributors to take their ৳30,000 on Tue 27 Oct instead of Tue 20 Oct" in move.text
    # Tue 27 is past a 14-day window, so it is counted on its last day (Sun 25): 34,000 - 30,000.
    assert move.new_lowest_paisa == 4_000_00 and not move.closes_gap

    longer = run(db, clock, days=21)
    move = next(s for s in longer.suggestions if s.kind == "move_payment")
    assert move.new_lowest_paisa == 6_000_00 and move.closes_gap  # 36,000 - 30,000 on Tue 27


def test_the_loan_suggestion_covers_the_gap_in_round_amounts(db, clock):
    owe_supplier(db, clock, "Meghna Distributors", 30_000, date(2026, 10, 20))
    loan = next(s for s in run(db, clock).suggestions if s.kind == "loan")
    assert loan.amount_paisa == 10_000_00  # lowest point -1,000 is 6,000 below the buffer: rounded up to 5,000s
    assert "can I get a loan?" in loan.text


# ---------------------------------------------------------------- the baseline itself

def test_a_one_off_withdrawal_does_not_make_every_week_expensive():
    flows = {}
    for i in range(56):
        day = TODAY - timedelta(days=56 - i)
        drawing = 0
        if day.weekday() == 3:  # Thursdays
            drawing = 28_000_00 if day == date(2026, 10, 8) else 1_000_00
        flows[day] = DailyFlow(sales_in=10_000_00, owner_drawings=drawing)
    baseline = build_baseline(flows, "grocery", TODAY)
    assert baseline.drawings[3] == pytest.approx((7 * 1_000_00 + 3_000_00) / 8)


def test_the_weekday_median_ignores_one_unusual_day():
    flows = {TODAY - timedelta(days=d): DailyFlow(sales_in=10_000_00) for d in range(1, 57)}
    flows[date(2026, 10, 5)] = DailyFlow(sales_in=90_000_00)  # a Monday with a wholesale order
    baseline = build_baseline(flows, "grocery", TODAY)
    assert baseline.sales_in[0] == 10_000_00


def test_eid_and_ramadan_change_expected_sales():
    assert sales_factor("grocery", date(2027, 3, 10)) == (0.2, "Eid (most shops close)")
    assert sales_factor("grocery", date(2027, 3, 5))[0] == 1.3
    assert sales_factor("tea_stall", date(2027, 2, 20)) == (0.6, "Ramadan")
    assert sales_factor("grocery", TODAY) == (1.0, None)


def test_history_is_divided_by_the_season_so_eid_does_not_drag_the_baseline_down():
    eid = date(2026, 5, 27)
    flows = {eid - timedelta(days=d): DailyFlow(sales_in=10_000_00) for d in range(-20, 36)}
    flows[eid] = DailyFlow(sales_in=2_000_00)  # most shops close on Eid: 0.2 of a normal day
    baseline = build_baseline(flows, "grocery", eid + timedelta(days=21))
    assert baseline.sales_in[eid.weekday()] == pytest.approx(10_000_00)
