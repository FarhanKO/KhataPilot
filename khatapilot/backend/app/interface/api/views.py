"""Read-only views for the app's tabs, plus the demo's money simulator. Numbers are masked."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.clock import SimClock, local_date
from app.core.masking import mask_phone, mask_text, normalize_msisdn
from app.core.money import fmt_taka, to_paisa
from app.core.provenance import Sender
from app.guard.service import Guard
from app.infra.sandbox_client import SandboxError
from app.interface.deps import get_container, require_owner
from app.interface.jobs import check_cash, run_due_payments

router = APIRouter(tags=["views"])


def _masked(number: str | None) -> str | None:
    if not number:
        return number
    return mask_phone(number) if normalize_msisdn(number) else f"****{number[-4:]}"


@router.get("/shop")
def shop(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    with container.db.session() as session:
        profile = Guard(session, container.guard_deps).shops.get(container.settings.shop_id)
        if profile is None:
            raise HTTPException(404, "The demo shop is not loaded. Run `python scripts/seed.py`.")
        return {**profile.model_dump(mode="json"), "owner_phone": mask_phone(profile.owner_phone),
                "llm_mode": container.settings.llm_mode, "model": container.provider.model,
                "risk_model": container.risk_model_name, "now": container.clock.now().isoformat()}


@router.get("/ledger/today")
def ledger_today(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        day = local_date(container.clock.now())
        totals = guard.ledger.day_totals(container.settings.shop_id, day)
        balances = guard.ledger.balances(container.settings.shop_id)
        return {"totals": totals.model_dump(mode="json"), "balances": balances,
                "total_on_hand": sum(balances.values())}


@router.get("/ledger/entries")
def ledger_entries(limit: int = Query(default=50, le=500), offset: int = Query(default=0, ge=0),
                   container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        entries = guard.ledger.entries(container.settings.shop_id, limit=limit, offset=offset)
        return {"total": guard.ledger.count(container.settings.shop_id), "entries": [
            {**e.model_dump(mode="json"), "counterparty_number": _masked(e.counterparty_number),
             "note": mask_text(e.note) if e.note else None} for e in entries]}


@router.get("/debtors")
def debtors(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> list[dict]:
    with container.db.session() as session:
        rows = Guard(session, container.guard_deps).ledger.baki_by_customer(container.settings.shop_id)
        return [{**r.model_dump(mode="json"), "number": _masked(r.number)} for r in rows]


@router.get("/obligations")
def obligations(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> list[dict]:
    with container.db.session() as session:
        rows = Guard(session, container.guard_deps).ledger.open_obligations(container.settings.shop_id)
        return [{**r.model_dump(mode="json"), "counterparty_number": _masked(r.counterparty_number)} for r in rows]


@router.get("/forecast")
def forecast(days: int = Query(default=14, ge=7, le=28), container=Depends(get_container),
             _owner: Sender = Depends(require_owner)) -> dict:
    """The day-by-day cash forecast, why it looks that way, and what would fix a shortfall."""
    with container.db.session() as session:
        result = Guard(session, container.guard_deps).forecasts.forecast(days)
    body = result.model_dump(mode="json")
    for day in body["days"]:
        for item in day["items"]:
            item["label"] = mask_text(item["label"])
    return body


@router.get("/payments")
def payments(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> list[dict]:
    with container.db.session() as session:
        rows = Guard(session, container.guard_deps).payments.list(limit=100)
        return [{**r.model_dump(mode="json", exclude={"idempotency_key"}), "payee_number": _masked(r.payee_number)}
                for r in rows]


@router.get("/audit")
def audit(limit: int = Query(default=100, le=2000), event: str | None = None,
          since: int = Query(default=0, ge=0, description="Only entries after this sequence number."),
          oldest_first: bool = False, container=Depends(get_container),
          _owner: Sender = Depends(require_owner)) -> list[dict]:
    entries = container.audit.entries(since_seq=since, limit=limit, event=event, newest_first=not oldest_first)
    return [e.model_dump() for e in entries]


@router.get("/audit/verify")
def audit_verify(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    return container.audit.verify().model_dump()


@router.get("/llm/usage")
def llm_usage(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    """Tokens the model has used since the backend started, and their rough cost at list prices."""
    from app.agent.llm.anthropic_provider import estimate_cost

    provider = container.provider
    usage = dict(getattr(provider, "usage", None) or {})
    cost = estimate_cost(provider.model, usage) if usage else None
    return {"provider": provider.name, "model": provider.model, "usage": usage,
            "estimated_cost_usd": round(cost, 4) if cost is not None else None}


@router.get("/trace/recent")
def trace_recent(limit: int = Query(default=50, le=300), container=Depends(get_container),
                 _owner: Sender = Depends(require_owner)) -> list[dict]:
    return container.trace.recent(limit)


@router.get("/sandbox/accounts")
def sandbox_accounts(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    try:
        return {"simulated": True, "accounts": container.sandbox.accounts(holder="Rahman Store")}
    except Exception as exc:  # the provider is a separate process; the app keeps working without it
        raise HTTPException(503, f"The sandbox is not reachable ({type(exc).__name__}).") from exc


@router.get("/sandbox/accounts/{account_id}/transactions")
def sandbox_statement(account_id: str, limit: int = Query(default=30, le=200), container=Depends(get_container),
                      _owner: Sender = Depends(require_owner)) -> dict:
    """The provider's statement for one of the shop's own accounts (SIMULATED)."""
    with container.db.session() as session:
        profile = Guard(session, container.guard_deps).shops.get(container.settings.shop_id)
    own = {profile.bkash_account, profile.nagad_account, profile.bank_account} if profile else set()
    if account_id not in own:
        raise HTTPException(404, "Not one of the shop's accounts.")
    try:
        return {"simulated": True, "account_id": account_id,
                "transactions": container.sandbox.statement(account_id, limit=limit)}
    except Exception as exc:  # the provider is a separate process
        raise HTTPException(503, f"The sandbox is not reachable ({type(exc).__name__}).") from exc


class Advance(BaseModel):
    hours: float = Field(gt=0, le=24 * 31)


@router.post("/sim/advance")
def advance_clock(body: Advance, container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    """Demo: move the simulated clock forward (backend and provider together), e.g. to Friday."""
    if not isinstance(container.clock, SimClock):
        raise HTTPException(409, "The clock is real time (SIM_TIME is not set), so it cannot be moved.")
    container.clock.advance(timedelta(hours=body.hours))
    now = container.clock.now()
    try:
        container.sandbox.set_clock(now, frozen=container.clock.frozen)
        provider = "in step"
    except Exception as exc:  # the provider is a separate process
        provider = f"not updated ({type(exc).__name__})"
    return {"now": now.isoformat(), "provider_clock": provider}


@router.post("/sim/restart")
def restart_clock(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    """Demo: after the shop is reloaded (`scripts/demo_run.py`), put the simulated clock back to SIM_TIME
    for the backend and the provider, and clear the live decision trace."""
    start = container.settings.sim_time
    if not isinstance(container.clock, SimClock) or start is None:
        raise HTTPException(409, "The clock is real time (SIM_TIME is not set), so it cannot be restarted.")
    container.clock.set(start)
    now = container.clock.now()
    try:
        container.sandbox.set_clock(now, frozen=container.clock.frozen)
        provider = "in step"
    except Exception as exc:  # the provider is a separate process
        provider = f"not updated ({type(exc).__name__})"
    container.trace.clear()
    return {"now": now.isoformat(), "provider_clock": provider}


@router.post("/sim/run-due")
def run_due(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    """Run scheduled payments that are due now (the background scheduler does this every minute)."""
    results = run_due_payments(container)
    return {"ran": len(results), "results": [{"status": r.status, "message": r.message} for r in results]}


@router.post("/sim/cash-check")
def cash_check(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    """Run the daily cash look-ahead now (the background loop runs it once a simulated day)."""
    return check_cash(container)


class IncomingPayment(BaseModel):
    from_number: str = Field(max_length=20)
    amount_taka: Decimal = Field(gt=0, le=1_000_000)
    from_name: str | None = Field(default=None, max_length=64)
    provider: str = Field(default="bkash", pattern="^(bkash|nagad)$")


@router.post("/sim/incoming-payment")
def incoming_payment(body: IncomingPayment, container=Depends(get_container),
                     _owner: Sender = Depends(require_owner)) -> dict:
    """Demo: a customer pays the shop through the simulated provider, which returns the SMS the owner
    receives. The owner can then forward that SMS to KhataPilot."""
    number = normalize_msisdn(body.from_number)
    if number is None:
        raise HTTPException(422, "from_number must be a Bangladeshi mobile number.")
    with container.db.session() as session:
        profile = Guard(session, container.guard_deps).shops.get(container.settings.shop_id)
    account = profile.bkash_account if body.provider == "bkash" else profile.nagad_account
    try:
        result = container.sandbox.simulate_incoming(account, number, to_paisa(body.amount_taka),
                                                     from_name=body.from_name)
    except SandboxError as exc:
        raise HTTPException(502, f"The sandbox refused: {exc.message}") from exc
    return {"simulated": True, "sms_text": result["sms_text"], "trx_id": result["trx_id"],
            "balance": fmt_taka(result["balance_after_paisa"])}
