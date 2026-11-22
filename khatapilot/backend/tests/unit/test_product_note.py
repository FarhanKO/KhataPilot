"""The optional product note: which products and how much, kept on the ledger entry only when the owner said it,
and only in the owner's own words (the grounding check refuses one the model made up or read in an SMS)."""

from app.agent.llm.mock_provider import products_in
from app.guard.checks import product_note_missing
from tests.guard_harness import SHOP, turn
from tests.unit.test_bookkeeping_tools import SMS_IN, forwarded, kamal_owes, answer


def latest(world):
    with world.guard() as guard:
        return guard.ledger.entries(SHOP, limit=1)[0]


def test_a_sale_keeps_the_product_note_apart_from_the_note(world):
    said = "sold 2 kg chini ar 1 ta Lux soap 260 taka cash"
    out = world.submit("add_ledger_entry", turn(said), kind="sale", amount_taka="260",
                       item="2 kg chini, 1 ta Lux soap")
    assert out.status == "ok" and out.data["products"] == "2 kg chini, 1 ta Lux soap"
    entry = latest(world)
    assert (entry.product_note, entry.note) == ("2 kg chini, 1 ta Lux soap", None)


def test_the_product_note_is_optional(world):
    out = world.submit("add_ledger_entry", turn("sold 500 taka cash"), kind="sale", amount_taka="500")
    assert out.status == "ok" and "products" not in out.data
    assert latest(world).product_note is None


def test_a_product_note_the_owner_did_not_say_is_refused(world):
    with world.guard() as guard:
        before = guard.ledger.count(SHOP)
    out = world.submit("add_ledger_entry", turn("sold 2 kg sugar 300 taka"), kind="sale", amount_taka="300",
                       item="5 kg sugar")
    assert out.status == "blocked" and "product note" in out.message and "5" in out.message
    with world.guard() as guard:
        assert guard.ledger.count(SHOP) == before, "nothing is recorded with a made-up product note"


def test_a_product_note_from_a_forwarded_sms_is_refused(world):
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN, owner="record this"), segment=2, item="10 bags of rice")
    assert out.status == "blocked" and "product note" in out.message


def test_a_forwarded_sms_keeps_the_owners_product_note(world):
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN, owner="record this, it was for 3 bosta chal"), segment=2,
                       item="3 bosta chal")
    assert out.status == "ok" and out.data["products"] == "3 bosta chal"
    assert latest(world).product_note == "3 bosta chal"


def test_the_product_note_waits_with_the_question_and_lands_on_the_sale(world):
    kamal_owes(world, amount=1_000)
    world.provider_saw(SMS_IN)
    out = world.submit("record_sms", forwarded(SMS_IN, owner="record this, 2 kg dal"), segment=2, item="2 kg dal")
    assert out.status == "asked"
    _view, data, _message = answer(world, out.data["question_id"], "baki")
    assert data["products"] == "2 kg dal"
    with world.guard() as guard:
        repaid, sale = sorted(guard.ledger.entries(SHOP, limit=2), key=lambda e: e.kind)
    assert (repaid.kind, repaid.product_note) == ("baki_repayment", None)
    assert (sale.kind, sale.product_note) == ("sale", "2 kg dal"), "the part over the baki is what was bought"


def test_typed_money_from_a_customer_keeps_its_product_note(world):
    out = world.submit("record_customer_payment", turn("Rina paid 400 for 2 packets biscuit"),
                       customer_name="Rina", amount_taka="400", purpose="other", item="2 packets biscuit")
    assert out.status == "ok" and latest(world).product_note == "2 packets biscuit"


def test_grounding_reads_the_owners_words_loosely_but_the_numbers_exactly():
    assert product_note_missing("2 kg chini, 1 Lux soap", "Rina ke 2kg chini ar 1 lux soap 260 taka") == []
    assert product_note_missing("3 bags of rice", "sold 3 bag rice 1500") == []
    assert product_note_missing("৩ কেজি চাল", "৩ কেজি চাল ২১০ টাকা বিক্রি") == []
    assert product_note_missing("3 bags of rice", "3 bosta chal 1500") == ["bags", "rice"], "never translated"
    assert product_note_missing("5 kg sugar", "sold 2 kg sugar 300") == ["5"]


def test_the_offline_model_copies_the_products_the_owner_named():
    assert products_in("sold 3 bags of rice ৳1,500 cash") == "3 bags of rice"
    assert products_in("sold 2 kg sugar and 1 ta Lux soap 260 taka bkash") == "2 kg sugar, 1 ta Lux soap"
    assert products_in("Jamal ke 450 taka baki dilam") is None
