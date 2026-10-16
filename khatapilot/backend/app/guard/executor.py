"""Carries out actions the guard has allowed (now or after the owner's approval).

For every side effect it first mints an `AuthorizedAction` binding the exact parameters, then
hands it to the module, which re-checks the binding before calling the provider, which checks it
again. Tier 1 handlers (messages, loan applications) register here as their modules land.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.actions import ActionKind, ProposedAction
from app.core.clock import Clock
from app.core.money import fmt_taka
from app.guard.capability import CapabilitySigner
from app.guard.recorder import Recorder
from app.modules.loans.schemas import ApplicationDraft
from app.modules.loans.service import LOAN_KIND, LoansService, SubmissionRefused
from app.modules.payments.schemas import PAYMENT_KIND, PaymentView
from app.modules.payments.service import PaymentsService
from app.modules.reminders.schemas import REMINDER_KIND, ReminderDraft, SendRefused
from app.modules.reminders.service import RemindersService


@dataclass
class ExecutionMeta:
    request_id: str
    channel: str
    actor: str
    provenance: str


@dataclass
class ExecutionResult:
    status: str  # completed | scheduled | failed | sent | submitted
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status != "failed"


Handler = Callable[[ProposedAction, str, int, ExecutionMeta], ExecutionResult]


class ActionExecutor:
    def __init__(self, *, clock: Clock, payments: PaymentsService, signer: CapabilitySigner, recorder: Recorder) -> None:
        self.clock = clock
        self.payments = payments
        self.signer = signer
        self.recorder = recorder
        self._handlers: dict[ActionKind, Handler] = {}

    def register(self, kind: ActionKind, handler: Handler) -> None:
        self._handlers[kind] = handler

    def run(self, action: ProposedAction, *, decision_id: str, tier: int, with_pin: bool,
            meta: ExecutionMeta) -> ExecutionResult:
        if action.kind is ActionKind.PAYMENT:
            return self.run_payment(action, decision_id=decision_id, tier=tier, with_pin=with_pin, meta=meta)
        handler = self._handlers.get(action.kind)
        if handler is None:
            return ExecutionResult("failed", f"No handler for {action.kind.value} yet.")
        return handler(action, decision_id, tier, meta)

    # ---- payments

    def run_payment(self, action: ProposedAction, *, decision_id: str, tier: int, with_pin: bool,
                    meta: ExecutionMeta) -> ExecutionResult:
        p = action.payment
        record = self.payments.create(
            payee_name=p.payee_name, payee_number=p.payee_number, method=p.method, amount_paisa=p.amount_paisa,
            execute_on=p.execute_on, decision_id=decision_id, request_id=meta.request_id, tier=tier,
            with_pin=with_pin, note=p.note,
        )
        if record.status == "scheduled":
            message = (f"Scheduled {fmt_taka(record.amount_paisa)} to {record.payee_name or 'the payee'} for "
                       f"{record.execute_on:%a %d %b}. It will be checked again before it goes out.")
            self.recorder.event(request_id=meta.request_id, channel=meta.channel, actor=meta.actor,
                                provenance=meta.provenance, event="execution", summary=action.summary,
                                decision="SCHEDULED", reason=message, tier=tier, decision_id=decision_id,
                                action={"payment_id": record.id})
            return ExecutionResult("scheduled", message, {"payment": record.model_dump(mode="json")})
        return self.execute_payment(record, tier=tier, decision_id=decision_id, meta=meta, summary=action.summary)

    def execute_payment(self, record: PaymentView, *, tier: int, decision_id: str, meta: ExecutionMeta,
                        summary: str | None = None) -> ExecutionResult:
        command = self.payments.command_for(record)
        auth = self.signer.mint(kind=PAYMENT_KIND, params=command.hash_params(),
                                idempotency_key=record.idempotency_key, tier=tier, decision_id=decision_id)
        done = self.payments.execute(record.id, auth)
        summary = summary or f"Pay {done.payee_name or 'payee'} {fmt_taka(done.amount_paisa)}"
        if done.status == "completed":
            message = (f"Paid {fmt_taka(done.amount_paisa)} to {done.payee_name or 'the payee'} "
                       f"(TrxID {done.sandbox_txn_id}, simulated).")
            decision = "EXECUTED"
        else:
            message = f"The provider refused the payment: {done.failure}."
            decision = "FAILED"
        self.recorder.event(request_id=meta.request_id, channel=meta.channel, actor=meta.actor,
                            provenance=meta.provenance, event="execution", summary=summary, decision=decision,
                            reason=message, tier=tier, sandbox_ref=done.sandbox_txn_id, decision_id=decision_id,
                            action={"payment_id": done.id, "authorization_id": auth.token_id})
        return ExecutionResult("completed" if done.status == "completed" else "failed", message,
                               {"payment": done.model_dump(mode="json")})

    # ---- loan applications (tier 1: only after the owner taps Approve)

    def submit_loan(self, loans: LoansService, action: ProposedAction, decision_id: str, tier: int,
                    meta: ExecutionMeta) -> ExecutionResult:
        draft = ApplicationDraft.model_validate(action.params["application"])
        auth = self.signer.mint(kind=LOAN_KIND, params=loans.auth_params(draft), idempotency_key=f"loan-{decision_id}",
                                tier=tier, decision_id=decision_id)
        try:
            done = loans.submit(draft, auth, decision_id=decision_id)
        except SubmissionRefused as exc:
            done, message = None, f"The application was not sent: {exc}."
        else:
            message = (f"Sent your application for a {done.product_name} ({fmt_taka(done.amount_paisa)}) to the lender: "
                       f"reference {done.sandbox_ref} (simulated). A loan officer reviews it within 3 working days."
                       if done.status == "submitted" else f"The lender refused the application: {done.failure}.")
        ok = done is not None and done.status == "submitted"
        self.recorder.event(request_id=meta.request_id, channel=meta.channel, actor=meta.actor,
                            provenance=meta.provenance, event="execution", summary=action.summary,
                            decision="SUBMITTED" if ok else "FAILED", reason=message, tier=tier,
                            sandbox_ref=done.sandbox_ref if done else None, decision_id=decision_id,
                            action={"application_id": done.id if done else None, "authorization_id": auth.token_id})
        return ExecutionResult("submitted" if ok else "failed", message,
                               {"application": done.model_dump(mode="json")} if done else {})

    # ---- baki reminders (tier 1: only after the owner taps Approve)

    def send_reminders(self, reminders: RemindersService, action: ProposedAction, decision_id: str, tier: int,
                       meta: ExecutionMeta) -> ExecutionResult:
        """Each reminder gets its own token, bound to its number and the hash of its text. A customer
        who paid in full while the reminders waited for approval is not sent one."""
        sent, paid_meanwhile, failed = [], [], []
        for i, raw in enumerate(action.params.get("reminders", [])):
            draft = ReminderDraft.model_validate(raw)
            if reminders.outstanding(draft.customer_name) <= 0:
                paid_meanwhile.append(draft.customer_name)
                continue
            auth = self.signer.mint(kind=REMINDER_KIND, params=reminders.auth_params(draft),
                                    idempotency_key=f"msg-{decision_id}-{i}", tier=tier, decision_id=decision_id)
            try:
                done = reminders.send(draft, auth, decision_id=decision_id)
            except SendRefused as exc:
                failed.append(f"{draft.customer_name} ({exc})")
                continue
            ok = done.status == "sent"
            (sent if ok else failed).append(draft.customer_name if ok else f"{draft.customer_name} ({done.failure})")
            self.recorder.event(request_id=meta.request_id, channel=meta.channel, actor=meta.actor,
                                provenance=meta.provenance, event="execution",
                                summary=f"Baki reminder to {draft.customer_name} ({fmt_taka(draft.owed_paisa)})",
                                decision="SENT" if ok else "FAILED",
                                reason=(f"Delivered by SMS (simulated), reference {done.sandbox_ref}." if ok
                                        else f"Not delivered: {done.failure}."),
                                tier=tier, sandbox_ref=done.sandbox_ref, decision_id=decision_id,
                                action={"reminder_id": done.id, "authorization_id": auth.token_id})
        parts = []
        if sent:
            parts.append(f"Sent {len(sent)} reminder{'s' if len(sent) != 1 else ''} (simulated): {', '.join(sent)}.")
        if paid_meanwhile:
            parts.append(f"Not sent to {', '.join(paid_meanwhile)}: paid in full while this waited.")
        if failed:
            parts.append(f"Failed: {'; '.join(failed)}.")
        status = "sent" if sent or not failed else "failed"
        return ExecutionResult(status, " ".join(parts) or "Nothing to send.",
                               {"sent": sent, "paid_meanwhile": paid_meanwhile, "failed": failed})
