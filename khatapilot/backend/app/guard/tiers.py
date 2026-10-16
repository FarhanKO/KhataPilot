"""Permission tiers: the base tier of each kind of action, and how check results turn into a tier
and an outcome. Escalations only ever raise the tier; any block wins."""

from __future__ import annotations

from collections.abc import Iterable

from app.core.actions import ActionKind
from app.core.decisions import CheckResult, CheckStatus, Outcome

BASE_TIER: dict[ActionKind, int] = {
    ActionKind.READ: 0,
    ActionKind.LEDGER_WRITE: 0,
    ActionKind.NOTIFY_OWNER: 0,
    ActionKind.MESSAGE: 1,
    ActionKind.LOAN_SUBMIT: 1,
    ActionKind.PAYMENT: 2,
}

OUTCOME_FOR_TIER: dict[int, Outcome] = {
    0: Outcome.ALLOW,
    1: Outcome.HOLD_APPROVAL,
    2: Outcome.ALLOW,
    3: Outcome.HOLD_PIN,
}

# Actions that leave the shop: these are frozen by the kill switch and need an owner's turn.
SIDE_EFFECT_KINDS = {ActionKind.PAYMENT, ActionKind.MESSAGE, ActionKind.LOAN_SUBMIT}


def decide(kind: ActionKind, checks: Iterable[CheckResult]) -> tuple[int | None, Outcome]:
    checks = list(checks)
    if any(c.status is CheckStatus.BLOCK for c in checks):
        return None, Outcome.BLOCK
    tier = max([BASE_TIER[kind]] + [c.tier for c in checks if c.status is CheckStatus.ESCALATE and c.tier is not None])
    return tier, OUTCOME_FOR_TIER[tier]
