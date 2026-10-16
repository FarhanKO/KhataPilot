from datetime import date

import pytest

from app.core.actions import PaymentRequest
from app.guard.intent import extract_payment_intents, match_payment, names_equivalent

TODAY = date(2026, 10, 12)  # a Monday
KNOWN = ["Rahim Traders", "রহিম ট্রেডার্স", "Meghna Distributors", "Bismillah Oil Agency"]


def one(text):
    intents = extract_payment_intents(text, today=TODAY, known_names=KNOWN)
    assert len(intents) == 1, intents
    return intents[0]


@pytest.mark.parametrize(
    ("text", "name", "number", "amount", "day"),
    [
        ("pay Rahim Traders ৳8,000 on Friday", "Rahim Traders", None, 800_000, date(2026, 10, 16)),
        ("Rahim Traders ke 8000 taka dao kal", "Rahim Traders", None, 800_000, date(2026, 10, 13)),
        ("pay Meghna Distributors Tk. 15,000 now", "Meghna Distributors", None, 1_500_000, None),
        ("send 50000 to 01999888777", None, "01999888777", 5_000_000, None),
        ("pay Rahim Traders 8000 at 01755501234", "Rahim Traders", "01755501234", 800_000, None),
        ("pay Rahim Traders 8.5k tomorrow", "Rahim Traders", None, 850_000, date(2026, 10, 13)),
        ("Please pay Shapla Packaging 3 cartons worth 4,500 taka on 20/10", "Shapla Packaging", None, 450_000,
         date(2026, 10, 20)),
        ("রহিম ট্রেডার্সকে ৮০০০ টাকা পাঠাও", "রহিম ট্রেডার্স", None, 800_000, None),
        ("pay Rahim Traders 2000 today", "Rahim Traders", None, 200_000, None),
    ],
)
def test_extracts_the_owners_payment(text, name, number, amount, day):
    intent = one(text)
    assert (intent.payee_name, intent.payee_number, intent.amounts, intent.execute_on) == (name, number, (amount,), day)


def test_two_payments_in_one_sentence():
    intents = extract_payment_intents("pay Rahim 4k and Meghna 5,000 on 15 Oct", today=TODAY, known_names=KNOWN)
    assert [(i.payee_name, i.amounts, i.execute_on) for i in intents] == [
        ("Rahim", (400_000,), None), ("Meghna", (500_000,), date(2026, 10, 15))]


@pytest.mark.parametrize("text", ["sold 3 bags of rice 1500 cash", "what is my balance", "pay Rahim Traders",
                                  "how much do I owe Meghna Distributors?"])
def test_no_intent_without_a_payment_instruction(text):
    assert extract_payment_intents(text, today=TODAY, known_names=KNOWN) == []


def test_numbers_in_the_clause_are_masked():
    assert "01999888777" not in one("send 50000 to 01999888777").clause


def trusted_lookup(name):
    return None


def request(**kw) -> PaymentRequest:
    base = dict(payee_name="Rahim Traders", payee_number=None, amount_paisa=800_000, execute_on=date(2026, 10, 16))
    base.update(kw)
    return PaymentRequest(**base)


def test_matching():
    intents = [one("pay Rahim Traders ৳8,000 on Friday")]
    assert match_payment(request(), intents, today=TODAY, payee_by_name=trusted_lookup).matched
    wrong_amount = match_payment(request(amount_paisa=8_000_000), intents, today=TODAY, payee_by_name=trusted_lookup)
    assert not wrong_amount.matched and "৳8,000" in wrong_amount.detail and "৳80,000" in wrong_amount.detail
    assert not match_payment(request(payee_name="Meghna Distributors"), intents, today=TODAY,
                             payee_by_name=trusted_lookup).matched
    assert not match_payment(request(execute_on=None), intents, today=TODAY, payee_by_name=trusted_lookup).matched
    assert not match_payment(request(), [], today=TODAY, payee_by_name=trusted_lookup).matched


def test_a_number_given_by_the_owner_must_be_used_exactly():
    intents = [one("send 50000 to 01999888777")]
    ok = request(payee_name=None, payee_number="01999888777", amount_paisa=5_000_000, execute_on=None)
    swapped = request(payee_name=None, payee_number="01999888778", amount_paisa=5_000_000, execute_on=None)
    assert match_payment(ok, intents, today=TODAY, payee_by_name=trusted_lookup).matched
    assert not match_payment(swapped, intents, today=TODAY, payee_by_name=trusted_lookup).matched


def test_names_equivalent():
    assert names_equivalent("Rahim Traders", "rahim traders")
    assert names_equivalent("Meghna", "Meghna Distributors")
    assert not names_equivalent("Rahim Traders", "Karim Traders")
    assert not names_equivalent("Ra", "Rahim Traders")
