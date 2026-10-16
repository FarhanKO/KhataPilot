"""Public API of the payments module."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.actions import AuthorizedAction
from app.core.clock import Clock, local_date, local_day_bounds
from app.core.ids import new_id
from app.core.money import fmt_taka
from app.core.sms import ParsedSms
from app.infra.sandbox_client import SandboxClient, SandboxError
from app.modules.ledger.schemas import NewLedgerEntry
from app.modules.ledger.service import LedgerService
from app.modules.payments.models import PaymentRecord
from app.modules.payments.schemas import PAYMENT_KIND, ExecutionRefused, PaymentCommand, PaymentView, SmsCheck
from app.modules.shop.service import ShopService


class PaymentsService:
    def __init__(self, session: Session, clock: Clock, shop_id: str, sandbox: SandboxClient | None = None) -> None:
        self.session = session
        self.clock = clock
        self.shop_id = shop_id
        self.sandbox = sandbox
        self.ledger = LedgerService(session, clock)
        self.shops = ShopService(session, clock)

    # ---- creating records

    def create(self, *, payee_name: str | None, payee_number: str, method: str, amount_paisa: int,
               execute_on: date | None, decision_id: str, request_id: str, tier: int, with_pin: bool,
               note: str | None = None) -> PaymentView:
        today = local_date(self.clock.now())
        when = execute_on or today
        row = PaymentRecord(
            id=new_id("pay"), shop_id=self.shop_id, payee_name=payee_name, payee_number=payee_number,
            method=method, amount_paisa=amount_paisa, execute_on=when,
            status="scheduled" if when > today else "executing", decision_id=decision_id,
            request_id=request_id, approved_tier=tier, approved_with_pin=with_pin,
            idempotency_key=new_id("idem"), note=note, created_at=self.clock.now(),
        )
        self.session.add(row)
        self.session.flush()
        return PaymentView.model_validate(row)

    def get(self, payment_id: str) -> PaymentView | None:
        row = self.session.get(PaymentRecord, payment_id)
        return PaymentView.model_validate(row) if row else None

    def command_for(self, payment: PaymentView) -> PaymentCommand:
        shop = self.shops.get(self.shop_id)
        accounts = {"bkash": shop.bkash_account, "nagad": shop.nagad_account, "bank": shop.bank_account}
        return PaymentCommand(
            from_account=accounts[payment.method], to_number=payment.payee_number, to_name=payment.payee_name,
            amount_paisa=payment.amount_paisa, method=payment.method, reference=payment.note,
        )

    # ---- execution (needs the guard's authorization)

    def execute(self, payment_id: str, auth: AuthorizedAction) -> PaymentView:
        row = self.session.get(PaymentRecord, payment_id)
        if row is None:
            raise ExecutionRefused(f"no payment {payment_id}")
        if row.status not in ("executing", "scheduled"):
            raise ExecutionRefused(f"payment {payment_id} is {row.status}")
        command = self.command_for(PaymentView.model_validate(row))
        if auth.idempotency_key != row.idempotency_key or not auth.binds(PAYMENT_KIND, command.hash_params()):
            raise ExecutionRefused("the authorization does not bind this payment's account, payee, amount and method")
        if self.sandbox is None:
            raise ExecutionRefused("no provider configured")
        row.status = "executing"
        try:
            result = self.sandbox.execute_payment(
                command.payload(), authorization=auth.header, idempotency_key=auth.idempotency_key
            )
        except SandboxError as exc:
            row.status, row.failure = "failed", f"{exc.code}: {exc.message}"
            self.session.flush()
            return PaymentView.model_validate(row)
        row.status = "completed"
        row.sandbox_txn_id = result["transaction_id"]
        row.executed_at = self.clock.now()
        self._book(row, result)
        self.session.flush()
        return PaymentView.model_validate(row)

    def _book(self, row: PaymentRecord, result: dict) -> None:
        """Record the money out in the books and settle the supplier's payable."""
        supplier = self.shops.find_party(self.shop_id, row.payee_name or "", kind="supplier") if row.payee_name else None
        self.ledger.add_entry(NewLedgerEntry(
            shop_id=self.shop_id,
            occurred_at=self.clock.now(),
            kind="supplier_payment" if supplier else "other_out",
            direction="out",
            method=row.method,
            amount_paisa=row.amount_paisa,
            category=(supplier.category if supplier and supplier.category else "Payments"),
            counterparty_name=row.payee_name,
            counterparty_number=row.payee_number,
            note=row.note or "Paid through KhataPilot",
            trx_id=result.get("transaction_id"),
            source="sandbox",
            source_ref=row.id,
        ))
        if supplier:
            self.ledger.apply_payment_to_payables(self.shop_id, supplier.name, row.amount_paisa)

    # ---- checking a forwarded SMS against the provider

    def check_sms(self, sms: ParsedSms) -> SmsCheck:
        """Ask the provider whether this SMS describes a real transaction on the shop's own account:
        the same TrxID, direction and amount. Anyone can send an SMS that *looks* like bKash (the
        commonest scam against shopkeepers), so the books only take the provider's word for it."""
        shop = self.shops.get(self.shop_id)
        account = {"bkash": shop.bkash_account, "nagad": shop.nagad_account, "bank": shop.bank_account}[sms.provider]
        name = {"bkash": "bKash", "nagad": "Nagad", "bank": "bank"}[sms.provider]
        if self.sandbox is None:
            return SmsCheck(confirmed=False, reason=f"KhataPilot cannot reach {name} to check this SMS, so it was not "
                                                    "recorded.")
        try:
            txn = self.sandbox.find_transaction(account, sms.trx_id)
        except SandboxError:
            return SmsCheck(confirmed=False, reason=f"I couldn't check this SMS with {name} just now, so it was not "
                                                    "recorded yet. Please try again in a minute.")
        if txn is None:
            return SmsCheck(confirmed=False, reason=(
                f"TrxID {sms.trx_id} is not in your {name} statement, so this SMS may be fake and nothing was recorded. "
                f"Check your {name} app before you hand over goods or mark a debt as paid."))
        direction = "credit" if sms.direction == "in" else "debit"
        if txn["direction"] != direction or txn["amount_paisa"] != sms.amount_paisa:
            shown = "received" if txn["direction"] == "credit" else "sent"
            return SmsCheck(confirmed=False, reason=(
                f"Your {name} statement shows TrxID {sms.trx_id} as {fmt_taka(txn['amount_paisa'])} {shown}, not what "
                "this SMS says, so it may have been altered. Nothing was recorded."))
        return SmsCheck(confirmed=True, reason=f"Confirmed with {name}: TrxID {sms.trx_id}, {fmt_taka(sms.amount_paisa)}.")

    # ---- schedules and the kill switch

    def due(self, on: date) -> list[PaymentView]:
        query = select(PaymentRecord).where(
            PaymentRecord.shop_id == self.shop_id, PaymentRecord.status == "scheduled",
            PaymentRecord.execute_on <= on,
        ).order_by(PaymentRecord.execute_on, PaymentRecord.created_at)
        return [PaymentView.model_validate(r) for r in self.session.scalars(query)]

    def set_status(self, payment_id: str, status: str, failure: str | None = None) -> None:
        row = self.session.get(PaymentRecord, payment_id)
        row.status = status
        if failure is not None:
            row.failure = failure

    def pause_scheduled(self) -> list[PaymentView]:
        rows = self.session.scalars(select(PaymentRecord).where(
            PaymentRecord.shop_id == self.shop_id, PaymentRecord.status == "scheduled")).all()
        for row in rows:
            row.status = "paused"
        self.session.flush()
        return [PaymentView.model_validate(r) for r in rows]

    def resume_paused(self) -> list[PaymentView]:
        rows = self.session.scalars(select(PaymentRecord).where(
            PaymentRecord.shop_id == self.shop_id, PaymentRecord.status == "paused")).all()
        for row in rows:
            row.status = "scheduled"
        self.session.flush()
        return [PaymentView.model_validate(r) for r in rows]

    # ---- views used by the guard's limits, velocity and fraud checks

    def list(self, status: str | None = None, limit: int = 100) -> list[PaymentView]:
        query = select(PaymentRecord).where(PaymentRecord.shop_id == self.shop_id)
        if status:
            query = query.where(PaymentRecord.status == status)
        query = query.order_by(PaymentRecord.created_at.desc()).limit(limit)
        return [PaymentView.model_validate(r) for r in self.session.scalars(query)]

    def committed_for_day(self, day: date) -> int:
        """Paid today plus still scheduled for today: what counts against the daily cap."""
        start, end = local_day_bounds(day)
        executed = self.session.scalar(select(func.coalesce(func.sum(PaymentRecord.amount_paisa), 0)).where(
            PaymentRecord.shop_id == self.shop_id, PaymentRecord.status == "completed",
            PaymentRecord.executed_at >= start, PaymentRecord.executed_at < end))
        pending = self.session.scalar(select(func.coalesce(func.sum(PaymentRecord.amount_paisa), 0)).where(
            PaymentRecord.shop_id == self.shop_id, PaymentRecord.status.in_(("scheduled", "executing", "paused")),
            PaymentRecord.execute_on == day))
        return int(executed) + int(pending)

    def recent(self, since: datetime) -> list[PaymentView]:
        """Payments created since `since` that went out or are still going out."""
        query = select(PaymentRecord).where(
            PaymentRecord.shop_id == self.shop_id, PaymentRecord.created_at >= since,
            PaymentRecord.status.in_(("completed", "executing", "scheduled", "paused")),
        ).order_by(PaymentRecord.created_at)
        return [PaymentView.model_validate(r) for r in self.session.scalars(query)]
