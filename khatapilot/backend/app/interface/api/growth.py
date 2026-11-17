"""How the owner could grow the shop: the Savings tab's growth section (read-only; see app.modules.growth),
and the owner's own choice of how much profit goes back into stock (it moves no money)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.ids import new_id
from app.core.provenance import Sender
from app.guard.service import Guard
from app.interface.deps import get_container, require_owner

router = APIRouter(tags=["growth"])


def _plan(guard: Guard) -> dict:
    try:
        return guard.growth.plan().model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(409, f"Not enough history yet: {exc}.") from exc


@router.get("/growth")
def growth(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    with container.db.session() as session:
        return _plan(Guard(session, container.guard_deps))


class ReinvestRequest(BaseModel):
    share_pct: int | None = Field(default=None, ge=5, le=50, description="None: KhataPilot sizes it from the books.")


@router.put("/growth/reinvest")
def set_reinvest(body: ReinvestRequest, container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    """No PIN: it only changes the plan's stock path; nothing moves."""
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        before = guard.savings.reinvest_share()
        guard.savings.set_reinvest_share(body.share_pct)
        guard.recorder.event(
            request_id=new_id("req"), channel="web", actor="owner", provenance="owner", event="owner_control",
            decision="CHANGED",
            summary=(f"Plan to put {body.share_pct}% of profit back into stock" if body.share_pct
                     else "Let KhataPilot size how much profit goes back into stock"),
            reason=f"Was {f'{before}%' if before else 'sized from the books'}. It moves no money.")
        return _plan(guard)
