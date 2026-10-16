"""Baki reminders: what was sent and who has paid since, and the Ledger tab's Prepare button, which goes
through the guard like a chat request (the reminders are held until the owner approves them)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.ids import new_id
from app.core.provenance import Provenance, Segment, Sender, TurnContext
from app.guard.gateway import ToolCall, ToolOutcome
from app.guard.service import Guard
from app.interface.deps import get_container, require_owner
from app.interface.jobs import check_baki
from app.modules.reminders.schemas import ReminderView

router = APIRouter(tags=["reminders"])


@router.get("/reminders")
def reminders(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    with container.db.session() as session:
        rows: list[ReminderView] = Guard(session, container.guard_deps).reminders.sent(limit=200)
        first = {"paid": 0, "part_paid": 1, "waiting": 2, "not_sent": 3}  # who paid since, first
        shown = sorted(rows, key=lambda r: first[r.outcome])[:50]
        return {"reminders": [r.model_dump(mode="json", exclude={"decision_id"}) for r in shown],
                "paid": sum(1 for r in rows if r.outcome == "paid"),
                "part_paid": sum(1 for r in rows if r.outcome == "part_paid"),
                "waiting": sum(1 for r in rows if r.outcome == "waiting")}


class PrepareRequest(BaseModel):
    min_days: int = Field(default=14, ge=1, le=365)
    language: Literal["english", "banglish"] = "english"


@router.post("/reminders/prepare", response_model=ToolOutcome)
def prepare(body: PrepareRequest, container=Depends(get_container), owner: Sender = Depends(require_owner)) -> ToolOutcome:
    text = f"Remind customers who have owed for {body.min_days} days or more (Ledger tab)"
    turn = TurnContext(request_id=new_id("req"), conversation_id="ledger-tab", shop_id=container.settings.shop_id,
                       channel="web", sender=owner, segments=(Segment(text=text, provenance=Provenance.OWNER,
                                                                      source="chat"),),
                       received_at=container.clock.now())
    with container.db.session() as session:
        return Guard(session, container.guard_deps).gateway.submit(
            ToolCall(name="send_reminders", arguments=body.model_dump()), turn)


@router.post("/sim/baki-check")
def baki_check(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    """Run the daily look at overdue baki now (the background loop runs it once a simulated day)."""
    return check_baki(container)
