"""The mock planner covers what customers owe the shop from chat: add baki, ask what one person owes, money
a customer gave (asked about unless the owner says what it is for), corrections and removal."""

from datetime import date

import pytest

from app.agent.llm.mock_provider import plan_owner_text

TODAY = date(2026, 10, 12)


def only(text: str):
    calls = plan_owner_text(text, TODAY)
    assert len(calls) == 1, calls
    return calls[0].name, calls[0].arguments


@pytest.mark.parametrize(("text", "name", "amount", "method"), [
    ("Babul Mia paid 2000", "Babul Mia", "2000", "cash"),
    ("Babul sent 1000 on bkash", "Babul", "1000", "bkash"),
    ("Babul Mia sent me 1000 by nagad", "Babul Mia", "1000", "nagad"),
    ("got 500 from Babul Mia", "Babul Mia", "500", "cash"),
    ("received 700 from Rina Akter on bkash", "Rina Akter", "700", "bkash"),
    ("Babul Mia 500 taka dilo", "Babul Mia", "500", "cash"),
    ("Babul theke 300 taka pelam", "Babul", "300", "cash"),
])
def test_money_from_a_customer_leaves_the_purpose_to_the_owner(text, name, amount, method):
    assert only(text) == ("record_customer_payment", {"customer_name": name, "amount_taka": amount, "method": method})


@pytest.mark.parametrize(("text", "purpose"), [
    ("Babul Mia paid back 500", "baki"),
    ("Jamal 500 taka ferot dilo", "baki"),
    ("Jamal paid 500 for his baki", "baki"),
    ("Babul paid 300 advance for a new order", "other"),
])
def test_saying_what_the_money_is_for_sets_the_purpose(text, purpose):
    tool, arguments = only(text)
    assert tool == "record_customer_payment" and arguments["purpose"] == purpose


@pytest.mark.parametrize("text", [
    "paid DESCO 1000", "I paid Rahim Traders 5000", "Rahim Traders sent 10 bags of rice 5000 on credit",
    "pay Babul Mia 500",
])
def test_the_shop_paying_someone_is_not_money_in(text):
    assert all(c.name != "record_customer_payment" for c in plan_owner_text(text, TODAY))


@pytest.mark.parametrize(("text", "expected"), [
    ("Babul Mia owes 3000 not 4000", {"customer_name": "Babul Mia", "change": "set_to", "amount_taka": "3000"}),
    ("Babul Mia's baki is now 3500", {"customer_name": "Babul Mia", "change": "set_to", "amount_taka": "3500"}),
    ("update Rina's baki to 1200", {"customer_name": "Rina", "change": "set_to", "amount_taka": "1200"}),
    ("remove Babul Mia from debtors", {"customer_name": "Babul Mia", "change": "forgive"}),
    ("delete Karim Mia's debt", {"customer_name": "Karim Mia", "change": "forgive"}),
    ("delete Karim Mia's dept", {"customer_name": "Karim Mia", "change": "forgive"}),
])
def test_corrections_and_removal(text, expected):
    assert only(text) == ("change_baki", expected)


@pytest.mark.parametrize(("text", "who"), [
    ("how much does Babul Mia owe?", "Babul Mia"),
    ("what does Rina owe me", "Rina"),
    ("does Jamal still owe anything?", "Jamal"),
    ("Babul er baki koto", "Babul"),
    ("Babul koto baki", "Babul"),
])
def test_asking_about_one_customer(text, who):
    assert only(text) == ("list_debtors", {"customer": who})


def test_asking_about_everyone_is_still_the_whole_list():
    assert only("who owes me money?") == ("list_debtors", {"min_days": 0})
