"""The daily match of the books against the provider, on the seeded demo shop."""


def check(app) -> dict:
    return app.http.post("/api/sim/provider-check").json()


def test_books_and_provider_agree_on_the_demo_shop(app):
    assert check(app) == {"matched": True, "reachable": True, "problems": []}


def test_money_that_arrived_but_is_not_booked_is_found_once(app):
    sms = app.http.post("/api/sim/incoming-payment", json={"from_number": "01811998877", "amount_taka": 1250}).json()
    first = check(app)
    assert first["alerted"] and len(first["problems"]) == 1
    problem = first["problems"][0]
    assert "Your bKash books show ৳7,000 but bKash shows ৳8,250: ৳1,250 more than the books." in problem
    assert sms["trx_id"] in problem
    assert check(app)["already_alerted"]  # told once a day, not every run
    feed = app.http.get("/api/notifications").json()
    money = next(s for s in feed["segments"] if s["id"] == "money")
    assert any(i["title"] == "Books and provider do not match" for i in money["items"])

    # Forwarding the SMS books it, and the books match again.
    app.chat("", forwarded=[{"source": "forwarded_sms", "text": sms["sms_text"]}])
    assert check(app)["matched"]


def test_payments_made_through_khatapilot_are_on_the_statement(app):
    app.chat("pay Rahim Traders 3000")
    assert check(app)["matched"]
