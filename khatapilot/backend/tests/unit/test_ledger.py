from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.bootstrap import register_models
from app.infra.db import Database
from app.modules.ledger.schemas import NewLedgerEntry
from app.modules.ledger.service import LedgerService
from tests.conftest import DEMO_NOW

SHOP = "test-shop"


@pytest.fixture
def db(tmp_path):
    register_models()
    database = Database(f"sqlite:///{(tmp_path / 'ledger.db').as_posix()}")
    database.create_all()
    yield database
    database.dispose()


def entry(kind="sale", direction="in", method="cash", amount=150_000, at=DEMO_NOW, **extra) -> NewLedgerEntry:
    return NewLedgerEntry(shop_id=SHOP, occurred_at=at, kind=kind, direction=direction, method=method,
                          amount_paisa=amount, category=extra.pop("category", "Sales"), source="owner_text", **extra)


def test_entry_gets_its_bangladesh_calendar_day(db, clock):
    late_night_utc = datetime(2026, 10, 12, 19, 30, tzinfo=UTC)  # 01:30 on the 13th in Dhaka
    with db.session() as session:
        view = LedgerService(session, clock).add_entry(entry(at=late_night_utc))
    assert view.local_date == date(2026, 10, 13)


def test_day_totals_separate_sales_credit_and_cash(db, clock):
    with db.session() as session:
        ledger = LedgerService(session, clock)
        for item in [
            entry(amount=150_000),
            entry(kind="baki_sale", method="credit", amount=50_000, counterparty_name="Jamal"),
            entry(kind="baki_repayment", method="bkash", amount=30_000, counterparty_name="Jamal"),
            entry(kind="purchase", direction="out", method="credit", amount=200_000, category="Rice"),
            entry(kind="expense", direction="out", amount=20_000, category="Transport & misc"),
            entry(kind="transfer", direction="out", amount=10_000, category="Transfer"),
            entry(kind="transfer", direction="in", method="bank", amount=10_000, category="Transfer"),
        ]:
            ledger.add_entry(item)
        totals = ledger.day_totals(SHOP, date(2026, 10, 12))
    assert totals.sales_paisa == 150_000
    assert totals.baki_given_paisa == 50_000
    assert totals.baki_collected_paisa == 30_000
    assert totals.money_in_paisa == 180_000
    assert totals.money_out_paisa == 20_000
    assert totals.net_paisa == 160_000
    assert totals.entries == 7


def test_balances_by_method_ignore_credit(db, clock):
    with db.session() as session:
        ledger = LedgerService(session, clock)
        ledger.add_entry(entry(kind="opening_balance", amount=100_000, category="Opening balance"))
        ledger.add_entry(entry(method="bkash", amount=50_000))
        ledger.add_entry(entry(kind="transfer", direction="out", amount=30_000, category="Transfer"))
        ledger.add_entry(entry(kind="transfer", direction="in", method="bank", amount=30_000, category="Transfer"))
        ledger.add_entry(entry(kind="purchase", direction="out", method="credit", amount=900_000, category="Rice"))
        assert ledger.balances(SHOP) == {"cash": 70_000, "bkash": 50_000, "nagad": 0, "bank": 30_000}


def test_baki_is_repaid_oldest_first(db, clock):
    day = timedelta(days=1)
    with db.session() as session:
        ledger = LedgerService(session, clock)
        ledger.add_entry(entry(kind="baki_sale", method="credit", amount=50_000, counterparty_name="Jamal Uddin",
                               at=DEMO_NOW - 10 * day))
        ledger.add_entry(entry(kind="baki_sale", method="credit", amount=30_000, counterparty_name="Jamal Uddin",
                               at=DEMO_NOW - 8 * day))
        ledger.add_entry(entry(kind="baki_repayment", amount=60_000, counterparty_name="Jamal Uddin",
                               at=DEMO_NOW - 2 * day))
        ledger.add_entry(entry(kind="baki_sale", method="credit", amount=10_000, counterparty_name="Rina Begum",
                               at=DEMO_NOW - 1 * day))
        balances = ledger.baki_by_customer(SHOP, today=date(2026, 10, 12))
    assert [(b.name, b.outstanding_paisa, b.days_outstanding) for b in balances] == [
        ("Jamal Uddin", 20_000, 8),
        ("Rina Begum", 10_000, 1),
    ]


def test_the_same_sms_cannot_be_booked_twice(db, clock):
    with db.session() as session:
        ledger = LedgerService(session, clock)
        ledger.add_entry(entry(method="bkash", trx_id="BJ71K2ABCD"))
        assert ledger.has_trx(SHOP, "BJ71K2ABCD")
        with pytest.raises(IntegrityError):
            ledger.add_entry(entry(method="bkash", trx_id="BJ71K2ABCD"))
