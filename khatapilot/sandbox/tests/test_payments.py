"""The provider refuses every payment the guard did not sign for exactly these parameters."""

import time

from sandbox.tests.conftest import SHOP_WALLET, SUPPLIER, pay, payment_body, token


def balance(client, account_id=SHOP_WALLET) -> int:
    return client.get(f"/v1/accounts/{account_id}").json()["balance_paisa"]


def last_oplog(client) -> dict:
    return client.get("/v1/oplog").json()["entries"][-1]


def test_signed_payment_executes(client):
    response = pay(client, payment_body())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["simulated"] is True
    assert body["status"] == "completed"
    assert body["to_number_masked"] == "017XXXX5678"
    assert body["sms_text"].startswith("Payment Tk 8,000.00 to Rahim Traders")
    assert balance(client) == 2_000_000 - 800_000
    assert balance(client, f"bkash:{SUPPLIER}") == 800_000
    entry = last_oplog(client)
    assert (entry["operation"], entry["status"], entry["code"]) == ("payment", "ok", "OK")


def test_unsigned_payment_is_rejected_and_logged(client):
    response = client.post("/v1/payments", json=payment_body(), headers={"Idempotency-Key": "idem-1"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "MISSING_AUTHORIZATION"
    assert balance(client) == 2_000_000
    entry = last_oplog(client)
    assert entry["code"] == "REJECTED_MISSING_AUTHORIZATION"
    assert entry["counterparty"] == "017XXXX5678"


def test_amount_tampered_after_approval_is_rejected(client):
    approved = payment_body(800_000)
    response = pay(client, payment_body(8_000_000), auth_for=approved)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "PARAMS_MISMATCH"
    assert balance(client) == 2_000_000


def test_payee_swapped_after_approval_is_rejected(client):
    approved = payment_body()
    response = pay(client, payment_body(to_number="01999888777"), auth_for=approved)
    assert response.json()["detail"]["code"] == "PARAMS_MISMATCH"


def test_signature_with_the_wrong_key_is_rejected(client):
    response = pay(client, payment_body(), key="x" * 48)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "BAD_SIGNATURE"


def test_expired_authorization_is_rejected(client):
    response = pay(client, payment_body(), issued_at=int(time.time()) - 600, ttl=300)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "EXPIRED"
    assert balance(client) == 2_000_000


def test_authorization_for_another_action_kind_is_rejected(client):
    body = payment_body()
    header = token("account.unfreeze", {"account_id": SHOP_WALLET}, "idem-k")
    response = client.post(
        "/v1/payments", json=body, headers={"Idempotency-Key": "idem-k", "X-Guard-Authorization": header}
    )
    assert response.json()["detail"]["code"] == "WRONG_KIND"


def test_retry_with_the_same_idempotency_key_pays_once(client):
    body = payment_body()
    first = pay(client, body, idempotency_key="idem-retry")
    second = pay(client, body, idempotency_key="idem-retry")
    assert first.status_code == second.status_code == 200
    assert second.json()["idempotent_replay"] is True
    assert second.json()["transaction_id"] == first.json()["transaction_id"]
    assert balance(client) == 2_000_000 - 800_000
    assert last_oplog(client)["code"] == "IDEMPOTENT_REPLAY"


def test_reusing_an_idempotency_key_for_a_different_payment_conflicts(client):
    pay(client, payment_body(800_000), idempotency_key="idem-x")
    response = pay(client, payment_body(900_000), idempotency_key="idem-x")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert balance(client) == 2_000_000 - 800_000


def test_insufficient_funds(client):
    response = pay(client, payment_body(5_000_000))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INSUFFICIENT_FUNDS"


def test_invalid_wallet_number(client):
    response = pay(client, payment_body(to_number="12345"))
    assert response.json()["detail"]["code"] == "INVALID_NUMBER"


def test_frozen_account_refuses_payments_until_unfrozen(client):
    freeze = client.post(
        f"/v1/accounts/{SHOP_WALLET}/freeze",
        json={"reason": "Owner pressed STOP"},
        headers={"Idempotency-Key": "f1", "X-Guard-Authorization": token("account.freeze", {"account_id": SHOP_WALLET}, "f1")},
    )
    assert freeze.json()["status"] == "frozen"

    blocked = pay(client, payment_body())
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "ACCOUNT_FROZEN"

    unfreeze_unsigned = client.post(f"/v1/accounts/{SHOP_WALLET}/unfreeze", headers={"Idempotency-Key": "u0"})
    assert unfreeze_unsigned.status_code == 401

    client.post(
        f"/v1/accounts/{SHOP_WALLET}/unfreeze",
        headers={"Idempotency-Key": "u1", "X-Guard-Authorization": token("account.unfreeze", {"account_id": SHOP_WALLET}, "u1")},
    )
    assert pay(client, payment_body()).status_code == 200


def test_statement_lists_both_legs(client):
    pay(client, payment_body())
    shop = client.get(f"/v1/accounts/{SHOP_WALLET}/transactions").json()["transactions"]
    supplier = client.get(f"/v1/accounts/bkash:{SUPPLIER}/transactions").json()["transactions"]
    assert shop[0]["direction"] == "debit"
    assert supplier[0]["direction"] == "credit"
    assert shop[0]["trx_id"] == supplier[0]["trx_id"]
