"""The tool gateway: the agent's only door to the rest of the system.

The agent hands over a `ToolCall` (a name and arguments produced by the LLM). The gateway validates
the arguments against a strict schema (unknown fields are rejected), turns them into a
`ProposedAction`, asks the policy engine, records the decision in the audit log, and then either
runs the action, holds it for the owner, or refuses it. Whatever happens, the LLM gets back data.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from app.core.actions import ActionKind, ProposedAction
from app.core.decisions import Decision, Outcome
from app.core.provenance import TurnContext
from app.guard.approvals import ApprovalService
from app.guard.engine import PolicyEngine
from app.guard.executor import ActionExecutor, ExecutionMeta
from app.guard.recorder import Recorder, actor_of
from app.guard.tiers import BASE_TIER


class ToolRefusal(Exception):
    """Raised by a handler that was allowed to run but could not do the job (e.g. an unreadable SMS)."""


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = {}
    call_id: str | None = None


class ToolDescriptor(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    kind: str
    base_tier: int


class ToolOutcome(BaseModel):
    call_id: str | None
    tool: str
    status: Literal["ok", "held", "blocked", "error"]
    tier: int | None = None
    decision_id: str | None = None
    pending_id: str | None = None
    requires_pin: bool = False
    message: str
    data: Any = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    kind: ActionKind
    args_model: type[BaseModel]
    to_action: Callable[[BaseModel, TurnContext], ProposedAction]
    # Runs an allowed tier 0 action and returns data for the LLM. Side effects go through the executor instead.
    handler: Callable[[BaseModel, Decision, TurnContext], Any] | None = None


class ToolGateway:
    def __init__(self, *, engine: PolicyEngine, approvals: ApprovalService, executor: ActionExecutor,
                 recorder: Recorder) -> None:
        self.engine = engine
        self.approvals = approvals
        self.executor = executor
        self.recorder = recorder
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def descriptors(self) -> list[ToolDescriptor]:
        return [
            ToolDescriptor(name=s.name, description=s.description, parameters=s.args_model.model_json_schema(),
                           kind=s.kind.value, base_tier=BASE_TIER[s.kind])
            for s in self._tools.values()
        ]

    def _refuse_call(self, call: ToolCall, turn: TurnContext, reason: str) -> ToolOutcome:
        self.recorder.event(request_id=turn.request_id, channel=turn.channel, actor=actor_of(turn),
                            provenance=turn.provenance.value, event="decision",
                            summary=f"Rejected tool call {call.name!r}", decision="BLOCKED", reason=reason,
                            action={"tool": call.name, "arguments": call.arguments})
        return ToolOutcome(call_id=call.call_id, tool=call.name, status="error", message=reason)

    def submit(self, call: ToolCall, turn: TurnContext) -> ToolOutcome:
        spec = self._tools.get(call.name)
        if spec is None:
            return self._refuse_call(call, turn, f"There is no tool called {call.name!r}.")
        try:
            args = spec.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in exc.errors())
            return self._refuse_call(call, turn, f"The arguments for {call.name} were not valid ({problems}).")

        try:
            action = spec.to_action(args, turn)
        except ToolRefusal as refusal:
            return self._refuse_call(call, turn, str(refusal))
        decision = self.engine.evaluate(action, turn)
        self.recorder.decision(decision, turn)
        base = dict(call_id=call.call_id, tool=call.name, tier=decision.tier, decision_id=decision.decision_id)

        if decision.outcome is Outcome.BLOCK:
            if decision.alert_owner:
                self.recorder.alert(request_id=turn.request_id, channel=turn.channel,
                                    summary=f"Blocked: {decision.action.summary}", reason=decision.reason,
                                    decision_id=decision.decision_id)
            return ToolOutcome(status="blocked", message=decision.reason, **base)

        if decision.outcome in (Outcome.HOLD_APPROVAL, Outcome.HOLD_PIN):
            pending = self.approvals.create(decision, turn)
            needs = "confirm and enter your PIN" if pending.requires_pin else "tap Approve"
            return ToolOutcome(status="held", pending_id=pending.id, requires_pin=pending.requires_pin,
                               message=f"Waiting for the owner to {needs}. {decision.reason}",
                               data={"pending_id": pending.id, "summary": pending.summary,
                                     "warnings": pending.warnings}, **base)

        if decision.action.kind in (ActionKind.PAYMENT, ActionKind.MESSAGE, ActionKind.LOAN_SUBMIT):
            meta = ExecutionMeta(request_id=turn.request_id, channel=turn.channel, actor=actor_of(turn),
                                 provenance=turn.provenance.value)
            result = self.executor.run(decision.action, decision_id=decision.decision_id, tier=decision.tier,
                                       with_pin=False, meta=meta)
            return ToolOutcome(status="ok" if result.ok else "error", message=result.message, data=result.data, **base)

        try:
            data = spec.handler(args, decision, turn) if spec.handler else None
        except ToolRefusal as refusal:
            self._record_result(decision, turn, "FAILED", str(refusal))
            return ToolOutcome(status="error", message=str(refusal), **base)
        if decision.action.kind is ActionKind.LEDGER_WRITE:
            self._record_result(decision, turn, "RECORDED", "Written to the shop's books.")
        return ToolOutcome(status="ok", message=decision.reason, data=data, **base)

    def _record_result(self, decision: Decision, turn: TurnContext, outcome: str, reason: str) -> None:
        self.recorder.event(request_id=turn.request_id, channel=turn.channel, actor=actor_of(turn),
                            provenance=turn.provenance.value, event="execution", summary=decision.action.summary,
                            decision=outcome, reason=reason, tier=decision.tier, decision_id=decision.decision_id)
