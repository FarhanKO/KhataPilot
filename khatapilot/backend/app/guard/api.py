"""The agent's only window into the rest of the system.

The agent layer may import this module and nothing else from the guard, the modules or the
infrastructure (`backend/tests/architecture/test_layers.py`). It gets a `ToolPort`: the list of
tools it may propose, and a way to submit a proposal. Every decision is the guard's.
"""

from __future__ import annotations

from typing import Protocol

from app.core.provenance import TurnContext
from app.guard.gateway import ToolCall, ToolDescriptor, ToolOutcome
from app.guard.injection import InjectionReport, scan


class ToolPort(Protocol):
    def descriptors(self) -> list[ToolDescriptor]: ...

    def submit(self, call: ToolCall, turn: TurnContext) -> ToolOutcome: ...


__all__ = ["InjectionReport", "ToolCall", "ToolDescriptor", "ToolOutcome", "ToolPort", "scan"]
