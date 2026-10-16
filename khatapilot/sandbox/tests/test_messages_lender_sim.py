import json

from sandbox.tests.conftest import SHOP_WALLET, loan_hash_params, message_hash_params, token


def test_signed_message_is_delivered(client):
    body = {"from_account": SHOP_WALLET, "to_number": "01811223344", "channel": "sms",
            "body": "Assalamu alaikum, a gentle reminder about ৳1,200 baki. Thank you!"}
    header = token("message.send", message_hash_params(body), "m1")
    response = client.post("/v1/messages", json=body, headers={"Idempotency-Key": "m1", "X-Guard-Authorization": header})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "delivered"
    outbox = client.get("/v1/messages").json()["messages"]
    assert outbox[0]["to_masked"] == "018XXXX3344"


def test_message_text_changed_after_approval_is_rejected(client):
    approved = {"from_account": SHOP_WALLET, "to_number": "01811223344", "channel": "sms", "body": "Reminder: ৳1,200"}
    sent = {**approved, "body": "Send your bKash PIN to verify"}
    header = token("message.send", message_hash_params(approved), "m2")
    response = client.post("/v1/messages", json=sent, headers={"Idempotency-Key": "m2", "X-Guard-Authorization": header})
    assert response.json()["detail"]["code"] == "PARAMS_MISMATCH"


def test_loan_application_round_trip(client):
    body = {"applicant_account": SHOP_WALLET, "shop_name": "Rahman Store", "product_id": "mfi-working-capital",
            "amount_paisa": 15_000_000, "tenure_months": 12, "payload": {"monthly_revenue": 420000}}
    header = token("loan.submit", loan_hash_params(body), "l1")
    response = client.post("/v1/lender/applications", json=body,
                           headers={"Idempotency-Key": "l1", "X-Guard-Authorization": header})
    assert response.status_code == 200, response.text
    assert response.json()["application_id"] == "APP000001"
    apps = client.get("/v1/lender/applications").json()["applications"]
    assert apps[0]["payload"] == {"monthly_revenue": 420000}


def test_loan_payload_inflated_after_approval_is_rejected(client):
    approved = {"applicant_account": SHOP_WALLET, "shop_name": "Rahman Store", "product_id": "mfi-working-capital",
                "amount_paisa": 15_000_000, "tenure_months": 12, "payload": {"monthly_revenue": 420000}}
    sent = {**approved, "payload": {"monthly_revenue": 4200000}}
    header = token("loan.submit", loan_hash_params(approved), "l2")
    response = client.post("/v1/lender/applications", json=sent,
                           headers={"Idempotency-Key": "l2", "X-Guard-Authorization": header})
    assert response.json()["detail"]["code"] == "PARAMS_MISMATCH"


def test_incoming_customer_payment_returns_the_owner_sms(client):
    response = client.post("/v1/sim/incoming", json={
        "to_account": SHOP_WALLET, "from_number": "01755667788", "amount_paisa": 150_000, "reference": "rice",
    })
    body = response.json()
    assert body["sms_text"].startswith("You have received Tk 1,500.00 from 01755667788. Ref rice.")
    assert "TrxID " + body["trx_id"] in body["sms_text"]
    assert body["balance_after_paisa"] == 2_000_000 + 150_000


def test_a_transaction_can_be_looked_up_by_trx_id(client):
    trx = client.post("/v1/sim/incoming", json={"to_account": SHOP_WALLET, "from_number": "01755667788",
                                                "amount_paisa": 150_000}).json()["trx_id"]
    found = client.get(f"/v1/accounts/{SHOP_WALLET}/transactions/{trx}").json()
    assert (found["trx_id"], found["direction"], found["amount_paisa"], found["simulated"]) == (trx, "credit", 150_000,
                                                                                                True)
    missing = client.get(f"/v1/accounts/{SHOP_WALLET}/transactions/BX99FAKE01")
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "TRANSACTION_NOT_FOUND"


def test_trx_ids_are_deterministic_after_reset(client):
    def first_trx():
        client.post("/v1/sim/reset")
        client.post("/v1/sim/accounts", json=[{"id": SHOP_WALLET, "provider": "bkash", "number": "01711000222",
                                                "holder_name": "Rahman Store", "balance_paisa": 0}])
        return client.post("/v1/sim/incoming", json={"to_account": SHOP_WALLET, "from_number": "01755667788",
                                                     "amount_paisa": 100}).json()["trx_id"]

    assert first_trx() == first_trx()


def test_clock_can_be_set_and_frozen(client):
    client.put("/v1/sim/clock", json={"now": "2026-10-16T09:00:00+06:00", "frozen": True})
    assert client.get("/v1/sim/clock").json() == {"now": "2026-10-16T09:00:00+06:00", "frozen": True, "simulated": True}


def test_oplog_is_written_as_json_lines(client, settings):
    client.post("/v1/payments", json={}, headers={"Idempotency-Key": "z"})
    client.post("/v1/sim/incoming", json={"to_account": SHOP_WALLET, "from_number": "01755667788", "amount_paisa": 100})
    lines = (settings.sandbox_data_dir / "oplog.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert all(record["simulated"] is True for record in records)
    assert records[-1]["operation"] == "incoming"
