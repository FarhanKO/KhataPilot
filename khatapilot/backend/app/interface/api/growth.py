"""How the owner could grow the shop: the Savings tab's growth section (read-only; see app.modules.growth)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.provenance import Sender
from app.guard.service import Guard
from app.interface.deps import get_container, require_owner

router = APIRouter(tags=["growth"])


@router.get("/growth")
def growth(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    with container.db.session() as session:
        try:
            return Guard(session, container.guard_deps).growth.plan().model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(409, f"Not enough history yet: {exc}.") from exc
