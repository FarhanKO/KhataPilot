"""Money in from a customer who owes baki is not booked on a guess: the chat asks what it is for, and the
owner's tap books it (against the baki, or as a sale). Seeded demo shop, mock LLM."""

BABUL = "01933445566"  # Babul Mia, who owes ৳7,541


def forward_payment(app, amount: int, owner: str = "record this", conversation_id: str | None = None) -> dict:
    sms = app.http.post("/api/sim/incoming-payment", json={"from_number": BABUL, "amount_taka": amount,
                                                           "from_name": "Babul Mia"}).json()
    return app.chat(owner, forwarded=[{"source": "forwarded_sms", "text": sms["sms_text"]}],
                    conversation_id=conversation_id)


def babul_owes(app) -> int:
    return next(d["outstanding_paisa"] for d in app.http.get("/api/debtors").json() if d["name"] == "Babul Mia")


def test_the_chat_asks_and_the_owners_tap_books_it_against_the_baki(app):
    before = app.http.get("/api/ledger/entries", params={"limit": 1}).json()["total"]
    out = forward_payment(app, 2000)
    assert "৳2,000 came in from Babul Mia, who owes ৳7,541 of baki" in out["reply"]
    assert "Tap one of the two buttons" in out["reply"]
    [asked] = [o for o in out["outcomes"] if o["status"] == "asked"]
    assert app.http.get("/api/ledger/entries", params={"limit": 1}).json()["total"] == before, "nothing booked yet"
    [question] = [q for q in app.http.get("/api/sms-questions").json() if q["id"] == asked["data"]["question_id"]]
    assert question["status"] == "open"

    done = app.http.post(f"/api/sms-questions/{question['id']}/answer", json={"purpose": "baki"})
    assert done.status_code == 200 and "They still owe ৳5,541" in done.json()["message"]
    assert babul_owes(app) == 554_100
    history = app.http.get(f"/api/chat/conversations/{out['conversation_id']}").json()
    assert "Update from the app: you answered the SMS question." in history[-1]["text"]
    again = app.http.post(f"/api/sms-questions/{question['id']}/answer", json={"purpose": "other"})
    assert again.status_code == 409


def test_something_else_is_booked_as_a_sale_and_the_baki_stays(app):
    out = forward_payment(app, 300)
    [asked] = [o for o in out["outcomes"] if o["status"] == "asked"]
    done = app.http.post(f"/api/sms-questions/{asked['data']['question_id']}/answer", json={"purpose": "other"})
    assert "as a sale" in done.json()["message"] and babul_owes(app) == 754_100


def test_saying_it_is_for_the_baki_up_front_skips_the_question(app):
    out = forward_payment(app, 1000, owner="Babul paid back some baki, record this")
    assert not [o for o in out["outcomes"] if o["status"] == "asked"]
    assert "as baki repayment" in out["reply"] and babul_owes(app) == 654_100
