"""Proposed and authorized actions, and the guard-to-provider hashing spec.

The hashing spec here must match the provider's (`sandbox/auth.py`); a contract test checks both
produce the same hash for the same request.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ActionKind(StrEnum):
    READ = "read"  # tier 0: look at the shop's own data
    LEDGER_WRITE = "ledger.write"  # tier 0: the owner's own books
    NOTIFY_OWNER = "notify_owner"  # tier 0: tell the owner something
    PAYMENT = "payment"  # tier 2 within limits, tier 3 otherwise
    MESSAGE = "message.send"  # tier 1: anything sent to a third party
    LOAN_SUBMIT = "loan.submit"  # tier 1


# Provider-side authorization kinds and exactly the fields each one binds.
AUTH_HASH_FIELDS: dict[str, tuple[str, ...]] = {
    "payment.execute": ("from_account", "to_number", "amount_paisa", "method"),
    "message.send": ("from_account", "to_number", "channel", "body_sha256"),
    "loan.submit": ("applicant_account", "product_id", "amount_paisa", "tenure_months", "payload_sha256"),
    "account.freeze": ("account_id",),
    "account.unfreeze": ("account_id",),
}


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def params_hash(kind: str, params: dict[str, Any]) -> str:
    fields = AUTH_HASH_FIELDS[kind]
    return sha256_hex(canonical_json({field: params[field] for field in fields}))


PayMethod = Literal["bkash", "nagad", "bank"]


class PaymentRequest(BaseModel):
    """A payment as proposed by the agent. Nothing here is trusted until the guard checks it."""

    model_config = ConfigDict(frozen=True)

    payee_name: str | None = Field(default=None, max_length=128)
    payee_number: str | None = Field(default=None, max_length=32)
    amount_paisa: int = Field(gt=0)
    method: PayMethod | None = None  # None: the trusted payee's saved method, else bKash
    execute_on: date | None = None  # None means now
    note: str | None = Field(default=None, max_length=200)


class ProposedAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    action_id: str
    tool: str
    kind: ActionKind
    summary: str  # plain English, numbers masked
    payment: PaymentRequest | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class AuthorizedAction(BaseModel):
    """A guard authorization for one side effect. Minted only by `app.guard.capability`."""

    model_config = ConfigDict(frozen=True)

    token_id: str
    kind: str  # provider authorization kind, e.g. "payment.execute"
    params_hash: str
    idempotency_key: str
    tier: int
    decision_id: str
    issued_at: int
    expires_at: int
    header: str  # "v1.<payload>.<signature>", sent to the provider

    def binds(self, kind: str, params: dict[str, Any]) -> bool:
        return self.kind == kind and self.params_hash == params_hash(kind, params)
