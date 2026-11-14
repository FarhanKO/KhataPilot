"""What the owner approves may have been prepared a while ago: the facts that can change in between are
checked again on approval, so a double tap or a newly blocked number never pays."""

from app.core.money import taka

from .conftest import PIN
from .test_savings import add_account, savings


def test_the_same_payment_held_twice_is_paid_once(app):
    first = app.chat("pay Fresh Dairy Point 1500", conversation_id="twice-1")["pending"][0]
    second = app.chat("pay Fresh Dairy Point 1500", conversation_id="twice-2")["pending"][0]
    before = app.wallet()
    assert app.approve(first["id"], PIN)["status"] == "completed"
    again = app.approve(second["id"], PIN)
    assert again["status"] == "refused" and "not paid twice" in again["message"]
    assert app.wallet() == before - taka(1500)
    assert not any(p["id"] == second["id"] for p in app.http.get("/api/approvals").json())  # closed, not left open


def test_a_number_blocked_while_the_payment_waits_is_not_paid(app):
    held = app.chat("pay Fresh Dairy Point 1500")["pending"][0]
    number = held["action"]["payment"]["payee_number"]
    assert app.http.post("/api/owner/block", json={"number": number}).json()["ok"]
    before = app.wallet()
    refused = app.approve(held["id"], PIN)
    assert refused["status"] == "refused" and "blocked list" in refused["message"]
    assert app.wallet() == before


def test_two_taps_on_move_to_savings_save_once(app):
    app.chat("save 10% of my profit every month")
    add_account(app)
    app.chat("sold goods 60,000 cash")
    first = app.http.post("/api/savings/prepare", json={}).json()
    second = app.http.post("/api/savings/prepare", json={}).json()
    assert first["status"] == second["status"] == "held"
    before = app.wallet()
    assert app.approve(first["pending_id"], PIN)["status"] == "completed"
    again = app.approve(second["pending_id"], PIN)
    assert again["status"] == "refused"
    assert app.wallet() == before - 230_000 and savings(app)["saved_paisa"] == 230_000


def test_a_plan_amount_that_no_longer_fits_is_prepared_again(app):
    app.chat("save 10% of my profit every month")
    add_account(app)
    app.chat("sold goods 60,000 cash")
    held = app.http.post("/api/savings/prepare", json={}).json()
    app.chat("put 1,000 in savings")  # the owner's own amount, held separately
    own = app.http.get("/api/approvals").json()
    own_id = next(p["id"] for p in own if p["id"] != held["pending_id"])
    assert app.approve(own_id, PIN)["status"] == "completed"
    stale = app.approve(held["pending_id"], PIN)
    assert stale["status"] == "refused" and "Only ৳1,300 is left to save this month" in stale["message"]
