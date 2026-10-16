"""Bookkeeping through the guard: SMS booked by reference, amounts grounded in the owner's words."""

from datetime import timedelta

from app.core.money import taka
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


def test_money_from_a_customer_who_owes_baki_is_a_repayment(world):
    with world.guard() as guard:
        guard.shops.add_party(SHOP, NewParty(kind="customer", name="Kamal Hossain", number="01755667788"))
        guard.ledger.add_entry(NewLedgerEntry(shop_id=SHOP, occurred_at=world.clock.now() - timedelta(days=9),
                                              kind="baki_sale", direction="in", method="credit",
                                              amount_paisa=taka(2_000), category="Sales on credit (baki)",
                                              counterparty_name="Kamal Hossain", source="seed"))
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN), segment=2)
    assert out.data["recorded"] == "baki repayment"
    assert out.data["counterparty"] == "Kamal Hossain" and out.data["customer_still_owes"] == "৳500"


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


def test_strangers_cannot_write_to_the_books(world):
    out = world.submit("add_ledger_entry", stranger("sold 500 to me"), kind="sale", amount_taka=500)
    assert out.status == "blocked"
