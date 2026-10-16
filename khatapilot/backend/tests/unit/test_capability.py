"""The guard's tokens and the provider's verifier implement the same protocol."""

import time

import pytest

from app.core.actions import AUTH_HASH_FIELDS, params_hash
from app.guard.capability import CapabilitySigner
from sandbox import auth as provider

KEY = b"k" * 48
PAYMENT = {"from_account": "bkash:01711000222", "to_number": "01712345678", "to_name": "Rahim Traders",
           "amount_paisa": 800_000, "method": "bkash", "reference": None}


def test_backend_and_provider_hash_the_same_fields():
    assert AUTH_HASH_FIELDS == provider.HASH_FIELDS
    samples = {
        "payment.execute": PAYMENT,
        "message.send": {"from_account": "bkash:01711000222", "to_number": "01811223344", "channel": "sms",
                         "body_sha256": "ab" * 32},
        "loan.submit": {"applicant_account": "bkash:01711000222", "product_id": "mfi", "amount_paisa": 1,
                        "tenure_months": 12, "payload_sha256": "cd" * 32},
        "account.freeze": {"account_id": "bkash:01711000222"},
        "account.unfreeze": {"account_id": "bkash:01711000222"},
    }
    for kind, params in samples.items():
        assert params_hash(kind, params) == provider.params_hash(kind, params), kind


def test_a_minted_token_verifies_at_the_provider():
    token = CapabilitySigner(KEY).mint(kind="payment.execute", params=PAYMENT, idempotency_key="idem-1", tier=2,
                                       decision_id="dec-1")
    verified = provider.verify(token.header, key=KEY, kind="payment.execute", params=PAYMENT,
                               idempotency_key="idem-1", now=time.time())
    assert verified.token_id == token.token_id
    assert verified.decision_id == "dec-1"
    assert token.binds("payment.execute", PAYMENT)


def test_the_token_binds_the_amount():
    token = CapabilitySigner(KEY).mint(kind="payment.execute", params=PAYMENT, idempotency_key="idem-1", tier=2,
                                       decision_id="dec-1")
    tampered = {**PAYMENT, "amount_paisa": 8_000_000}
    assert not token.binds("payment.execute", tampered)
    with pytest.raises(provider.AuthError) as error:
        provider.verify(token.header, key=KEY, kind="payment.execute", params=tampered, idempotency_key="idem-1",
                        now=time.time())
    assert error.value.code == "PARAMS_MISMATCH"


def test_tokens_expire():
    token = CapabilitySigner(KEY, ttl_seconds=300, wall_clock=lambda: 1_000_000).mint(
        kind="account.freeze", params={"account_id": "a"}, idempotency_key="i", tier=0, decision_id="d")
    verified = provider.verify(token.header, key=KEY, kind="account.freeze", params={"account_id": "a"},
                               idempotency_key="i", now=1_000_100)
    provider.check_not_expired(verified, 1_000_299)
    with pytest.raises(provider.AuthError, match="expired"):
        provider.check_not_expired(verified, 1_000_301)


def test_a_signer_without_a_real_key_refuses_to_start():
    with pytest.raises(RuntimeError, match="GUARD_SIGNING_KEY"):
        CapabilitySigner(b"short")
