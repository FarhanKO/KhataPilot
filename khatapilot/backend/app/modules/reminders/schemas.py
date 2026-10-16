from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

REMINDER_KIND = "message.send"  # the provider authorization a reminder needs
Language = Literal["english", "banglish"]
Outcome = Literal["paid", "part_paid", "waiting", "not_sent"]


class ReminderDraft(BaseModel):
    """One reminder as it will be sent. The guard binds the body's hash, so the owner approves exactly this."""

    customer_name: str
    to_number: str
    owed_paisa: int
    days_outstanding: int
    open_since: date | None
    language: Language
    from_account: str
    channel: Literal["sms"] = "sms"
    body: str
    body_sha256: str


class Skipped(BaseModel):
    name: str
    why: str


class ReminderPlan(BaseModel):
    drafts: list[ReminderDraft]
    skipped: list[Skipped]

    @property
    def total_paisa(self) -> int:
        return sum(d.owed_paisa for d in self.drafts)


class ReminderView(BaseModel):
    id: str
    customer_name: str
    to_masked: str
    owed_paisa: int  # what they owed when reminded
    days_outstanding: int
    body: str
    status: str  # sent | failed
    sandbox_ref: str | None
    failure: str | None
    sent_at: datetime
    decision_id: str
    paid_since_paisa: int
    still_owes_paisa: int
    outcome: Outcome


class NothingToRemind(Exception):
    """No customer fits the request (nobody owes that long, an unknown name, no number on file)."""


class SendRefused(Exception):
    """The authorization does not match the reminder, or no messaging gateway is configured."""
