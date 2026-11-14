"""Actions waiting for the owner. Tier 1: the owner taps Approve. Tier 3: the owner sees the full
details (payee, number, amount, date), confirms and enters the PIN. The stored action is hashed when
it is held, and the hash is checked again on approval, so what runs is exactly what was shown. Facts that
can change while an action waits (a blocked number, the same payment already made, this month's savings
already moved) are checked again too (`rechecks.py`)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.actions import ActionKind, ProposedAction, canonical_json, sha256_hex
from app.core.clock import Clock
from app.core.decisions import CheckStatus, Decision, Outcome
from app.core.ids import new_id
from app.core.money import fmt_taka
from app.core.provenance import TurnContext
from app.guard.executor import ActionExecutor, ExecutionMeta
from app.guard.models import PendingAction
from app.guard.pin import PinManager
from app.guard.policy_store import PolicyStore
from app.guard.recorder import Recorder


class PendingView(BaseModel):
    id: str
    decision_id: str
    tool: str
    kind: str
    tier: int
    requires_pin: bool
    summary: str
    warnings: list[str]
    action: dict[str, Any]
    status: str
    created_at: datetime
    expires_at: datetime
    conversation_id: str = ""


class ApprovalResult(BaseModel):
    ok: bool
    status: str  # completed | scheduled | sent | submitted | failed (the executor's), or wrong_pin | locked |
    #              refused | rejected | expired | not_found
    message: str
    attempts_left: int | None = None
    data: dict[str, Any] = {}


def _hash(action_json: str) -> str:
    return sha256_hex(canonical_json(json.loads(action_json)))


def _view(row: PendingAction) -> PendingView:
    return PendingView(id=row.id, decision_id=row.decision_id, tool=row.tool, kind=row.kind, tier=row.tier,
                       requires_pin=row.requires_pin, summary=row.summary, warnings=json.loads(row.warnings_json),
                       action=json.loads(row.action_json), status=row.status, created_at=row.created_at,
                       expires_at=row.expires_at, conversation_id=row.conversation_id)


class ApprovalService:
    def __init__(self, *, session: Session, clock: Clock, shop_id: str, store: PolicyStore, pins: PinManager,
                 executor: ActionExecutor, recorder: Recorder, ttl_minutes: int = 30,
                 recheck: Callable[[ProposedAction, datetime], str | None] | None = None) -> None:
        self.session = session
        self.recheck = recheck  # facts that may have changed since the action was held (app.guard.rechecks)
        self.clock = clock
        self.shop_id = shop_id
        self.store = store
        self.pins = pins
        self.executor = executor
        self.recorder = recorder
        self.ttl = timedelta(minutes=ttl_minutes)

    def create(self, decision: Decision, turn: TurnContext) -> PendingView:
        action_json = decision.action.model_dump_json()
        now = self.clock.now()
        row = PendingAction(
            id=new_id("pend"), shop_id=self.shop_id, decision_id=decision.decision_id, request_id=turn.request_id,
            conversation_id=turn.conversation_id, tool=decision.action.tool, kind=decision.action.kind.value,
            action_json=action_json, action_hash=_hash(action_json), tier=decision.tier,
            requires_pin=decision.outcome is Outcome.HOLD_PIN, summary=decision.action.summary,
            warnings_json=json.dumps([c.detail for c in decision.checks if c.status is CheckStatus.ESCALATE]),
            status="pending", created_at=now, expires_at=now + self.ttl,
        )
        self.session.add(row)
        self.session.flush()
        return _view(row)

    def pending(self) -> list[PendingView]:
        self._expire()
        rows = self.session.scalars(select(PendingAction).where(
            PendingAction.shop_id == self.shop_id, PendingAction.status == "pending").order_by(PendingAction.created_at))
        return [_view(r) for r in rows]

    def get(self, pending_id: str) -> PendingView | None:
        row = self.session.get(PendingAction, pending_id)
        return _view(row) if row and row.shop_id == self.shop_id else None

    def _expire(self) -> None:
        now = self.clock.now()
        for row in self.session.scalars(select(PendingAction).where(
                PendingAction.shop_id == self.shop_id, PendingAction.status == "pending",
                PendingAction.expires_at <= now)):
            row.status, row.resolved_at = "expired", now
        self.session.flush()

    def approve(self, pending_id: str, *, pin: str | None, request_id: str, channel: str = "web") -> ApprovalResult:
        self._expire()
        row = self.session.get(PendingAction, pending_id)
        if row is None or row.shop_id != self.shop_id:
            return ApprovalResult(ok=False, status="not_found", message="There is no such pending action.")
        if row.status != "pending":
            return ApprovalResult(ok=False, status=row.status, message=f"This action is already {row.status}.")
        meta = ExecutionMeta(request_id=request_id, channel=channel, actor="owner", provenance="owner")
        if self.store.kill_switch().frozen and row.kind in {k.value for k in (ActionKind.PAYMENT, ActionKind.MESSAGE,
                                                                              ActionKind.LOAN_SUBMIT)}:
            return self._refuse(row, meta, "STOP is on. Resume with your PIN first.")
        if _hash(row.action_json) != row.action_hash:
            return self._refuse(row, meta, "The held action changed after it was shown to you, so it was not run.")
        if self.recheck and (problem := self.recheck(ProposedAction.model_validate_json(row.action_json),
                                                     row.created_at)):
            row.status, row.resolved_at = "failed", self.clock.now()
            row.result_json = json.dumps({"status": "refused", "message": problem})
            self.session.flush()
            return self._refuse(row, meta, problem)
        if row.requires_pin:
            check = self.pins.verify(pin)
            self.recorder.event(request_id=request_id, channel=channel, actor="owner", provenance="owner", event="pin",
                                summary=f"PIN entered to approve: {row.summary}",
                                decision="PIN_OK" if check.ok else ("PIN_LOCKED" if check.locked else "PIN_WRONG"),
                                reason=check.message, decision_id=row.decision_id, tier=row.tier)
            if not check.ok:
                return ApprovalResult(ok=False, status="locked" if check.locked else "wrong_pin", message=check.message,
                                      attempts_left=check.attempts_left)
        action = ProposedAction.model_validate_json(row.action_json)
        self.recorder.event(request_id=request_id, channel=channel, actor="owner", provenance="owner", event="approval",
                            summary=row.summary, decision="APPROVED",
                            reason="Approved by the owner" + (" with PIN." if row.requires_pin else "."),
                            decision_id=row.decision_id, tier=row.tier)
        result = self.executor.run(action, decision_id=row.decision_id, tier=row.tier, with_pin=row.requires_pin,
                                   meta=meta)
        row.status = "executed" if result.ok else "failed"
        row.result_json = json.dumps({"status": result.status, "message": result.message})
        row.resolved_at = self.clock.now()
        self.session.flush()
        return ApprovalResult(ok=result.ok, status=result.status, message=result.message, data=result.data)

    def remove_reminder(self, pending_id: str, customer_name: str, *, request_id: str,
                        channel: str = "web") -> ApprovalResult:
        """Take one customer out of a held batch of baki reminders before it is approved. This can only
        remove: every other reminder stays exactly as it was shown (the batch is hashed again), and taking
        out the last one cancels the batch."""
        self._expire()
        row = self.session.get(PendingAction, pending_id)
        if row is None or row.shop_id != self.shop_id or row.status != "pending":
            return ApprovalResult(ok=False, status="not_found", message="There is no such pending action.")
        if row.tool != "send_reminders":
            return ApprovalResult(ok=False, status="refused", message="Only baki reminders can be changed this way.")
        if _hash(row.action_json) != row.action_hash:
            return ApprovalResult(ok=False, status="refused",
                                  message="The held reminders changed after they were shown to you, so nothing changed.")
        action = json.loads(row.action_json)
        drafts = action.get("params", {}).get("reminders", [])
        keep = [d for d in drafts if d.get("customer_name") != customer_name]
        if len(keep) == len(drafts):
            return ApprovalResult(ok=False, status="not_found",
                                  message=f"{customer_name} is not in these reminders.")
        before = row.summary
        if not keep:
            row.status, row.resolved_at = "rejected", self.clock.now()
            self.session.flush()
            self.recorder.event(request_id=request_id, channel=channel, actor="owner", provenance="owner",
                                event="approval", summary=before, decision="REJECTED",
                                reason=f"The owner took out {customer_name}, the last customer in the batch, so no "
                                       "reminder will be sent.", decision_id=row.decision_id, tier=row.tier)
            return ApprovalResult(ok=True, status="rejected",
                                  message=f"{customer_name} was taken out. Nobody is left, so nothing will be sent.")
        params = action["params"]
        params["reminders"] = keep
        params["removed"] = [*params.get("removed", []), customer_name]
        n, total = len(keep), sum(int(d.get("owed_paisa", 0)) for d in keep)
        names = ", ".join(d["customer_name"] for d in keep[:3]) + (f" and {n - 3} more" if n > 3 else "")
        summary = f"Send baki reminders to {n} customer{'s' if n != 1 else ''} who owe {fmt_taka(total)}: {names}"
        action["summary"] = summary
        row.action_json = json.dumps(action, ensure_ascii=False)
        row.action_hash = _hash(row.action_json)
        row.summary = summary
        self.session.flush()
        self.recorder.event(request_id=request_id, channel=channel, actor="owner", provenance="owner",
                            event="approval", summary=f"Took {customer_name} out of: {before}", decision="NARROWED",
                            reason=(f"The owner removed {customer_name} before approving; no reminder will go to them. "
                                    f"{n} still wait{'s' if n == 1 else ''} for approval."),
                            decision_id=row.decision_id, tier=row.tier)
        return ApprovalResult(ok=True, status="narrowed",
                              message=(f"{customer_name} was taken out. {n} reminder{'s' if n != 1 else ''} still "
                                       "wait for your approval."),
                              data={"pending": _view(row).model_dump(mode="json")})

    def reject(self, pending_id: str, *, request_id: str, channel: str = "web") -> ApprovalResult:
        row = self.session.get(PendingAction, pending_id)
        if row is None or row.shop_id != self.shop_id or row.status != "pending":
            return ApprovalResult(ok=False, status="not_found", message="There is no such pending action.")
        row.status, row.resolved_at = "rejected", self.clock.now()
        self.session.flush()
        self.recorder.event(request_id=request_id, channel=channel, actor="owner", provenance="owner", event="approval",
                            summary=row.summary, decision="REJECTED", reason="Rejected by the owner.",
                            decision_id=row.decision_id, tier=row.tier)
        return ApprovalResult(ok=True, status="rejected", message="Cancelled. Nothing was done.")

    def _refuse(self, row: PendingAction, meta: ExecutionMeta, reason: str) -> ApprovalResult:
        self.recorder.event(request_id=meta.request_id, channel=meta.channel, actor="owner", provenance="owner",
                            event="approval", summary=row.summary, decision="REFUSED", reason=reason,
                            decision_id=row.decision_id, tier=row.tier)
        return ApprovalResult(ok=False, status="refused", message=reason)
