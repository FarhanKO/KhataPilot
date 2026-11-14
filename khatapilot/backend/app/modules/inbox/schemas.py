from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["booked", "asked", "review", "duplicate", "fake_sender", "not_booked", "ignored"]
Source = Literal["webhook", "pull", "import", "share"]


class IncomingSms(BaseModel):
    """One SMS as the phone (or an import) delivered it, before anything is decided about it."""

    sender: str = Field(default="", max_length=64)
    body: str = Field(max_length=2_000)
    received_at: datetime | None = None
    source: Source = "webhook"


class PhoneSmsView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sender: str
    sender_kind: str
    source: str
    body: str
    received_at: datetime | None
    seen_at: datetime
    provider: str | None
    trx_id: str | None
    direction: str | None
    amount_paisa: int | None
    status: Status
    detail: str
    question_id: str | None
