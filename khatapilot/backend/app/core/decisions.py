"""The guard's verdicts. One `Decision` feeds the audit log, the decision-trace card and the tests."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.core.actions import ProposedAction


class CheckStatus(StrEnum):
    PASS = "pass"
    ESCALATE = "escalate"  # raises the tier; the action needs more from the owner
    BLOCK = "block"
    SKIP = "skip"  # not applicable to this action
    INFO = "info"


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str  # provenance | kill_switch | pin_lock | injection | intent | payee | limits | velocity | fraud
    label: str
    status: CheckStatus
    detail: str
    tier: int | None = None  # the tier this check requires when it escalates


class Outcome(StrEnum):
    ALLOW = "allow"  # tier 0 or 2: the agent may go ahead
    HOLD_APPROVAL = "hold_approval"  # tier 1: owner taps Approve
    HOLD_PIN = "hold_pin"  # tier 3: owner confirms and enters the PIN
    BLOCK = "block"


TIER_NAMES = {0: "Read only", 1: "Suggest", 2: "Act within limits", 3: "Never alone"}


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str
    request_id: str
    action: ProposedAction
    tier: int | None  # None when blocked
    outcome: Outcome
    checks: tuple[CheckResult, ...]
    reason: str
    alert_owner: bool
    created_at: datetime

    @property
    def blocked(self) -> bool:
        return self.outcome is Outcome.BLOCK

    def check(self, name: str) -> CheckResult | None:
        return next((c for c in self.checks if c.name == name), None)
