"""Runs scheduled payments on their day. Approval happened days earlier, so the hard facts are
checked again first (time-of-check versus time-of-use): the kill switch, the blocklist, whether the
payee is still trusted, and the daily cap. A payment that fails a re-check is held and the owner
is alerted; nothing is paid on stale approval."""

from __future__ import annotations

from app.core.clock import Clock, local_date
from app.core.money import fmt_taka
from app.guard.executor import ActionExecutor, ExecutionMeta, ExecutionResult
from app.guard.policy_store import PolicyStore
from app.guard.recorder import Recorder
from app.modules.payments.schemas import PaymentView
from app.modules.payments.service import PaymentsService


class PaymentScheduler:
    def __init__(self, *, clock: Clock, store: PolicyStore, payments: PaymentsService, executor: ActionExecutor,
                 recorder: Recorder) -> None:
        self.clock = clock
        self.store = store
        self.payments = payments
        self.executor = executor
        self.recorder = recorder

    def recheck(self, record: PaymentView) -> str | None:
        blocked = self.store.blocked_reason(record.payee_number)
        if blocked:
            return f"the number is now on your blocked list ({blocked})"
        trusted = self.store.payee_by_number(record.payee_number)
        if not record.approved_with_pin and trusted is None:
            return "the payee is no longer on your trusted list"
        limits = self.store.limits()
        if not record.approved_with_pin:
            already = self.payments.committed_for_day(local_date(self.clock.now())) - record.amount_paisa
            if already + record.amount_paisa > limits.daily_paisa:
                return f"it would take today's payments over your {fmt_taka(limits.daily_paisa)} daily cap"
        return None

    def run_due(self, *, request_id: str) -> list[ExecutionResult]:
        if self.store.kill_switch().frozen:
            return []
        meta = ExecutionMeta(request_id=request_id, channel="scheduler", actor="scheduler", provenance="system")
        results = []
        for record in self.payments.due(local_date(self.clock.now())):
            problem = self.recheck(record)
            if problem:
                self.payments.set_status(record.id, "held", failure=problem)
                summary = f"Held scheduled payment: {fmt_taka(record.amount_paisa)} to {record.payee_name or 'payee'}"
                self.recorder.event(request_id=request_id, channel="scheduler", actor="scheduler", provenance="system",
                                    event="execution", summary=summary, decision="BLOCKED",
                                    reason=f"Not paid because {problem}. Approve it again if it is still wanted.",
                                    decision_id=record.decision_id, tier=record.approved_tier)
                self.recorder.alert(request_id=request_id, channel="scheduler", summary=summary,
                                    reason=f"Not paid because {problem}.", decision_id=record.decision_id)
                results.append(ExecutionResult("failed", f"{summary}: {problem}."))
                continue
            result = self.executor.execute_payment(record, tier=record.approved_tier,
                                                   decision_id=record.decision_id, meta=meta)
            if result.ok:
                self.recorder.notify(request_id=request_id, channel="scheduler", summary="Scheduled payment sent",
                                     reason=result.message, decision_id=record.decision_id)
            else:
                self.recorder.alert(request_id=request_id, channel="scheduler", summary="Scheduled payment failed",
                                    reason=result.message, decision_id=record.decision_id)
            results.append(result)
        return results
