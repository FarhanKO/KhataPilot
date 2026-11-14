"""Provider-neutral shapes the orchestrator works with. Each provider converts to and from its own
SDK types internally."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol

from app.guard.api import ToolOutcome


# Told about every call to the model as it returns: (the model that answered, its token usage). The app
# uses it to meter each shop's monthly spend; a broken listener never breaks the call.
UsageListener = Callable[[str, dict[str, int]], None]


def report_usage(listener: UsageListener | None, model: str, usage: dict[str, int]) -> None:
    if listener is None:
        return
    try:
        listener(model, usage)
    except Exception:  # metering must never break a turn
        import logging

        logging.getLogger(__name__).exception("usage listener failed")


class LLMError(Exception):
    """The model could not be reached or answered with an error. The message is safe to show the owner.

    `transient` marks a failure that says nothing about the setup (no network, a timeout, the service
    busy or down), so the offline planner may answer instead. A bad key or model name is not transient:
    falling back would only hide a mistake the owner has to fix."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass(frozen=True)
class LLMTool:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class LLMToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMStep:
    text: str
    tool_calls: list[LLMToolCall]
    stop_reason: str  # end_turn | tool_use | max_tokens | refusal
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    refusal_category: str | None = None


@dataclass(frozen=True)
class HistoryMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class SegmentBrief:
    index: int
    source: str
    text: str
    kind: str  # owner_text | mobile_money_sms | invoice | message | unknown
    parsed: dict[str, Any] | None
    suspected_injection: bool


@dataclass(frozen=True)
class TurnBrief:
    """A structured view of the turn. Only the deterministic mock reads it; real models get the
    rendered text, exactly like any other user message."""

    sender: Literal["owner", "external"]
    sender_display: str
    owner_text: str
    segments: tuple[SegmentBrief, ...]
    today: date
    now: datetime


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str  # JSON handed back to the model
    is_error: bool
    outcome: ToolOutcome


class LLMSession(Protocol):
    """One turn's conversation with the model. Append-only: earlier messages are never edited."""

    def user(self, content: str, brief: TurnBrief) -> None: ...

    def step(self) -> LLMStep: ...

    def tool_results(self, results: list[ToolResult]) -> None: ...


class LLMProvider(Protocol):
    name: str
    model: str

    def session(self, *, system: str, tools: list[LLMTool], history: list[HistoryMessage]) -> LLMSession: ...

    def extract(self, *, system: str, text: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        """Structured extraction with NO tools: used to read untrusted documents as data."""
        ...

    def extract_image(self, *, system: str, image: bytes, media_type: str,
                      schema: dict[str, Any]) -> dict[str, Any] | None:
        """The same, for a photo (an invoice the owner took a picture of)."""
        ...
