"""The owner's monthly savings: the Savings tab's view, its share setting and its "Move to savings"
button. The button goes through the guard like a chat request, so the transfer is checked like any
payment and waits for the owner's PIN. The account money goes to is set on the owner controls route
(`PUT /api/owner/savings-account`), with the PIN."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.ids import new_id
from app.core.provenance import Provenance, Segment, Sender, TurnContext
from app.guard.gateway import ToolCall, ToolOutcome
from app.guard.service import Guard
from app.interface.deps import get_container, require_owner
from app.interface.jobs import check_savings
from app.modules.savings.schemas import MAX_SHARE_PCT, MIN_SHARE_PCT

router = APIRouter(tags=["savings"])


def _view(guard: Guard) -> dict:
    status = guard.savings.status(guard.forecasts.forecast(guard.forecast_days))
    data = status.model_dump(mode="json")
    if data["account"]:
        data["account"].pop("number")  # the screen only ever shows the masked number
    return data


@router.get("/savings")
def savings(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    with container.db.session() as session:
        return _view(Guard(session, container.guard_deps))


class ShareRequest(BaseModel):
    share_pct: int | None = Field(default=None, ge=MIN_SHARE_PCT, le=MAX_SHARE_PCT, description="None pauses the plan.")


@router.get("/savings/products")
def products(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    """Where to keep the savings: real products, dated and sourced, and which fit this shop."""
    with container.db.session() as session:
        return Guard(session, container.guard_deps).savings.products()


@router.put("/savings/share")
def set_share(body: ShareRequest, container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    """No PIN: the share moves no money, and every transfer still waits for the PIN."""
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        before = guard.savings.share()
        guard.savings.set_share(body.share_pct)
        what = f"Save {body.share_pct}% of each month's profit" if body.share_pct else "Paused the savings plan"
        guard.recorder.event(request_id=new_id("req"), channel="web", actor="owner", provenance="owner",
                             event="owner_control", summary=what, decision="CHANGED",
                             reason=f"Was {f'{before}%' if before else 'not set'}. No money moves until the owner "
                                    "confirms a transfer with the PIN.")
        return _view(guard)


class BasisRequest(BaseModel):
    basis: Literal["last_month", "three_months"]


@router.put("/savings/basis")
def set_basis(body: BasisRequest, container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    """What the target is a share of: last month's profit, or the middle of the last three (steadier)."""
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        guard.savings.set_basis(body.basis)
        guard.recorder.event(request_id=new_id("req"), channel="web", actor="owner", provenance="owner",
                             event="owner_control", decision="CHANGED",
                             summary=("Base savings on the last three months" if body.basis == "three_months"
                                      else "Base savings on last month"),
                             reason="No money moves until the owner confirms a transfer with the PIN.")
        return _view(guard)


class TransferRequest(BaseModel):
    amount_taka: int | None = Field(default=None, gt=0, le=10_000_000, description="None: what the plan suggests.")


@router.post("/savings/prepare", response_model=ToolOutcome)
def prepare(body: TransferRequest, container=Depends(get_container), owner: Sender = Depends(require_owner)) -> ToolOutcome:
    text = (f"Move ৳{body.amount_taka:,} to my savings (Savings tab)" if body.amount_taka
            else "Move this month's savings (Savings tab)")
    turn = TurnContext(request_id=new_id("req"), conversation_id="savings-tab", shop_id=container.settings.shop_id,
                       channel="web", sender=owner, segments=(Segment(text=text, provenance=Provenance.OWNER,
                                                                      source="chat"),),
                       received_at=container.clock.now())
    arguments = {"amount_taka": body.amount_taka} if body.amount_taka else {}
    with container.db.session() as session:
        return Guard(session, container.guard_deps).gateway.submit(
            ToolCall(name="save_to_savings", arguments=arguments), turn)


def _tab_turn(container, owner: Sender, text: str) -> TurnContext:
    return TurnContext(request_id=new_id("req"), conversation_id="savings-tab", shop_id=container.settings.shop_id,
                       channel="web", sender=owner, segments=(Segment(text=text, provenance=Provenance.OWNER,
                                                                      source="chat"),),
                       received_at=container.clock.now())


class GoalRequest(BaseModel):
    name: str = Field(min_length=2, max_length=48)
    amount_taka: int = Field(gt=0, le=10_000_000)
    by_month: str | None = Field(default=None, pattern=r"^20\d\d-(0[1-9]|1[0-2])$")


@router.post("/savings/goals", response_model=ToolOutcome)
def add_goal(body: GoalRequest, container=Depends(get_container), owner: Sender = Depends(require_owner)) -> ToolOutcome:
    """The Savings tab's goal form, through the guard like the same words typed in chat."""
    by = f" by {body.by_month}" if body.by_month else ""
    turn = _tab_turn(container, owner, f"Savings goal {body.name}: ৳{body.amount_taka:,}{by} (Savings tab)")
    arguments = {"name": body.name, "amount_taka": body.amount_taka, **({"by_month": body.by_month} if body.by_month else {})}
    with container.db.session() as session:
        return Guard(session, container.guard_deps).gateway.submit(ToolCall(name="set_savings_goal",
                                                                            arguments=arguments), turn)


@router.delete("/savings/goals/{goal_id}", response_model=ToolOutcome)
def remove_goal(goal_id: str, container=Depends(get_container), owner: Sender = Depends(require_owner)) -> ToolOutcome:
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        goal = next((g for g in guard.savings.goals() if g.id == goal_id), None)
        if goal is None:
            raise HTTPException(404, "No such goal (it may have been removed already).")
        turn = _tab_turn(container, owner, f"Remove the savings goal {goal.name} (Savings tab)")
        return guard.gateway.submit(ToolCall(name="remove_savings_goal", arguments={"name": goal.name}), turn)


@router.post("/savings/goals/starters", response_model=ToolOutcome)
def starter_goals(container=Depends(get_container), owner: Sender = Depends(require_owner)) -> ToolOutcome:
    turn = _tab_turn(container, owner, "Suggest savings goals from my books (Savings tab)")
    with container.db.session() as session:
        return Guard(session, container.guard_deps).gateway.submit(ToolCall(name="add_starter_goals", arguments={}),
                                                                   turn)


@router.post("/sim/savings-check")
def savings_check(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    """Run the daily look at this month's savings now (the background loop runs it once a simulated day)."""
    return check_savings(container)
