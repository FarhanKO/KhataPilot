"""Sandbox provider API: a bKash/Nagad-style wallet, a bank account, a messaging gateway and a lender.

Every side effect (payment, message, loan application, freeze, unfreeze) needs a valid guard
authorization (see `sandbox/auth.py`) and an idempotency key. Every request, accepted or refused,
is written to the operation log. Every response carries `"simulated": true`.

Run with:  python -m uvicorn sandbox.app:create_app --factory --port 8100
"""

from __future__ import annotations

import hmac
import json
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sandbox import sms
from sandbox.auth import AuthError, VerifiedAuthorization, canonical_json, check_not_expired, sha256_hex, verify
from sandbox.config import SandboxSettings
from sandbox.db import Base, make_engine, make_session_factory
from sandbox.models import (
    Account,
    LoanApplication,
    OpLogEntry,
    OutboundMessage,
    Transaction,
    UsedAuthorization,
)
from sandbox.runtime import BDT, OpLog, SandboxClock, is_msisdn, mask_number, next_seq, next_trx_id, to_json

Provider = Literal["bkash", "nagad", "bank"]


class ProviderError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def sim(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "simulated": True}


# ---------------------------------------------------------------- request bodies

class PaymentRequest(BaseModel):
    from_account: str
    to_number: str
    to_name: str | None = Field(default=None, max_length=128)
    amount_paisa: int = Field(gt=0)
    method: Provider
    reference: str | None = Field(default=None, max_length=120)


class FreezeRequest(BaseModel):
    reason: str = Field(default="Owner activated the kill switch", max_length=200)


class MessageRequest(BaseModel):
    from_account: str
    to_number: str
    channel: Literal["sms", "messenger"] = "sms"
    body: str = Field(min_length=1, max_length=1000)


class LoanApplicationRequest(BaseModel):
    applicant_account: str
    shop_name: str = Field(max_length=128)
    product_id: str = Field(max_length=64)
    amount_paisa: int = Field(gt=0)
    tenure_months: int = Field(ge=1, le=60)
    payload: dict[str, Any] = Field(default_factory=dict)


class AccountSeed(BaseModel):
    id: str
    provider: Provider
    number: str
    holder_name: str
    kind: Literal["merchant", "personal", "bank"] = "personal"
    balance_paisa: int = 0


class TransactionSeed(BaseModel):
    trx_id: str
    account_id: str
    direction: Literal["debit", "credit"]
    txn_type: str
    amount_paisa: int
    fee_paisa: int = 0
    balance_after_paisa: int
    counterparty_number: str | None = None
    counterparty_name: str | None = None
    reference: str | None = None
    occurred_at: datetime
    sms_text: str | None = None


class ClockRequest(BaseModel):
    now: datetime
    frozen: bool = False


class IncomingRequest(BaseModel):
    to_account: str
    from_number: str
    from_name: str | None = None
    amount_paisa: int = Field(gt=0)
    txn_type: Literal["received", "payment_received", "cash_in"] = "received"
    reference: str | None = Field(default=None, max_length=120)


# ---------------------------------------------------------------- views

def account_view(account: Account) -> dict[str, Any]:
    return {
        "id": account.id,
        "provider": account.provider,
        "number_masked": mask_number(account.number),
        "holder_name": account.holder_name,
        "kind": account.kind,
        "balance_paisa": account.balance_paisa,
        "frozen": account.frozen,
        "frozen_reason": account.frozen_reason,
    }


def transaction_view(txn: Transaction) -> dict[str, Any]:
    return {
        "trx_id": txn.trx_id,
        "account_id": txn.account_id,
        "direction": txn.direction,
        "txn_type": txn.txn_type,
        "amount_paisa": txn.amount_paisa,
        "fee_paisa": txn.fee_paisa,
        "balance_after_paisa": txn.balance_after_paisa,
        "counterparty_masked": mask_number(txn.counterparty_number),
        "counterparty_name": txn.counterparty_name,
        "reference": txn.reference,
        "status": txn.status,
        "authorization_id": txn.authorization_id,
        "decision_id": txn.decision_id,
        "occurred_at": txn.occurred_at.astimezone(BDT).isoformat(),
        "sms_text": txn.sms_text,
    }


# ---------------------------------------------------------------- the app

def create_app(settings: SandboxSettings | None = None) -> FastAPI:
    settings = settings or SandboxSettings()
    settings.check_secrets()
    engine = make_engine(settings.sandbox_data_dir / "sandbox.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    clock = SandboxClock(sessions, settings.sim_time)
    oplog = OpLog(sessions, settings.sandbox_data_dir / "oplog.jsonl", clock)
    signing_key = settings.guard_signing_key.get_secret_value().encode("utf-8")
    admin_key = settings.sandbox_admin_key.get_secret_value()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        engine.dispose()  # release the SQLite file (Windows will not delete an open file)

    app = FastAPI(
        title="KhataPilot sandbox (SIMULATED providers)",
        version="0.1.0",
        description="Simulated wallet, bank, messaging gateway and lender. Nothing here touches real money.",
        lifespan=lifespan,
    )
    app.state.settings, app.state.sessions, app.state.clock, app.state.oplog = settings, sessions, clock, oplog

    @app.exception_handler(ProviderError)
    def _provider_error(_request: Request, exc: ProviderError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"detail": {"code": exc.code, "message": exc.message}, "simulated": True},
        )

    def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
        if not x_admin_key or not hmac.compare_digest(x_admin_key.encode(), admin_key.encode()):
            raise ProviderError(401, "ADMIN_KEY_REQUIRED", "This simulator endpoint needs the admin key.")

    # ---- the authorization pipeline shared by every side effect ----

    def run_authorized(
        *,
        kind: str,
        params: dict[str, Any],
        header: str | None,
        idempotency_key: str | None,
        operation: str,
        log_fields: dict[str, Any],
        execute: Callable[[Session, VerifiedAuthorization, datetime], dict[str, Any]],
    ) -> dict[str, Any]:
        def reject(err: ProviderError, auth: VerifiedAuthorization | None = None) -> ProviderError:
            oplog.record(
                operation=operation,
                status="rejected",
                code=f"REJECTED_{err.code}",
                detail=err.message,
                idempotency_key=idempotency_key,
                authorization_id=auth.token_id if auth else None,
                decision_id=auth.decision_id if auth else None,
                **log_fields,
            )
            return err

        now_wall = time.time()
        try:
            auth = verify(
                header,
                key=signing_key,
                kind=kind,
                params=params,
                idempotency_key=idempotency_key,
                now=now_wall,
                skew_seconds=settings.token_clock_skew_seconds,
            )
        except AuthError as exc:
            raise reject(ProviderError(401, exc.code, exc.message)) from None

        def replay() -> dict[str, Any] | None:
            with sessions() as session:
                used = session.get(UsedAuthorization, auth.idempotency_key)
                if used is None:
                    return None
                if used.kind != auth.kind or used.params_hash != auth.params_hash:
                    raise reject(
                        ProviderError(409, "IDEMPOTENCY_CONFLICT", "This idempotency key was already used for a different request."),
                        auth,
                    )
                original = json.loads(used.response_json)
            oplog.record(
                operation=operation,
                status="replayed",
                code="IDEMPOTENT_REPLAY",
                detail="Duplicate request: returned the original result; nothing was executed twice.",
                idempotency_key=idempotency_key,
                authorization_id=auth.token_id,
                decision_id=auth.decision_id,
                ref_id=original.get("ref_id"),
                **log_fields,
            )
            return {**original, "idempotent_replay": True}

        previous = replay()
        if previous is not None:
            return previous
        try:
            check_not_expired(auth, now_wall)
        except AuthError as exc:
            raise reject(ProviderError(401, exc.code, exc.message), auth) from None

        try:
            with sessions.begin() as session:
                used = UsedAuthorization(
                    idempotency_key=auth.idempotency_key,
                    token_id=auth.token_id,
                    kind=auth.kind,
                    params_hash=auth.params_hash,
                    decision_id=auth.decision_id,
                    response_json="{}",
                    created_at=datetime.now(UTC),
                )
                session.add(used)
                session.flush()  # takes the write lock; a concurrent duplicate fails here
                result = sim(execute(session, auth, clock.now()))
                used.response_json = json.dumps(result, default=str)
        except IntegrityError:
            previous = replay()
            if previous is not None:
                return previous
            raise reject(ProviderError(409, "TOKEN_REUSED", "This authorization was already used."), auth) from None
        except ProviderError as exc:
            raise reject(exc, auth) from None

        oplog.record(
            operation=operation,
            status="ok",
            code="OK",
            detail=result.get("detail", "Executed."),
            idempotency_key=idempotency_key,
            authorization_id=auth.token_id,
            decision_id=auth.decision_id,
            ref_id=result.get("ref_id"),
            **log_fields,
        )
        return result

    def get_account(session: Session, account_id: str) -> Account:
        account = session.get(Account, account_id)
        if account is None:
            raise ProviderError(404, "ACCOUNT_NOT_FOUND", f"No account {account_id}.")
        return account

    def counterparty_account(session: Session, provider: str, number: str, name: str | None, now: datetime) -> Account:
        account_id = f"{provider}:{number}"
        account = session.get(Account, account_id)
        if account is None:
            account = Account(
                id=account_id,
                provider=provider,
                number=number,
                holder_name=name or "Unregistered holder",
                kind="bank" if provider == "bank" else "personal",
                balance_paisa=0,
                created_at=now,
            )
            session.add(account)
        return account

    # ---- health ----

    @app.get("/health")
    def health() -> dict:
        return sim({"status": "ok", "service": "khatapilot-sandbox", "now": clock.now().astimezone(BDT).isoformat()})

    # ---- payments ----

    @app.post("/v1/payments")
    def create_payment(
        body: PaymentRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        authorization: str | None = Header(default=None, alias="X-Guard-Authorization"),
    ) -> dict:
        def execute(session: Session, auth: VerifiedAuthorization, now: datetime) -> dict[str, Any]:
            if body.method != "bank" and not is_msisdn(body.to_number):
                raise ProviderError(422, "INVALID_NUMBER", "The payee number is not a valid wallet number.")
            source = get_account(session, body.from_account)
            if source.frozen:
                raise ProviderError(403, "ACCOUNT_FROZEN", f"The account is frozen ({source.frozen_reason}).")
            if source.provider != body.method:
                raise ProviderError(422, "METHOD_MISMATCH", f"{source.id} cannot pay by {body.method}.")
            if source.balance_paisa < body.amount_paisa:
                raise ProviderError(422, "INSUFFICIENT_FUNDS", "Not enough balance for this payment.")
            payee = counterparty_account(session, body.method, body.to_number, body.to_name, now)
            trx_id = next_trx_id(session, body.method)
            source.balance_paisa -= body.amount_paisa
            payee.balance_paisa += body.amount_paisa
            text = sms.outgoing_text(
                body.method, body.amount_paisa, body.to_name, body.to_number,
                source.balance_paisa, trx_id, now.astimezone(BDT), body.reference,
            )
            common = dict(
                trx_id=trx_id, amount_paisa=body.amount_paisa, reference=body.reference,
                idempotency_key=idempotency_key, authorization_id=auth.token_id,
                decision_id=auth.decision_id, occurred_at=now,
            )
            session.add(Transaction(
                account_id=source.id, direction="debit", txn_type="payment",
                balance_after_paisa=source.balance_paisa, counterparty_number=body.to_number,
                counterparty_name=body.to_name, sms_text=text, **common,
            ))
            session.add(Transaction(
                account_id=payee.id, direction="credit", txn_type="payment_received",
                balance_after_paisa=payee.balance_paisa, counterparty_number=source.number,
                counterparty_name=source.holder_name, **common,
            ))
            return {
                "status": "completed",
                "ref_id": trx_id,
                "transaction_id": trx_id,
                "from_account": source.id,
                "to_name": body.to_name,
                "to_number_masked": mask_number(body.to_number),
                "amount_paisa": body.amount_paisa,
                "fee_paisa": 0,
                "balance_after_paisa": source.balance_paisa,
                "occurred_at": now.astimezone(BDT).isoformat(),
                "sms_text": text,
                "authorization_id": auth.token_id,
                "decision_id": auth.decision_id,
                "detail": f"Paid {sms.tk(body.amount_paisa)} to {mask_number(body.to_number)}.",
            }

        return run_authorized(
            kind="payment.execute",
            params=body.model_dump(),
            header=authorization,
            idempotency_key=idempotency_key,
            operation="payment",
            log_fields={
                "account_id": body.from_account,
                "counterparty": mask_number(body.to_number),
                "amount_paisa": body.amount_paisa,
            },
            execute=execute,
        )

    # ---- freeze / unfreeze (the provider side of the kill switch) ----

    def set_frozen(account_id: str, frozen: bool, reason: str, header: str | None, key: str | None) -> dict:
        def execute(session: Session, auth: VerifiedAuthorization, now: datetime) -> dict[str, Any]:
            account = get_account(session, account_id)
            account.frozen = frozen
            account.frozen_reason = reason if frozen else None
            account.frozen_at = now if frozen else None
            state = "frozen" if frozen else "active"
            return {
                "status": state,
                "ref_id": account_id,
                "account": account_view(account),
                "detail": f"Account {account_id} is now {state}.",
            }

        return run_authorized(
            kind="account.freeze" if frozen else "account.unfreeze",
            params={"account_id": account_id},
            header=header,
            idempotency_key=key,
            operation="freeze" if frozen else "unfreeze",
            log_fields={"account_id": account_id},
            execute=execute,
        )

    @app.post("/v1/accounts/{account_id}/freeze")
    def freeze(
        account_id: str,
        body: FreezeRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        authorization: str | None = Header(default=None, alias="X-Guard-Authorization"),
    ) -> dict:
        return set_frozen(account_id, True, body.reason, authorization, idempotency_key)

    @app.post("/v1/accounts/{account_id}/unfreeze")
    def unfreeze(
        account_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        authorization: str | None = Header(default=None, alias="X-Guard-Authorization"),
    ) -> dict:
        return set_frozen(account_id, False, "", authorization, idempotency_key)

    # ---- messaging gateway ----

    @app.post("/v1/messages")
    def send_message(
        body: MessageRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        authorization: str | None = Header(default=None, alias="X-Guard-Authorization"),
    ) -> dict:
        def execute(session: Session, auth: VerifiedAuthorization, now: datetime) -> dict[str, Any]:
            if not is_msisdn(body.to_number):
                raise ProviderError(422, "INVALID_NUMBER", "The recipient number is not valid.")
            sender = get_account(session, body.from_account)
            if sender.frozen:
                raise ProviderError(403, "ACCOUNT_FROZEN", f"The account is frozen ({sender.frozen_reason}).")
            message_id = f"MSG{next_seq(session, 'msg'):06d}"
            session.add(OutboundMessage(
                id=message_id, from_account=sender.id, to_number=body.to_number, channel=body.channel,
                body=body.body, status="delivered", idempotency_key=idempotency_key,
                authorization_id=auth.token_id, decision_id=auth.decision_id, sent_at=now,
            ))
            return {
                "status": "delivered",
                "ref_id": message_id,
                "message_id": message_id,
                "to_masked": mask_number(body.to_number),
                "channel": body.channel,
                "sent_at": now.astimezone(BDT).isoformat(),
                "detail": f"Delivered a {body.channel} message to {mask_number(body.to_number)}.",
            }

        params = {**body.model_dump(), "body_sha256": sha256_hex(body.body)}
        return run_authorized(
            kind="message.send",
            params=params,
            header=authorization,
            idempotency_key=idempotency_key,
            operation="message",
            log_fields={"account_id": body.from_account, "counterparty": mask_number(body.to_number)},
            execute=execute,
        )

    @app.get("/v1/messages")
    def list_messages(from_account: str | None = None, limit: int = Query(default=50, le=500)) -> dict:
        with sessions() as session:
            query = select(OutboundMessage).order_by(OutboundMessage.sent_at.desc()).limit(limit)
            if from_account:
                query = query.where(OutboundMessage.from_account == from_account)
            rows = session.scalars(query).all()
        return sim({"messages": [
            {
                "id": m.id, "to_masked": mask_number(m.to_number), "channel": m.channel, "body": m.body,
                "status": m.status, "sent_at": m.sent_at.astimezone(BDT).isoformat(),
                "authorization_id": m.authorization_id, "decision_id": m.decision_id,
            }
            for m in rows
        ]})

    # ---- lender ----

    @app.post("/v1/lender/applications")
    def submit_application(
        body: LoanApplicationRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        authorization: str | None = Header(default=None, alias="X-Guard-Authorization"),
    ) -> dict:
        def execute(session: Session, auth: VerifiedAuthorization, now: datetime) -> dict[str, Any]:
            applicant = get_account(session, body.applicant_account)
            if applicant.frozen:
                raise ProviderError(403, "ACCOUNT_FROZEN", f"The account is frozen ({applicant.frozen_reason}).")
            app_id = f"APP{next_seq(session, 'loan'):06d}"
            session.add(LoanApplication(
                id=app_id, applicant_account=applicant.id, shop_name=body.shop_name,
                product_id=body.product_id, amount_paisa=body.amount_paisa,
                tenure_months=body.tenure_months, payload_json=json.dumps(body.payload, ensure_ascii=False),
                status="received", idempotency_key=idempotency_key, authorization_id=auth.token_id,
                decision_id=auth.decision_id, submitted_at=now,
            ))
            return {
                "status": "received",
                "ref_id": app_id,
                "application_id": app_id,
                "product_id": body.product_id,
                "amount_paisa": body.amount_paisa,
                "submitted_at": now.astimezone(BDT).isoformat(),
                "next_step": "A loan officer reviews applications within 3 working days (simulated).",
                "detail": f"Received application {app_id} for {body.product_id}.",
            }

        params = {**body.model_dump(), "payload_sha256": sha256_hex(canonical_json(body.payload))}
        return run_authorized(
            kind="loan.submit",
            params=params,
            header=authorization,
            idempotency_key=idempotency_key,
            operation="loan_application",
            log_fields={"account_id": body.applicant_account, "amount_paisa": body.amount_paisa},
            execute=execute,
        )

    @app.get("/v1/lender/applications")
    def list_applications(applicant_account: str | None = None) -> dict:
        with sessions() as session:
            query = select(LoanApplication).order_by(LoanApplication.submitted_at.desc())
            if applicant_account:
                query = query.where(LoanApplication.applicant_account == applicant_account)
            rows = session.scalars(query).all()
        return sim({"applications": [
            {
                "id": a.id, "shop_name": a.shop_name, "product_id": a.product_id, "amount_paisa": a.amount_paisa,
                "tenure_months": a.tenure_months, "status": a.status,
                "submitted_at": a.submitted_at.astimezone(BDT).isoformat(),
                "payload": json.loads(a.payload_json), "authorization_id": a.authorization_id,
                "decision_id": a.decision_id,
            }
            for a in rows
        ]})

    # ---- read-only account views ----

    @app.get("/v1/accounts")
    def list_accounts(holder: str | None = None) -> dict:
        with sessions() as session:
            query = select(Account).order_by(Account.id)
            if holder:
                query = query.where(Account.holder_name == holder)
            return sim({"accounts": [account_view(a) for a in session.scalars(query).all()]})

    @app.get("/v1/accounts/{account_id}")
    def read_account(account_id: str) -> dict:
        with sessions() as session:
            return sim(account_view(get_account(session, account_id)))

    @app.get("/v1/accounts/{account_id}/transactions")
    def statement(account_id: str, limit: int = Query(default=50, le=1000)) -> dict:
        with sessions() as session:
            get_account(session, account_id)
            rows = session.scalars(
                select(Transaction)
                .where(Transaction.account_id == account_id)
                .order_by(Transaction.occurred_at.desc(), Transaction.seq.desc())
                .limit(limit)
            ).all()
        return sim({"account_id": account_id, "transactions": [transaction_view(t) for t in rows]})

    @app.get("/v1/accounts/{account_id}/transactions/{trx_id}")
    def find_transaction(account_id: str, trx_id: str) -> dict:
        """Look up one transaction on an account by its TrxID, like a wallet's transaction-search API.
        KhataPilot uses it to check a forwarded SMS against what really happened."""
        with sessions() as session:
            get_account(session, account_id)
            row = session.scalars(select(Transaction).where(
                Transaction.account_id == account_id, Transaction.trx_id == trx_id).limit(1)).first()
            found = sim(transaction_view(row)) if row else None
        # Logged like any provider query, so the evidence shows a forwarded SMS was checked here.
        oplog.record(operation="lookup", status="ok" if found else "not_found",
                     code="OK" if found else "TRANSACTION_NOT_FOUND",
                     detail=f"Transaction {trx_id} {'found' if found else 'not found'} on this account.",
                     account_id=account_id, ref_id=trx_id, amount_paisa=found["amount_paisa"] if found else None)
        if found is None:
            raise ProviderError(404, "TRANSACTION_NOT_FOUND", f"No transaction {trx_id} on {account_id}.")
        return found

    @app.get("/v1/oplog")
    def read_oplog(since: int = 0, limit: int = Query(default=500, le=5000)) -> dict:
        with sessions() as session:
            rows = session.scalars(
                select(OpLogEntry).where(OpLogEntry.seq > since).order_by(OpLogEntry.seq).limit(limit)
            ).all()
        return sim({"entries": [to_json(r) for r in rows]})

    # ---- simulator controls (admin key) ----

    @app.post("/v1/sim/reset", dependencies=[Depends(require_admin)])
    def reset() -> dict:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        clock.reset()
        oplog.clear_file()
        oplog.record(operation="reset", status="ok", code="OK", detail="Sandbox reset to an empty state.")
        return sim({"status": "reset", "now": clock.now().astimezone(BDT).isoformat()})

    @app.post("/v1/sim/accounts", dependencies=[Depends(require_admin)])
    def seed_accounts(accounts: list[AccountSeed]) -> dict:
        with sessions.begin() as session:
            for seed in accounts:
                account = session.get(Account, seed.id)
                if account is None:
                    session.add(Account(created_at=clock.now(), **seed.model_dump()))
                else:
                    for field, value in seed.model_dump().items():
                        setattr(account, field, value)
        oplog.record(operation="seed", status="ok", code="OK", detail=f"Seeded {len(accounts)} accounts.")
        return sim({"seeded": len(accounts)})

    @app.post("/v1/sim/transactions/import", dependencies=[Depends(require_admin)])
    def import_transactions(rows: list[TransactionSeed]) -> dict:
        with sessions.begin() as session:
            for row in rows:
                session.add(Transaction(status="completed", **row.model_dump()))
        oplog.record(operation="seed", status="ok", code="OK", detail=f"Imported {len(rows)} historical transactions.")
        return sim({"imported": len(rows)})

    @app.get("/v1/sim/clock")
    def read_clock() -> dict:
        return sim({"now": clock.now().astimezone(BDT).isoformat(), "frozen": clock.frozen})

    @app.put("/v1/sim/clock", dependencies=[Depends(require_admin)])
    def set_clock(body: ClockRequest) -> dict:
        clock.set(body.now, frozen=body.frozen)
        now = clock.now().astimezone(BDT).isoformat()
        oplog.record(operation="clock", status="ok", code="OK", detail=f"Simulated clock set to {now}.")
        return sim({"now": now, "frozen": clock.frozen})

    @app.post("/v1/sim/incoming", dependencies=[Depends(require_admin)])
    def incoming(body: IncomingRequest) -> dict:
        """Simulate a customer paying the shop. Returns the SMS the owner would receive."""
        with sessions.begin() as session:
            account = get_account(session, body.to_account)
            now = clock.now()
            trx_id = next_trx_id(session, account.provider)
            account.balance_paisa += body.amount_paisa
            text = sms.incoming_text(
                account.provider, body.txn_type, body.amount_paisa, body.from_number,
                account.balance_paisa, trx_id, now.astimezone(BDT), body.reference,
            )
            session.add(Transaction(
                trx_id=trx_id, account_id=account.id, direction="credit", txn_type=body.txn_type,
                amount_paisa=body.amount_paisa, balance_after_paisa=account.balance_paisa,
                counterparty_number=body.from_number, counterparty_name=body.from_name,
                reference=body.reference, sms_text=text, occurred_at=now,
            ))
            balance = account.balance_paisa
        oplog.record(
            operation="incoming", status="ok", code="OK",
            detail=f"Customer payment of {sms.tk(body.amount_paisa)} received.",
            account_id=body.to_account, counterparty=mask_number(body.from_number),
            amount_paisa=body.amount_paisa, ref_id=trx_id,
        )
        return sim({"trx_id": trx_id, "balance_after_paisa": balance, "sms_text": text})

    return app
