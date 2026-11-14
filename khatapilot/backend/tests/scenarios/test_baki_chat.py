"""What customers owe the shop, managed from the chat on the seeded demo shop (mock LLM): add baki (to a new
or an existing customer), read it, change it (a part payment, a correction) and remove it. Money a customer
who owes baki gives the shop is not booked on a guess: the chat asks whether it pays off the baki or is for
something else, exactly as for a forwarded SMS."""


def owes(app, name: str) -> int:
    return next((d["outstanding_paisa"] for d in app.http.get("/api/debtors").json() if d["name"] == name), 0)


def entries_total(app) -> int:
    return app.http.get("/api/ledger/entries", params={"limit": 1}).json()["total"]


def asked(out: dict) -> dict:
    [question] = [o for o in out["outcomes"] if o["status"] == "asked"]
    return question["data"]


def test_create_read_update_delete_a_customers_baki(app):
    # Create: a new customer
    out = app.chat("Karim Mia ke 600 taka baki dilam")
    assert "Recorded baki sale ৳600" in out["reply"] and "Karim Mia now owes ৳600" in out["reply"]
    # Read: one customer
    assert "Karim Mia owes you ৳600 (given today)" in app.chat("how much does Karim Mia owe?")["reply"]
    # Update: more baki on the same customer, then a part payment, then a correction
    app.chat("Karim Mia ke 400 taka baki dilam")
    assert owes(app, "Karim Mia") == 100_000
    out = app.chat("Karim Mia paid back 300")
    assert "against their baki" in out["reply"] and "Karim Mia still owes ৳700" in out["reply"]
    assert "Corrected Karim Mia's baki from ৳700 to ৳500" in app.chat("Karim Mia owes 500 not 700")["reply"]
    assert owes(app, "Karim Mia") == 50_000
    # Delete
    assert "They owe nothing now" in app.chat("remove Karim Mia from debtors")["reply"]
    assert owes(app, "Karim Mia") == 0
    assert "Karim Mia does not owe you anything" in app.chat("how much does Karim Mia owe?")["reply"]


def test_a_short_name_goes_on_the_existing_customers_account(app):
    before = owes(app, "Babul Mia")
    app.chat("Babul ke 500 taka baki dilam")
    assert owes(app, "Babul Mia") == before + 50_000
    assert not owes(app, "Babul"), "no second customer called Babul"


def test_money_typed_in_from_a_debtor_is_asked_about_and_the_tap_books_it_against_the_baki(app):
    before, total = owes(app, "Babul Mia"), entries_total(app)
    out = app.chat("Babul Mia sent 2000 on bkash")
    question = asked(out)
    assert "৳2,000 came in from Babul Mia, who owes ৳7,541 of baki" in out["reply"]
    assert entries_total(app) == total and owes(app, "Babul Mia") == before, "nothing booked yet"
    [kept] = [q for q in app.http.get("/api/sms-questions").json() if q["id"] == question["question_id"]]
    assert kept["source"] == "typed" and kept["status"] == "open"

    done = app.http.post(f"/api/sms-questions/{question['question_id']}/answer", json={"purpose": "baki"})
    assert done.status_code == 200 and "They still owe ৳5,541" in done.json()["message"]
    assert owes(app, "Babul Mia") == before - 200_000
    [entry] = app.http.get("/api/ledger/entries", params={"limit": 1}).json()["entries"]
    assert entry["kind"] == "baki_repayment" and entry["method"] == "bkash"
    history = app.http.get(f"/api/chat/conversations/{out['conversation_id']}").json()
    assert "you answered the question about money from Babul Mia" in history[-1]["text"]
    again = app.http.post(f"/api/sms-questions/{question['question_id']}/answer", json={"purpose": "other"})
    assert again.status_code == 409


def test_money_for_something_else_is_a_sale_and_the_baki_stays(app):
    before = owes(app, "Babul Mia")
    question = asked(app.chat("got 300 from Babul Mia"))
    done = app.http.post(f"/api/sms-questions/{question['question_id']}/answer", json={"purpose": "other"})
    assert "as a sale" in done.json()["message"] and owes(app, "Babul Mia") == before


def test_saying_what_it_is_for_skips_the_question(app):
    before = owes(app, "Babul Mia")
    out = app.chat("Babul paid 300 advance for a new order")
    assert not [o for o in out["outcomes"] if o["status"] == "asked"]
    assert "as a sale" in out["reply"] and owes(app, "Babul Mia") == before


def test_paying_more_than_the_baki_books_the_rest_as_a_sale(app):
    app.chat("Karim Mia ke 400 taka baki dilam")
    out = app.chat("Karim Mia paid back 1000")
    assert "৳600 of it was more than they owed" in out["reply"] and owes(app, "Karim Mia") == 0


def test_money_from_someone_who_owes_nothing_is_a_sale_without_a_question(app):
    out = app.chat("Nasima Begum paid 400")
    assert not [o for o in out["outcomes"] if o["status"] == "asked"]
    assert "Recorded ৳400 from Nasima Begum as a sale" in out["reply"]
