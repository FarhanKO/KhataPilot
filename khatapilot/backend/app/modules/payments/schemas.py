from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

PAYMENT_KIND = "payment.execute"


class SmsCheck(BaseModel):
    """Whether the provider confirms a forwarded SMS, and why not when it does not."""

    confirmed: bool
    reason: str


class PaymentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    payee_name: str | None
    payee_number: str
    method: str
    amount_paisa: int
    execute_on: date
    status: str
    decision_id: str
    approved_tier: int
    approved_with_pin: bool
    idempotency_key: str
    note: str | None
    sandbox_txn_id: str | None
    failure: str | None
    created_at: datetime
    executed_at: datetime | None


class PaymentCommand(BaseModel):
    """Exactly what is sent to the provider. `hash_params()` is what the guard's token binds."""

    model_config = ConfigDict(frozen=True)

    from_account: str
    to_number: str
    to_name: str | None
    amount_paisa: int
    method: str
    reference: str | None

    def payload(self) -> dict[str, Any]:
        return self.model_dump()

    def hash_params(self) -> dict[str, Any]:
        return self.model_dump()


class ExecutionRefused(Exception):
    """The module refused to call the provider (the authorization does not bind this payment)."""
