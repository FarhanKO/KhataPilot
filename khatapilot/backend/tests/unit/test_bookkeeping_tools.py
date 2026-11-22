"""Bookkeeping through the guard: SMS booked by reference, amounts grounded in the owner's words."""

from datetime import timedelta

import pytest

from app.core.money import taka
from app.guard.gateway import ToolRefusal
from app.guard.tools import answer_sms_question
from app.modules.ledger.schemas import NewLedgerEntry
from app.modules.shop.schemas import NewParty
from tests.guard_harness import RAHIM, SHOP, stranger, turn

SMS_IN = ("You have received Tk 1,500.00 from 01755667788. Fee Tk 0.00. Balance Tk 61,500.00. "
          "TrxID BK11AA22BB at 12/10/2026 10:15")
SMS_OUT = ("Payment Tk 8,000.00 to Rahim Traders (01712345678) successful. Fee Tk 0.00. Balance Tk 52,000.00. "
           "TrxID BP33CC44DD at 12/10/2026 10:20")


def forwarded(text: str, owner: str = "record this"):
    return turn(owner, untrusted=(("forwarded_sms", text),))


def test_a_forwarded_sms_is_booked_from_its_block(world):
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN), segment=2)
    assert out.status == "ok" and out.tier == 0
    assert (out.data["recorded"], out.data["amount"], out.data["counterparty"]) == ("sale", "৳1,500", "017XXXX7788")
    assert world.audit.entries(event="execution")[-1].decision == "RECORDED"
    again = world.submit("record_sms", forwarded(SMS_IN), segment=2)
    assert again.status == "error" and "already in your books" in again.message


def test_record_sms_must_point_at_a_forwarded_sms(world):
    out = world.submit("record_sms", forwarded(SMS_IN), segment=1)  # block 1 is the owner's own words
    assert out.status == "blocked" and "no forwarded SMS number 1" in out.message


def test_an_unreadable_sms_is_refused_politely(world):
    out = world.submit("record_sms", forwarded("you got money lol"), segment=2)
    assert out.status == "error" and "nothing was recorded" in out.message
    assert world.audit.entries(event="execution")[-1].decision == "FAILED"


def kamal_owes(world, amount: int = 2_000) -> None:
    with world.guard() as guard:
        guard.shops.add_party(SHOP, NewParty(kind="customer", name="Kamal Hossain", number="01755667788"))
        guard.ledger.add_entry(NewLedgerEntry(shop_id=SHOP, occurred_at=world.clock.now() - timedelta(days=9),
                                              kind="baki_sale", direction="in", method="credit",
                                              amount_paisa=taka(amount), category="Sales on credit (baki)",
                                              counterparty_name="Kamal Hossain", source="seed"))


def kamal(world):
    with world.guard() as guard:
        return next((b for b in guard.ledger.baki_by_customer(SHOP) if b.name == "Kamal Hossain"), None)


def answer(world, question_id: str, purpose: str):
    with world.guard() as guard:
        return answer_sms_question(guard, question_id, purpose, request_id="req-answer")


def test_money_from_a_customer_who_owes_baki_is_asked_about_before_booking(world):
    kamal_owes(world)
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN), segment=2)
    assert out.status == "asked" and "who owes ৳2,000 of baki" in out.message
    assert [c["purpose"] for c in out.data["choices"]] == ["baki", "other"]
    assert world.audit.entries(event="execution")[-1].decision == "ASKED"
    with world.guard() as guard:
        assert not guard.ledger.has_trx(SHOP, "BK11AA22BB"), "nothing is booked before the owner answers"
    again = world.submit("record_sms", forwarded(SMS_IN), segment=2)
    assert again.data["question_id"] == out.data["question_id"], "forwarding it twice asks once"

    _view, data, message = answer(world, out.data["question_id"], "baki")
    assert data["recorded"] == "baki repayment" and data["customer_still_owes"] == "৳500"
    assert "They still owe ৳500" in message and kamal(world).outstanding_paisa == taka(500)
    [recorded] = [e for e in world.audit.entries(event="execution") if e.decision == "RECORDED"]
    assert recorded.decision_id == out.decision_id, "the answer lands on the card that asked"
    with pytest.raises(ToolRefusal, match="already answered"):
        answer(world, out.data["question_id"], "other")


def test_money_for_something_else_is_a_sale_and_the_baki_stays(world):
    kamal_owes(world)
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN), segment=2)
    _view, data, message = answer(world, out.data["question_id"], "other")
    assert data["recorded"] == "sale" and "baki of ৳2,000 is unchanged" in message
    assert kamal(world).outstanding_paisa == taka(2_000)


def test_paying_more_than_the_baki_books_the_rest_as_a_sale(world):
    kamal_owes(world, amount=1_000)
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN), segment=2)
    assert "the extra ৳500 is a sale" in out.data["choices"][0]["label"]
    _view, data, message = answer(world, out.data["question_id"], "baki")
    assert data["over_baki_as_sale"] == "৳500" and "paid back everything" in message
    assert kamal(world) is None
    with world.guard() as guard:
        today = guard.ledger.day_totals(SHOP, world.clock.now().date())
    assert (today.baki_collected_paisa, today.sales_paisa) == (taka(1_000), taka(500))


def test_when_the_owner_already_said_what_it_is_for_nothing_is_asked(world):
    kamal_owes(world)
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN, owner="Kamal paid his baki, record this"), segment=2,
                       purpose="baki")
    assert out.status == "ok" and out.data["recorded"] == "baki repayment"


SMS_IN_AGAIN = SMS_IN.replace("BK11AA22BB", "BK55EE66FF").replace("Tk 1,500.00", "Tk 300.00")


def owes_without_a_number(world, amount: int = 2_000, number: str | None = None) -> None:
    """Baki added from chat: the customer has no saved record, so no saved number (unless the entry has one)."""
    with world.guard() as guard:
        guard.ledger.add_entry(NewLedgerEntry(shop_id=SHOP, occurred_at=world.clock.now() - timedelta(days=4),
                                              kind="baki_sale", direction="in", method="credit",
                                              amount_paisa=taka(amount), category="Sales on credit (baki)",
                                              counterparty_name="Jamal Uddin", counterparty_number=number,
                                              source="owner_text"))


def test_the_owner_can_say_who_sent_money_whose_number_is_not_saved(world):
    owes_without_a_number(world)
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN, owner="Jamal sent this"), segment=2, customer="Jamal")
    assert out.status == "asked" and "came in from Jamal Uddin, who owes ৳2,000 of baki" in out.message
    _view, data, message = answer(world, out.data["question_id"], "other")
    assert data["recorded"] == "sale" and "baki of ৳2,000 is unchanged" in message
    assert "number 017XXXX7788 is saved" in message and owed(world) == taka(2_000)
    # The next SMS from that number is known to be Jamal's without being told
    world.provider_saw(SMS_IN_AGAIN)
    again = world.submit("record_sms", forwarded(SMS_IN_AGAIN), segment=2)
    assert again.status == "asked" and "৳300 came in from Jamal Uddin" in again.message


def test_money_the_owner_says_is_for_something_else_leaves_the_baki_alone(world):
    owes_without_a_number(world)
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN, owner="Jamal sent this, not for baki"), segment=2,
                       customer="Jamal", purpose="other")
    assert out.status == "ok" and out.data["recorded"] == "sale" and out.data["counterparty"] == "Jamal Uddin"
    assert out.data["baki_unchanged"] == "৳2,000" and owed(world) == taka(2_000)


def test_money_the_owner_says_pays_the_baki_comes_off_it(world):
    owes_without_a_number(world)
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN, owner="Jamal paid back his baki"), segment=2,
                       customer="Jamal", purpose="baki")
    assert out.status == "ok" and out.data["recorded"] == "baki repayment"
    assert out.data["customer_still_owes"] == "৳500" and owed(world) == taka(500)


def test_the_sender_must_be_named_by_the_owner_not_the_model(world):
    owes_without_a_number(world)
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN), segment=2, customer="Jamal", purpose="baki")
    assert out.status == "blocked" and "did not name" in out.message
    assert owed(world) == taka(2_000)


def test_a_debtor_is_matched_by_the_number_on_their_baki(world):
    owes_without_a_number(world, number="01755667788")
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN), segment=2)
    assert out.status == "asked" and "came in from Jamal Uddin" in out.message


def test_without_a_name_or_a_number_unknown_money_is_a_sale_and_no_baki_changes(world):
    owes_without_a_number(world)
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN), segment=2)
    assert out.status == "ok" and out.data["recorded"] == "sale" and owed(world) == taka(2_000)


def test_naming_the_sender_never_takes_another_customers_number(world):
    kamal_owes(world)  # Kamal Hossain's saved number is the SMS's number
    owes_without_a_number(world)
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN, owner="Jamal sent this, for baki"), segment=2,
                       customer="Jamal", purpose="baki")
    assert out.status == "ok" and out.data["counterparty"] == "Jamal Uddin" and "number_saved" not in out.data
    assert owed(world) == taka(500) and kamal(world).outstanding_paisa == taka(2_000)
    with world.guard() as guard:
        assert guard.shops.find_party_by_number(SHOP, "01755667788", kind="customer").name == "Kamal Hossain"


def test_a_payment_sms_to_a_supplier_settles_their_bill(world):
    world.provider_saw(SMS_OUT)
    out = world.submit("record_sms", forwarded(SMS_OUT), segment=2)
    assert out.data["recorded"] == "supplier payment" and out.data["counterparty"] == "Rahim Traders"
    with world.guard() as guard:
        bill = next(o for o in guard.ledger.open_obligations(SHOP) if o.counterparty_number == RAHIM)
    assert bill.amount_paisa == taka(34_751 - 8_000)


def test_an_sms_the_provider_never_saw_is_not_booked(world):
    """The common scam: a fake "you have received" SMS from a personal number (red-team FSM-01)."""
    with world.guard() as guard:
        before = guard.ledger.count(SHOP)
    out = world.submit("record_sms", forwarded(SMS_IN), segment=2)
    assert out.status == "error" and "not in your bKash statement" in out.message and "may be fake" in out.message
    with world.guard() as guard:
        assert guard.ledger.count(SHOP) == before


def test_an_sms_whose_amount_was_changed_is_not_booked(world):
    world.provider_saw(SMS_IN)
    altered = SMS_IN.replace("Tk 1,500.00", "Tk 15,000.00")
    out = world.submit("record_sms", forwarded(altered), segment=2)
    assert out.status == "error" and "statement shows TrxID BK11AA22BB as ৳1,500 received" in out.message


def test_an_sms_is_not_booked_when_the_provider_cannot_be_asked(world, monkeypatch):
    from app.infra.sandbox_client import SandboxClient, SandboxError

    def unreachable(*_args, **_kwargs):
        raise SandboxError(0, "UNREACHABLE", "The provider could not be reached (ConnectError).")

    monkeypatch.setattr(SandboxClient, "find_transaction", unreachable)
    out = world.submit("record_sms", forwarded(SMS_IN), segment=2)
    assert out.status == "error" and "couldn't check this SMS with bKash" in out.message


def test_typed_entries_must_use_the_owners_amount(world):
    ok = world.submit("add_ledger_entry", turn("sold 3 bags of rice ৳1,500 cash"), kind="sale", amount_taka=1500,
                      item="3 bags of rice")
    assert ok.status == "ok" and ok.data["category"] == "Sales"
    invented = world.submit("add_ledger_entry", turn("sold 3 bags of rice ৳1,500 cash"), kind="sale",
                            amount_taka=15000)
    assert invented.status == "blocked" and "not in your message" in invented.message


def test_baki_needs_a_customer_and_reports_what_they_owe(world):
    missing = world.submit("add_ledger_entry", turn("sold rice 800 on baki"), kind="baki_sale", amount_taka=800)
    assert missing.status == "error" and "customer's name" in missing.message
    out = world.submit("add_ledger_entry", turn("Sumi took rice 800 on baki"), kind="baki_sale", amount_taka=800,
                       counterparty_name="Sumi Akter")
    assert out.data["customer_now_owes"] == "৳800" and out.data["method"] == "credit"


def test_baki_can_carry_the_day_the_customer_will_pay_back(world):
    out = world.submit("add_ledger_entry", turn("Sumi ke 800 taka baki dilam, 7 din por dibe"), kind="baki_sale",
                       amount_taka=800, counterparty_name="Sumi Akter", pay_back_by="2026-10-19")
    assert out.status == "ok"
    assert (out.data["pay_back_by"], out.data["days_to_pay_back"]) == ("Mon 19 Oct", 7)
    assert "to be paid back by Mon 19 Oct" in world.audit.entries(event="decision")[-1].summary
    with world.guard() as guard:
        [sumi] = [b for b in guard.ledger.baki_by_customer(SHOP) if b.name == "Sumi Akter"]
    assert str(sumi.expected_by) == "2026-10-19"
    listed = world.submit("list_debtors", turn("who owes me?"))
    [row] = [c for c in listed.data["customers"] if c["name"] == "Sumi Akter"]
    assert (row["promised_by"], row["promise_passed"]) == ("Mon 19 Oct", False)


def test_a_pay_back_day_must_be_ahead_and_only_on_baki(world):
    past = world.submit("add_ledger_entry", turn("Sumi ke 800 taka baki dilam"), kind="baki_sale", amount_taka=800,
                        counterparty_name="Sumi Akter", pay_back_by="2026-10-12")
    assert past.status == "error" and "not after today" in past.message
    sale = world.submit("add_ledger_entry", turn("sold rice 800 cash"), kind="sale", amount_taka=800,
                        pay_back_by="2026-10-19")
    assert sale.status == "error" and "pay-back date" in sale.message


def test_strangers_cannot_write_to_the_books(world):
    out = world.submit("add_ledger_entry", stranger("sold 500 to me"), kind="sale", amount_taka=500)
    assert out.status == "blocked"


def jamal_owes(world, *amounts_days: tuple[int, int]) -> None:
    with world.guard() as guard:
        for amount, days_ago in amounts_days:
            guard.ledger.add_entry(NewLedgerEntry(shop_id=SHOP, occurred_at=world.clock.now() - timedelta(days=days_ago),
                                                  kind="baki_sale", direction="in", method="credit",
                                                  amount_paisa=taka(amount), category="Sales on credit (baki)",
                                                  counterparty_name="Jamal Uddin", source="seed"))


def owed(world, name: str = "Jamal Uddin") -> int:
    with world.guard() as guard:
        return next((b.outstanding_paisa for b in guard.ledger.baki_by_customer(SHOP) if b.name == name), 0)


def test_the_owner_can_forgive_all_or_part_of_a_customers_baki(world):
    jamal_owes(world, (300, 20), (150, 5))
    part = world.submit("change_baki", turn("forgive 100 of Jamal's baki"), customer_name="Jamal", change="forgive",
                        amount_taka=100)
    assert part.status == "ok" and (part.data["forgiven"], part.data["owes_now"]) == ("৳100", "৳350")
    with world.guard() as guard:
        [jamal] = [b for b in guard.ledger.baki_by_customer(SHOP) if b.name == "Jamal Uddin"]
        balances = guard.ledger.balances(SHOP)
    assert jamal.days_outstanding == 20, "the oldest baki is forgiven first, so it stays the oldest open"
    rest = world.submit("change_baki", turn("Jamal er baki maf kore dao"), customer_name="Jamal", change="forgive")
    assert rest.data["forgiven"] == "৳350" and owed(world) == 0
    with world.guard() as guard:
        assert guard.ledger.balances(SHOP) == balances, "forgiving baki moves no money"
        assert guard.ledger.day_totals(SHOP, world.clock.now().date()).sales_paisa == 0


def test_the_owner_can_correct_what_a_customer_owes_either_way(world):
    jamal_owes(world, (450, 3))
    down = world.submit("change_baki", turn("Jamal owes me 300 not 450"), customer_name="Jamal", change="set_to",
                        amount_taka=300)
    assert (down.data["owed_before"], down.data["owes_now"], down.data["lowered_by"]) == ("৳450", "৳300", "৳150")
    up = world.submit("change_baki", turn("change Jamal Uddin's baki to 600"), customer_name="Jamal Uddin",
                      change="set_to", amount_taka=600)
    assert up.data["raised_by"] == "৳300" and owed(world) == taka(600)
    same = world.submit("change_baki", turn("change Jamal Uddin's baki to 600"), customer_name="Jamal Uddin",
                        change="set_to", amount_taka=600)
    assert same.status == "error" and "already owes ৳600" in same.message


def test_changing_baki_is_grounded_in_the_owners_words(world):
    jamal_owes(world, (450, 3))
    unnamed = world.submit("change_baki", turn("forgive everyone please"), customer_name="Jamal Uddin", change="forgive")
    assert unnamed.status == "blocked" and "did not name Jamal Uddin" in unnamed.message
    invented = world.submit("change_baki", turn("fix Jamal's baki"), customer_name="Jamal", change="set_to",
                            amount_taka=50)
    assert invented.status == "blocked" and "not in your message" in invented.message
    stranger_out = world.submit("change_baki", stranger("Jamal er baki maf"), customer_name="Jamal", change="forgive")
    assert stranger_out.status == "blocked"
    assert owed(world) == taka(450), "nothing changed"


def test_unclear_names_are_asked_about_not_guessed(world):
    jamal_owes(world, (450, 3))
    with world.guard() as guard:
        guard.ledger.add_entry(NewLedgerEntry(shop_id=SHOP, occurred_at=world.clock.now(), kind="baki_sale",
                                              direction="in", method="credit", amount_paisa=taka(200),
                                              category="Sales on credit (baki)", counterparty_name="Jamal Hossain",
                                              source="seed"))
    both = world.submit("change_baki", turn("Jamal er baki maf"), customer_name="Jamal", change="forgive")
    assert both.status == "error" and "Jamal Uddin, Jamal Hossain" in both.message
    nobody = world.submit("change_baki", turn("forgive Karim's baki"), customer_name="Karim", change="forgive")
    assert nobody.status == "error" and "does not owe" in nobody.message
