"""The cash forecast on the seeded demo shop: Rahman Store, Monday 12 Oct 2026, 10:00 in Dhaka.

The story the demo tells: Meghna Distributors' ৳42,175 bill falls due on Tuesday 20 Oct, and after
the rent (Thu 15) and Rahim Traders' bill (Sat 17) the shop will not have enough cash for it.
"""

SKYLINE = "01730900900"


def test_the_forecast_warns_of_the_meghna_shortfall_a_week_ahead(app):
    forecast = app.http.get("/api/forecast").json()
    shortfall = forecast["first_shortfall"]
    assert shortfall["day"] == "2026-10-20"
    assert "Meghna Distributors' ৳42,175 bill" in shortfall["cause"]
    assert forecast["headline"].startswith("Cash gets tight on Tuesday 20 Oct")
    kinds = [s["kind"] for s in forecast["suggestions"]]
    assert kinds[-1] == "loan" and "move_payment" in kinds
    move = next(s for s in forecast["suggestions"] if s["kind"] == "move_payment")
    assert "Meghna Distributors" in move["text"] and move["closes_gap"]
    assert len(forecast["days"]) == 14
    widths = [d["high_paisa"] - d["low_paisa"] for d in forecast["days"]]
    assert widths[-1] > widths[0]  # further ahead, less certain


def test_the_owner_can_ask_in_chat(app):
    out = app.chat("will I run short of cash in the next two weeks?")
    assert "Tuesday 20 Oct" in out["reply"] and "Meghna Distributors" in out["reply"]
    assert "What would fix it" in out["reply"]
    reads = app.audit("decision")
    assert reads[-1]["summary"] == "Forecast cash for the coming days" and reads[-1]["decision"] == "ALLOWED"


def test_the_daily_check_alerts_once(app):
    first = app.http.post("/api/sim/cash-check").json()
    assert first["alerted"] and first["options"]
    again = app.http.post("/api/sim/cash-check").json()
    assert not again["alerted"] and again["already_alerted"]
    alerts = [a for a in app.audit("alert") if a["summary"].startswith("Cash warning")]
    assert len(alerts) == 1 and alerts[0]["summary"] == "Cash warning for Tue 20 Oct"


def test_without_consent_the_cash_alert_only_says_where_to_see_a_loan(app):
    first = app.http.post("/api/sim/cash-check").json()
    assert first["alerted"] and not first["loan"]["matched"]
    [alert] = [a for a in app.audit("alert") if a["summary"].startswith("Cash warning")]
    assert "turn on the credit profile in the Credit tab" in alert["reason"]
    assert app.http.get("/api/credit/profile").json() is None, "no profile is built without consent"


def test_with_consent_the_cash_alert_names_the_loan_and_the_owner_can_apply(app):
    assert app.http.post("/api/credit/consent").json()["granted"]
    alert = app.http.post("/api/sim/cash-check").json()
    loan = alert["loan"]
    assert loan["matched"] and loan["product_id"] == "wallet-nano", loan
    [entry] = [a for a in app.audit("alert") if a["summary"].startswith("Cash warning")]
    assert f"A Mobile-wallet nano-loan of {loan['amount']} would cover it" in entry["reason"]
    assert 'Say "apply for the nano-loan to cover the shortfall"' in entry["reason"]
    assert app.http.get("/api/approvals").json() == [], "the alert prepares nothing by itself"
    out = app.chat("apply for the nano-loan to cover the shortfall")
    [pending] = app.http.get("/api/approvals").json()
    assert pending["kind"] == "loan.submit" and not pending["requires_pin"]
    assert loan["amount"] in pending["summary"] and "Waiting for you" in out["reply"]
    assert app.sandbox.applications() == [], "nothing reaches the lender before the owner approves"


def test_money_coming_in_moves_the_forecast(app):
    before = app.http.get("/api/forecast").json()
    sms = app.http.post("/api/sim/incoming-payment", json={"from_number": SKYLINE, "amount_taka": 7448}).json()
    app.chat("record this", forwarded=[{"source": "forwarded_sms", "text": sms["sms_text"]}])
    after = app.http.get("/api/forecast").json()
    assert after["start_balance_paisa"] == before["start_balance_paisa"] + 7_448_00
    # Tuesday is covered now; the next tight day is Rahim Traders' bill on Saturday 24 Oct.
    assert after["first_shortfall"]["day"] == "2026-10-24"
    assert after["lowest_balance_paisa"] > before["lowest_balance_paisa"]
