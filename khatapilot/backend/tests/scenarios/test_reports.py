"""Printable monthly reports: the owner's full statement and a six-month statement for a lender, from the
books, with the guard's integrity checks, recorded in the audit log and sent nowhere."""

import json


def _report(app, kind: str, month: str = "2026-09") -> dict:
    response = app.http.get("/api/reports/monthly", params={"kind": kind, "month": month})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_months_on_offer_default_to_the_last_finished_one(app):
    months = app.http.get("/api/reports/months").json()
    assert months["default"] == "2026-09"
    assert months["months"][0] == {"month": "2026-10", "label": "October 2026", "finished": False}


def test_the_owners_statement_matches_the_savings_plan_and_says_what_the_guard_saw(app):
    report = _report(app, "owner")
    assert report["title"] == "Monthly statement: September 2026" and report["period"] == "1–30 Sep 2026"
    # the same cash profit the savings plan takes 10% of
    assert report["figures"]["cash_profit_paisa"] == 2_305_300
    assert "cash profit ৳23,053" in report["headline"]
    assert [w["label"] for w in report["weeks"]] == ["1–7 Sep", "8–14 Sep", "15–21 Sep", "22–28 Sep", "29–30 Sep"]
    assert sum(w["profit_paisa"] for w in report["weeks"]) == report["figures"]["cash_profit_paisa"]
    assert set(report["security"]) == {"blocked", "held_for_you", "alerts", "wrong_pin", "stop_used"}
    assert report["integrity"]["audit"]["verified"] and report["integrity"]["provider"]["reachable"]
    made = [e for e in app.audit("report")]
    assert made and made[-1]["summary"] == "Monthly statement made for you: 1–30 Sep 2026"
    feed = app.http.get("/api/notifications").json()
    cash = next(s for s in feed["segments"] if s["id"] == "cash")
    assert any(i["title"].startswith("Monthly statement made for you") for i in cash["items"])


def test_the_lender_statement_has_six_months_and_no_customer_names(app):
    report = _report(app, "lender")
    assert len(report["months"]) == 6 and report["months"][-1]["label"] == "Sep 2026"
    assert report["period"] == "Apr 2026–Sep 2026"
    text = json.dumps(report, ensure_ascii=False)
    debtors = [d["name"] for d in app.http.get("/api/debtors").json()]
    assert debtors and not any(name in text for name in debtors)
    assert report["averages"]["sales_paisa"] % 100 == 0  # whole taka, never paisa
    assert report["credit_profile"]["included"] is False and "credit profile" in report["credit_profile"]["why"]
    assert report["declaration"].startswith("I, ") and report["capacity"]["text"]
    assert len(report["integrity"]["fingerprint"]) == 64
    # the same books give the same fingerprint, so a lender can ask for it again and compare
    assert _report(app, "lender")["integrity"]["fingerprint"] == report["integrity"]["fingerprint"]
    app.chat("sold rice 1,500 cash")
    assert _report(app, "owner", "2026-10")["integrity"]["fingerprint"] != report["integrity"]["fingerprint"]


def test_a_month_without_books_is_refused(app):
    assert app.http.get("/api/reports/monthly", params={"kind": "owner", "month": "2020-01"}).status_code == 404
    assert app.http.get("/api/reports/monthly", params={"kind": "owner", "month": "2027-01"}).status_code == 404
    assert app.http.get("/api/reports/monthly", params={"kind": "owner", "month": "Sept"}).status_code == 422
