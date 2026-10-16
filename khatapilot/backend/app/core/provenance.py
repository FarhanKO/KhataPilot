"""Where text came from. The channel adapter labels every piece of text before the agent sees it,
and the guard trusts these labels, never anything the LLM says about provenance."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Provenance(StrEnum):
    OWNER = "owner"  # typed by the authenticated owner
    UNTRUSTED = "untrusted"  # forwarded SMS, invoice/OCR text, anything from someone who is not the owner
    SYSTEM = "system"  # produced by KhataPilot itself


class SenderKind(StrEnum):
    OWNER = "owner"
    EXTERNAL = "external"  # a supplier, a customer, a stranger
    SYSTEM = "system"  # the scheduler, the seed, a background job


class Segment(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    provenance: Provenance
    source: str  # chat | forwarded_sms | invoice_text | inbound_message | ocr | system


class Sender(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: SenderKind
    display: str  # already masked, e.g. "Owner (web)" or "018XXXX4455"


class TurnContext(BaseModel):
    """Everything the guard knows about the turn that led to a proposed action."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    conversation_id: str
    shop_id: str
    channel: str  # web | messenger | simulator | scheduler
    sender: Sender
    segments: tuple[Segment, ...]
    received_at: datetime

    @property
    def is_owner_turn(self) -> bool:
        return self.sender.kind is SenderKind.OWNER

    @property
    def owner_text(self) -> str:
        return "\n".join(s.text for s in self.segments if s.provenance is Provenance.OWNER)

    @property
    def untrusted_text(self) -> str:
        return "\n".join(s.text for s in self.segments if s.provenance is Provenance.UNTRUSTED)

    @property
    def provenance(self) -> Provenance:
        """The provenance of the *instruction*: only an owner's turn carries owner authority."""
        if self.sender.kind is SenderKind.OWNER:
            return Provenance.OWNER
        if self.sender.kind is SenderKind.SYSTEM:
            return Provenance.SYSTEM
        return Provenance.UNTRUSTED
