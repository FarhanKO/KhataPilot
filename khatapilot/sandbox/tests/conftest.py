from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from sandbox.app import create_app
from sandbox.auth import canonical_json, encode_token, params_hash, sha256_hex
from sandbox.config import SandboxSettings

SIGNING_KEY = "k" * 48
ADMIN_KEY = "a" * 48
BDT = timezone(timedelta(hours=6))
SHOP_WALLET = "bkash:01711000222"
SUPPLIER = "01712345678"


@pytest.fixture
def settings(tmp_path) -> SandboxSettings:
    return SandboxSettings(
        _env_file=None,
        sandbox_data_dir=tmp_path / "sandbox",
        guard_signing_key=SecretStr(SIGNING_KEY),
        sandbox_admin_key=SecretStr(ADMIN_KEY),
        sim_time=datetime(2026, 10, 12, 10, 0, tzinfo=BDT),
    )


@pytest.fixture
def client(settings) -> TestClient:
    with TestClient(create_app(settings)) as test_client:
        test_client.headers.update({"X-Admin-Key": ADMIN_KEY})
        test_client.post(
            "/v1/sim/accounts",
            json=[
                {"id": SHOP_WALLET, "provider": "bkash", "number": "01711000222",
                 "holder_name": "Rahman Store", "kind": "merchant", "balance_paisa": 2_000_000},
                {"id": f"bkash:{SUPPLIER}", "provider": "bkash", "number": SUPPLIER,
                 "holder_name": "Rahim Traders", "kind": "merchant", "balance_paisa": 0},
            ],
        )
        yield test_client


def token(kind: str, params: dict[str, Any], idempotency_key: str, *, key: str = SIGNING_KEY,
          ttl: int = 300, issued_at: int | None = None) -> str:
    now = int(time.time()) if issued_at is None else issued_at
    claims = {
        "tid": f"tok_{uuid.uuid4().hex[:12]}",
        "kind": kind,
        "ph": params_hash(kind, params),
        "idk": idempotency_key,
        "tier": 2,
        "did": "dec_test",
        "iat": now,
        "exp": now + ttl,
    }
    return encode_token(key.encode(), claims)


def payment_body(amount_paisa: int = 800_000, to_number: str = SUPPLIER) -> dict[str, Any]:
    return {
        "from_account": SHOP_WALLET,
        "to_number": to_number,
        "to_name": "Rahim Traders",
        "amount_paisa": amount_paisa,
        "method": "bkash",
        "reference": "Rice restock",
    }


def pay(client: TestClient, body: dict[str, Any], *, idempotency_key: str | None = None,
        auth_for: dict[str, Any] | None = None, **token_kwargs):
    key = idempotency_key or f"idem_{uuid.uuid4().hex[:10]}"
    header = token("payment.execute", auth_for or body, key, **token_kwargs)
    return client.post(
        "/v1/payments", json=body, headers={"Idempotency-Key": key, "X-Guard-Authorization": header}
    )


def message_hash_params(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "body_sha256": sha256_hex(body["body"])}


def loan_hash_params(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "payload_sha256": sha256_hex(canonical_json(body["payload"]))}
