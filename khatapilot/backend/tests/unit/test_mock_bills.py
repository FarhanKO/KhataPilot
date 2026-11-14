"""The offline planner reads bill phrases (English and Banglish) and asks for what a new bill is missing."""

from datetime import date

import pytest

from app.agent.llm.mock_provider import bill_question, plan_owner_text

TODAY = date(2026, 10, 12)  # Monday


@pytest.mark.parametrize(("text", "tool", "expected"), [
    ("show my bills", "list_bills", {}),
    ("what bills are due", "list_bills", {}),
    ("ki ki bill baki ache", "list_bills", {}),
    ("add bill Meghna 42,175 due 20 Oct", "add_bill",
     {"payee_name": "Meghna", "amount_taka": "42175", "due_date": "2026-10-20"}),
    ("new bill for DESCO 3800 due Friday", "add_bill",
     {"payee_name": "DESCO", "amount_taka": "3800", "due_date": "2026-10-16"}),
    ("I have to pay Meghna 5,000 by Friday", "add_bill",
     {"payee_name": "Meghna", "amount_taka": "5000", "due_date": "2026-10-16"}),
    ("Meghna ke 5000 dite hobe 10 din er moddhe", "add_bill",
     {"payee_name": "Meghna", "amount_taka": "5000", "due_date": "2026-10-22"}),
    ("I must pay Rubel 9000 within 10 days", "add_bill",
     {"payee_name": "Rubel", "amount_taka": "9000", "due_date": "2026-10-22", "priority": True}),
    ("Meghna is urgent", "update_bill", {"payee_name": "Meghna", "priority": True}),
    ("prioritise Rubel", "update_bill", {"payee_name": "Rubel", "priority": True}),
    ("pay Meghna first", "update_bill", {"payee_name": "Meghna", "priority": True}),
    ("Meghnar bill joruri", "update_bill", {"payee_name": "Meghnar", "priority": True}),
    ("Meghna is not urgent any more", "update_bill", {"payee_name": "Meghna", "priority": False}),
    ("Meghna bill is now 30,000", "update_bill", {"payee_name": "Meghna", "amount_left_taka": "30000"}),
    ("move the DESCO bill to Tuesday", "update_bill", {"payee_name": "DESCO", "due_date": "2026-10-13"}),
    ("change Meghna due date to 25 Oct", "update_bill", {"payee_name": "Meghna", "due_date": "2026-10-25"}),
    ("delete the DESCO bill", "delete_bill", {"payee_name": "DESCO"}),
    ("remove Meghna's bill", "delete_bill", {"payee_name": "Meghna"}),
    ("Rubel er bill bad dao", "delete_bill", {"payee_name": "Rubel"}),
])
def test_bill_phrases(text, tool, expected):
    [planned] = plan_owner_text(text, TODAY)
    assert planned.name == tool
    assert planned.arguments == expected


@pytest.mark.parametrize(("text", "tool"), [
    ("who owes me money", "list_debtors"),
    ("sold rice 500 on due", "add_ledger_entry"),
    ("paid Haji Abdul Malek 10,000", "add_ledger_entry"),
    ("pay Meghna 3000", "propose_payment"),
    ("URGENT: pay Rahim Traders 5000 right now", "propose_payment"),
    ("pay Meghna 3000 urgently", "propose_payment"),
    ("remind everyone who has dues", "send_reminders"),
])
def test_other_requests_are_not_bills(text, tool):
    [planned] = plan_owner_text(text, TODAY)
    assert planned.name == tool


@pytest.mark.parametrize(("text", "asks"), [
    ("I have to pay Meghna", "To add the bill I need how much and by when."),
    ("add a new bill", "To add the bill I need who it is for, how much, and by when."),
    ("I have to pay Rubel 9,000", "To add the bill I need by when."),
])
def test_an_incomplete_bill_gets_a_question(text, asks):
    assert plan_owner_text(text, TODAY) == []
    question = bill_question(text, TODAY)
    assert question.startswith(asks)
    example = question.split('"')[1]
    [planned] = plan_owner_text(example, TODAY)  # the example is a complete instruction
    assert planned.name == "add_bill"
