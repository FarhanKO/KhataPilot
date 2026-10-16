"""Writes the guard's decisions and events to the hash-chained audit log. The decision-trace stream
subscribes to the audit log, so what the owner sees on screen is exactly what was recorded."""

from __future__ import annotations

from typing import Any

from app.core.decisions import Decision, Outcome
from app.core.provenance import SenderKind, TurnContext
from app.infra.audit_log import AuditEntry, AuditLog, AuditRecord

OUTCOME_LABEL = {
    Outcome.ALLOW: "ALLOWED",
    Outcome.HOLD_APPROVAL: "HELD_FOR_APPROVAL",
    Outcome.HOLD_PIN: "HELD_FOR_PIN",
    Outcome.BLOCK: "BLOCKED",
}


def actor_of(turn: TurnContext) -> str:
    if turn.sender.kind is SenderKind.OWNER:
        return "owner"
    if turn.sender.kind is SenderKind.SYSTEM:
        return "system"
    return f"external:{turn.sender.display}"


class Recorder:
    def __init__(self, audit: AuditLog) -> None:
        self.audit = audit

    def decision(self, decision: Decision, turn: TurnContext) -> AuditEntry:
        return self.audit.append(AuditRecord(
            request_id=turn.request_id,
            channel=turn.channel,
            actor=actor_of(turn),
            provenance=turn.provenance.value,
            event="decision",
            summary=decision.action.summary,
            action=decision.action.model_dump(mode="json"),
            tier=decision.tier,
            checks=[c.model_dump(mode="json") for c in decision.checks],
            decision=OUTCOME_LABEL[decision.outcome],
            reason=decision.reason,
            decision_id=decision.decision_id,
        ))

    def event(self, *, request_id: str, channel: str, actor: str, provenance: str, event: str, summary: str,
              decision: str, reason: str, action: dict[str, Any] | None = None, tier: int | None = None,
              sandbox_ref: str | None = None, decision_id: str | None = None,
              checks: list[dict[str, Any]] | None = None) -> AuditEntry:
        return self.audit.append(AuditRecord(
            request_id=request_id, channel=channel, actor=actor, provenance=provenance, event=event,
            summary=summary, action=action, tier=tier, checks=checks or [], decision=decision, reason=reason,
            sandbox_ref=sandbox_ref, decision_id=decision_id,
        ))

    def notify(self, *, request_id: str, channel: str, summary: str, reason: str,
               decision_id: str | None = None) -> AuditEntry:
        """Something the owner should hear about that went fine (e.g. a scheduled payment was sent)."""
        return self.event(request_id=request_id, channel=channel, actor="system", provenance="system",
                          event="notification", summary=summary, decision="INFO", reason=reason,
                          decision_id=decision_id)

    def alert(self, *, request_id: str, channel: str, summary: str, reason: str,
              decision_id: str | None = None) -> AuditEntry:
        """An alert the owner must see (blocked attack, frozen account, held scheduled payment)."""
        return self.event(request_id=request_id, channel=channel, actor="system", provenance="system", event="alert",
                          summary=summary, decision="ALERT", reason=reason, decision_id=decision_id)
