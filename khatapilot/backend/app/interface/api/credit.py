"""Credit profile, consent, loans and the owner's data. Consent can only be given or withdrawn here,
by the authenticated owner; no agent tool can do either."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.ids import new_id
from app.core.masking import mask_text
from app.core.money import to_paisa
from app.core.provenance import Provenance, Segment, Sender, TurnContext
from app.guard.gateway import ToolCall, ToolOutcome
from app.guard.service import Guard
from app.interface.api.views import _masked
from app.interface.deps import get_container, require_owner
from app.modules.credit.schemas import ConsentRequired, ConsentView, CreditProfile, NotEnoughHistory
from app.modules.credit.service import CreditModelMissing
from app.modules.loans.schemas import LoanApplicationView, LoanOptions, LoanProduct, Purpose

router = APIRouter(tags=["credit"])


def _guard(container, session) -> Guard:
    return Guard(session, container.guard_deps)


# ---- consent

@router.get("/credit/consent", response_model=ConsentView)
def consent(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> ConsentView:
    with container.db.session() as session:
        return _guard(container, session).credit.consent()


@router.post("/credit/consent", response_model=ConsentView)
def grant_consent(container=Depends(get_container), owner: Sender = Depends(require_owner)) -> ConsentView:
    with container.db.session() as session:
        guard = _guard(container, session)
        view = guard.credit.grant_consent()
        guard.recorder.event(request_id=new_id("req"), channel="web", actor="owner", provenance="owner",
                             event="consent", summary="Agreed to a credit profile", decision="GRANTED",
                             reason=f"Consent text {view.version}.")
        return view


@router.delete("/credit/consent")
def withdraw_consent(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    """Withdraw consent: stored profiles are deleted and unsent loan applications are cancelled."""
    request_id = new_id("req")
    with container.db.session() as session:
        guard = _guard(container, session)
        deleted = guard.credit.withdraw_consent()
        cancelled = 0
        for pending in guard.approvals.pending():
            if pending.kind == "loan.submit":
                guard.approvals.reject(pending.id, request_id=request_id)
                cancelled += 1
        guard.recorder.event(request_id=request_id, channel="web", actor="owner", provenance="owner", event="consent",
                             summary="Withdrew consent for a credit profile", decision="WITHDRAWN",
                             reason=f"Deleted {deleted} stored profile(s); cancelled {cancelled} unsent application(s).")
        return {"withdrawn": True, "deleted_profiles": deleted, "cancelled_applications": cancelled}


# ---- the profile

@router.get("/credit/profile", response_model=CreditProfile | None)
def latest_profile(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> CreditProfile | None:
    with container.db.session() as session:
        return _guard(container, session).credit.latest()


@router.post("/credit/profile", response_model=CreditProfile)
def build_profile(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> CreditProfile:
    with container.db.session() as session:
        try:
            return _guard(container, session).credit.build()
        except ConsentRequired as exc:
            raise HTTPException(409, "Agree to the credit profile first (POST /api/credit/consent).") from exc
        except NotEnoughHistory as exc:
            raise HTTPException(422, str(exc)) from exc
        except CreditModelMissing as exc:
            raise HTTPException(503, str(exc)) from exc


# ---- loans

@router.get("/loans/products", response_model=list[LoanProduct])
def products(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> list[LoanProduct]:
    with container.db.session() as session:
        return _guard(container, session).loans.products()


@router.get("/loans/options", response_model=LoanOptions)
def options(amount_taka: Decimal | None = Query(default=None, gt=0, le=10_000_000),
            purpose: Purpose = "working_capital", container=Depends(get_container),
            _owner: Sender = Depends(require_owner)) -> LoanOptions:
    with container.db.session() as session:
        try:
            return _guard(container, session).loans.options(to_paisa(amount_taka) if amount_taka else None, purpose)
        except ConsentRequired as exc:
            raise HTTPException(409, "Agree to the credit profile first (POST /api/credit/consent).") from exc
        except (NotEnoughHistory, CreditModelMissing) as exc:
            raise HTTPException(422, str(exc)) from exc


class ApplyRequest(BaseModel):
    product_id: str = Field(max_length=64)
    amount_taka: Decimal | None = Field(default=None, gt=0, le=10_000_000)
    tenure_months: int | None = Field(default=None, ge=1, le=60)
    purpose: Purpose = "working_capital"


@router.post("/loans/apply", response_model=ToolOutcome)
def apply(body: ApplyRequest, container=Depends(get_container), owner: Sender = Depends(require_owner)) -> ToolOutcome:
    """The Loans tab's Apply button. It goes through the guard like any other request: the application is
    pre-filled and held, and is sent only when the owner approves it."""
    text = f"Apply for {body.product_id}" + (f", ৳{body.amount_taka}" if body.amount_taka else "") + " (Loans tab)"
    turn = TurnContext(request_id=new_id("req"), conversation_id="loans-tab", shop_id=container.settings.shop_id,
                       channel="web", sender=owner, segments=(Segment(text=text, provenance=Provenance.OWNER,
                                                                      source="chat"),),
                       received_at=container.clock.now())
    arguments = body.model_dump(mode="json", exclude_none=True)
    with container.db.session() as session:
        return _guard(container, session).gateway.submit(ToolCall(name="prefill_application", arguments=arguments), turn)


@router.get("/loans/applications")
def applications(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    with container.db.session() as session:
        guard = _guard(container, session)
        waiting = [p.model_dump(mode="json") for p in guard.approvals.pending() if p.kind == "loan.submit"]
        sent: list[LoanApplicationView] = guard.loans.applications()
        return {"waiting_for_approval": waiting, "sent": [a.model_dump(mode="json") for a in sent]}


# ---- the owner's data

@router.get("/data/export")
def export(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    """Everything KhataPilot holds about the shop, as JSON (phone numbers masked, as everywhere in the UI)."""
    shop_id = container.settings.shop_id
    with container.db.session() as session:
        guard = _guard(container, session)
        profile = guard.shops.get(shop_id)
        entries = guard.ledger.entries(shop_id, limit=1_000_000)
        return {
            "simulated": True,
            "exported_at": container.clock.now().isoformat(),
            "shop": profile.model_dump(mode="json", exclude={"owner_phone"}) if profile else None,
            "ledger": [{**e.model_dump(mode="json"), "counterparty_number": _masked(e.counterparty_number),
                        "note": mask_text(e.note) if e.note else None} for e in entries],
            "obligations": [{**o.model_dump(mode="json"), "counterparty_number": _masked(o.counterparty_number)}
                            for o in guard.ledger.open_obligations(shop_id)],
            "payments": [{**p.model_dump(mode="json", exclude={"idempotency_key"}),
                          "payee_number": _masked(p.payee_number)} for p in guard.payments.list(limit=100_000)],
            "credit_consent": guard.credit.consent().model_dump(mode="json"),
            "credit_profile": (latest.model_dump(mode="json") if (latest := guard.credit.latest()) else None),
            "loan_applications": [a.model_dump(mode="json") for a in guard.loans.applications()],
        }
