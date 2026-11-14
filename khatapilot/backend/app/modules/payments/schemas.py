from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.money import fmt_taka

PAYMENT_KIND = "payment.execute"


class SmsCheck(BaseModel):
    """Whether the provider confirms a forwarded SMS, and why not when it does not."""

    confirmed: bool
    reason: str
    # confirmed | unknown (not on the statement) | mismatch (on it, but different) | unreachable
    status: str = "confirmed"


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
    purpose: str = "payment"
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


class AccountMatch(BaseModel):
    """One provider account: what the books say is in it against what the provider says."""

    method: str
    account_id: str
    books_paisa: int
    provider_paisa: int
    unbooked: list[str]  # recent provider transactions the books do not have, in words

    @property
    def difference_paisa(self) -> int:
        return self.provider_paisa - self.books_paisa


class ProviderMatch(BaseModel):
    """The nightly match of the books against the provider."""

    reachable: bool
    accounts: list[AccountMatch] = []
    missing_at_provider: list[str] = []  # KhataPilot payments whose TrxID the provider does not have
    stuck: list[str] = []  # payments still "executing" long after they started

    @property
    def problems(self) -> list[str]:
        out = []
        names = {"bkash": "bKash", "nagad": "Nagad", "bank": "bank"}
        for a in self.accounts:
            if a.difference_paisa:
                name = names.get(a.method, a.method)
                way = "more" if a.difference_paisa > 0 else "less"
                line = (f"Your {name} books show {fmt_taka(a.books_paisa)} but {name} shows "
                        f"{fmt_taka(a.provider_paisa)}: {fmt_taka(abs(a.difference_paisa))} {way} than the books.")
                if a.unbooked:
                    line += " Not in the books: " + "; ".join(a.unbooked) + "."
                out.append(line)
        out += [f"Paid through KhataPilot but not at the provider: {m}." for m in self.missing_at_provider]
        out += [f"Still not confirmed after 15 minutes: {s}. Check your wallet app before paying again."
                for s in self.stuck]
        return out


class ExecutionRefused(Exception):
    """The module refused to call the provider (the authorization does not bind this payment)."""
