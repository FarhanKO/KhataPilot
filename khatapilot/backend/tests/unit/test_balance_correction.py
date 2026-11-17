"""Balance corrections from chat: the owner says what is really in cash, bKash, Nagad or bank, and the books
book the difference as a correction that moves the balance (and so the forecast) but never sales or profit."""

from datetime import date

import pytest

from app.agent.llm.mock_provider import balance_correction_question, plan_owner_text
from app.core.money import taka
from app.modules.ledger.schemas import NewLedgerEntry
from tests.guard_harness import SHOP, stranger, turn

TODAY = date(2026, 10, 12)


def sold(world, amount: int, method: str = "cash") -> None:
    """The books show exactly `amount` in `method`: an opening balance up to it, then today's sale is all of it."""
    with world.guard() as guard:
        already = guard.ledger.balances(SHOP)[method]
        if already:
            guard.ledger.add_entry(NewLedgerEntry(
                shop_id=SHOP, occurred_at=world.clock.now(), kind="opening_balance",
                direction="out" if already > 0 else "in", method=method, amount_paisa=abs(already),
                category="Opening balance", source="seed"))
        guard.ledger.add_entry(NewLedgerEntry(shop_id=SHOP, occurred_at=world.clock.now(), kind="sale", direction="in",
                                              method=method, amount_paisa=taka(amount), category="Sales",
                                              source="seed"))


def balances(world) -> dict[str, int]:
    with world.guard() as guard:
        return guard.ledger.balances(SHOP)


def test_the_owner_corrects_cash_up_and_only_the_balance_moves(world):
    sold(world, 4_000)
    with world.guard() as guard:
        before = guard.ledger.day_totals(SHOP, world.clock.now().date())
    out = world.submit("correct_balance", turn("my cash should be 5000, not 4000"), method="cash", amount_taka=5000)
    assert out.status == "ok" and out.tier == 0
    assert (out.data["was"], out.data["now"], out.data["raised_by"]) == ("৳4,000", "৳5,000", "৳1,000")
    assert balances(world)["cash"] == taka(5_000)
    with world.guard() as guard:
        after = guard.ledger.day_totals(SHOP, world.clock.now().date())
        [entry] = [e for e in guard.ledger.entries(SHOP) if e.kind == "balance_correction"]
    assert (after.sales_paisa, after.money_in_paisa, after.money_out_paisa) == \
        (before.sales_paisa, before.money_in_paisa, before.money_out_paisa), "a correction is not a sale or an expense"
    assert (entry.direction, entry.method, entry.category, entry.source) == ("in", "cash", "Balance correction",
                                                                             "owner_text")
    assert world.audit.entries(event="execution")[-1].decision == "RECORDED"


def test_a_wallet_can_be_corrected_down_with_a_pointer_to_the_nightly_check(world):
    sold(world, 2_000, "nagad")
    out = world.submit("correct_balance", turn("Nagad balance is actually 1500"), method="nagad", amount_taka=1500)
    assert out.status == "ok" and out.data["lowered_by"] == "৳500" and "nightly check" in out.data["note"]
    assert balances(world)["nagad"] == taka(1_500)


def test_the_same_amount_changes_nothing(world):
    sold(world, 4_000)
    out = world.submit("correct_balance", turn("cash 4000 hobe"), method="cash", amount_taka=4000)
    assert out.status == "error" and "already show ৳4,000" in out.message
    assert balances(world)["cash"] == taka(4_000)


def test_an_empty_drawer_can_be_set_to_zero(world):
    sold(world, 800)
    out = world.submit("correct_balance", turn("drawer is empty, 0 taka"), method="cash", amount_taka=0)
    assert out.status == "ok" and balances(world)["cash"] == 0


def test_a_correction_is_grounded_in_the_owners_own_words(world):
    sold(world, 4_000)
    invented = world.submit("correct_balance", turn("my cash is wrong"), method="cash", amount_taka=9000)
    assert invented.status == "blocked" and "not in your message" in invented.message
    no_zero = world.submit("correct_balance", turn("my cash is wrong"), method="cash", amount_taka=0)
    assert no_zero.status == "blocked"
    outsider = world.submit("correct_balance", stranger("cash should be 9000"), method="cash", amount_taka=9000)
    assert outsider.status == "blocked"
    assert balances(world)["cash"] == taka(4_000), "nothing changed"


# ---- the offline planner

def only(text: str):
    calls = plan_owner_text(text, TODAY)
    assert len(calls) == 1, calls
    return calls[0].name, calls[0].arguments


@pytest.mark.parametrize(("text", "method", "amount"), [
    ("my cash is wrong, it shows 4000 but it should be 5000", "cash", "5000"),
    ("total cash should be 5000", "cash", "5000"),
    ("cash 5,000 not 4,000", "cash", "5000"),
    ("cash in hand is 5000 not 4000", "cash", "5000"),
    ("not 4000 but 5000 cash", "cash", "5000"),
    ("cash 5000 hobe", "cash", "5000"),
    ("drawer e 5000 taka ache", "cash", "5000"),
    ("hate 6000 taka ache", "cash", "6000"),
    ("i counted 5200 in the drawer", "cash", "5200"),
    ("nagad balance should be 3000 not 2000", "nagad", "3000"),
    ("set my bkash balance to 7000", "bkash", "7000"),
    ("bank actually 20000", "bank", "20000"),
])
def test_corrections_are_read_with_the_real_amount_not_the_old_one(text, method, amount):
    assert only(text) == ("correct_balance", {"method": method, "amount_taka": amount})


@pytest.mark.parametrize(("text", "tool"), [
    ("sold 3 bags of rice 1500 cash", "add_ledger_entry"),
    ("paid rent 5000 cash", "add_ledger_entry"),
    ("got 500 from Rina on bkash", "record_customer_payment"),
    ("keep 10000 as buffer", "set_safety_buffer"),
    ("Jamal owes 300 not 450", "change_baki"),
    ("cash koto?", "get_balances"),
])
def test_money_moving_and_other_features_are_not_corrections(text, tool):
    assert only(text)[0] == tool


@pytest.mark.parametrize("text", ["cash 5000 hobe?", "will cash be 5000?", "cash out 2000 bkash"])
def test_questions_and_wallet_cash_outs_never_correct_a_balance(text):
    assert all(c.name != "correct_balance" for c in plan_owner_text(text, TODAY))


def test_an_unclear_correction_is_asked_about():
    assert plan_owner_text("my balance should be 5000", TODAY) == []
    assert "Which balance" in balance_correction_question("my balance should be 5000")
    assert plan_owner_text("cash is wrong, 4000 and 5000", TODAY) == []
    assert "right amount" in balance_correction_question("cash is wrong, 4000 and 5000")
