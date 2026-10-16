"""The approval and PIN dialogs. The PIN arrives only here, never through chat or the model."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.ids import new_id
from app.core.provenance import Sender
from app.guard.approvals import ApprovalResult, PendingView
from app.guard.service import Guard
from app.interface.deps import get_container, require_owner
from app.modules.chat.service import ChatService

router = APIRouter(prefix="/approvals", tags=["approvals"])


# Outcomes the owner settled in the app. A wrong PIN is not one: the dialog stays open for another try.
SETTLED = {"completed", "scheduled", "sent", "submitted", "failed", "locked", "refused", "rejected", "expired"}


def _tell_chat(container, session, view: PendingView | None, result: ApprovalResult, request_id: str) -> None:
    """Add what the owner decided in the app to the conversation the action came from, so the model's history
    matches what really happened (it said "waiting for your PIN"; now it can see it was approved or cancelled)."""
    if view is None or not view.conversation_id or result.status not in SETTLED:
        return
    if result.status == "rejected":
        text = f'Update from the app: you cancelled "{view.summary}". Nothing was done.'
    elif result.ok:
        text = f'Update from the app: you approved "{view.summary}". {result.message}'
    else:
        text = f'Update from the app: "{view.summary}" was not done. {result.message}'
    ChatService(session, container.clock, container.settings.shop_id).append(
        view.conversation_id, "assistant", text, display=text, sender="app", request_id=request_id)


class ApproveRequest(BaseModel):
    pin: str | None = Field(default=None, max_length=12)


@router.get("", response_model=list[PendingView])
def pending(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> list[PendingView]:
    with container.db.session() as session:
        return Guard(session, container.guard_deps).approvals.pending()


@router.post("/{pending_id}/approve", response_model=ApprovalResult)
def approve(pending_id: str, body: ApproveRequest, container=Depends(get_container),
            _owner: Sender = Depends(require_owner)) -> ApprovalResult:
    request_id = new_id("req")
    with container.db.session() as session:
        approvals = Guard(session, container.guard_deps).approvals
        view = approvals.get(pending_id)
        result = approvals.approve(pending_id, pin=body.pin, request_id=request_id, channel="web")
        _tell_chat(container, session, view, result, request_id)
        return result


@router.post("/{pending_id}/reject", response_model=ApprovalResult)
def reject(pending_id: str, container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> ApprovalResult:
    request_id = new_id("req")
    with container.db.session() as session:
        approvals = Guard(session, container.guard_deps).approvals
        view = approvals.get(pending_id)
        result = approvals.reject(pending_id, request_id=request_id)
        _tell_chat(container, session, view, result, request_id)
        return result
