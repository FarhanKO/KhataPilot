"""An incomplete payment gets a question, and the answer it asks for gets through the guard."""

from app.core.money import taka


def test_a_payment_without_an_amount_is_asked_about_then_paid(app):
    before = app.wallet()
    asked = app.chat("pay Rahim Traders")
    assert asked["reply"].startswith("How much should I pay Rahim Traders?")
    assert not asked["outcomes"] and not asked["pending"], "nothing was proposed on a guess"
    assert app.wallet() == before

    paid = app.chat("pay Rahim Traders ৳5,000")  # the example the question gave
    assert "Paid ৳5,000 to Rahim Traders" in paid["reply"]
    assert app.wallet() == before - taka(5000)
