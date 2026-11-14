"""Bills from the chat box on the seeded demo shop (mock LLM): read, add, change, pay part of, remove."""


def open_bills(app) -> dict[str, dict]:
    return {b["counterparty_name"]: b for b in app.http.get("/api/obligations").json()}


def test_bills_can_be_managed_from_chat(app):
    before = open_bills(app)
    assert {"Haji Abdul Malek (landlord)", "DESCO prepaid recharge", "Rubel (shop helper)"} <= set(before)

    listed = app.chat("show my bills")["reply"]
    assert f"{len(before)} open bills" in listed and "Meghna Distributors" in listed

    assert "priority" in app.chat("Rubel is urgent")["reply"]
    assert open_bills(app)["Rubel (shop helper)"]["priority"] is True

    app.chat("Meghna bill is now 40,000")
    assert open_bills(app)["Meghna Distributors"]["amount_paisa"] == 4_000_000

    paid = app.chat("paid Haji Abdul Malek 10,000")["reply"]
    assert "bill now has ৳12,000 left" in paid
    assert open_bills(app)["Haji Abdul Malek (landlord)"]["amount_paisa"] == 1_200_000

    assert "Removed the bill" in app.chat("delete the DESCO bill")["reply"]
    assert "DESCO prepaid recharge" not in open_bills(app)

    asked = app.chat("I have to pay Kashem")["reply"]
    assert asked.startswith("To add the bill I need how much and by when.")
    added = app.chat("I have to pay Kashem 5,000 by Friday")["reply"]
    assert added.startswith("Added the bill: Kashem ৳5,000")
    assert open_bills(app)["Kashem"]["amount_paisa"] == 500_000
