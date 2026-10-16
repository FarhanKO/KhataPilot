"""Owner-only controls: STOP and resume, limits, trusted payees, blocked numbers. (Consent for the
credit profile lives with the credit module: `interface/api/credit.py`.)

These are not LLM tools. Anything that makes the shop less safe needs the PIN; STOP never does.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.ids import new_id
from app.core.money import fmt_taka
from app.core.provenance import Sender
from app.guard.killswitch import KillSwitchResult
from app.guard.owner_controls import ControlResult, OwnerControls
from app.guard.service import Guard
from app.interface.deps import get_container, require_owner

router = APIRouter(prefix="/owner", tags=["owner controls"])


@contextmanager
def controls(container) -> Iterator[tuple[Guard, OwnerControls]]:
    with container.db.session() as session:
        guard = Guard(session, container.guard_deps)
        yield guard, OwnerControls(clock=container.clock, store=guard.store, pins=guard.pins,
                                   kill_switch=guard.kill_switch, recorder=guard.recorder)


class StopRequest(BaseModel):
    reason: str = Field(default="The owner pressed STOP", max_length=200)


class PinRequest(BaseModel):
    pin: str = Field(max_length=12)


class LimitsRequest(PinRequest):
    per_txn_taka: int | None = Field(default=None, gt=0, le=10_000_000)
    daily_taka: int | None = Field(default=None, gt=0, le=10_000_000)


class PayeeRequest(PinRequest):
    name: str = Field(min_length=2, max_length=128)
    number: str = Field(min_length=6, max_length=32)
    method: Literal["bkash", "nagad", "bank"] = "bkash"


class BlockRequest(BaseModel):
    number: str = Field(min_length=10, max_length=20)
    reason: str = Field(default="Reported by the owner", max_length=200)


@router.get("/status")
def status(container=Depends(get_container), _owner: Sender = Depends(require_owner)) -> dict:
    with controls(container) as (guard, _):
        limits = guard.store.limits()
        switch = guard.store.kill_switch()
        locked, until = guard.pins.lock_state()
        return {
            "kill_switch": {"on": switch.frozen, "since": switch.since, "reason": switch.reason},
            "limits": {"per_payment": fmt_taka(limits.per_txn_paisa), "per_day": fmt_taka(limits.daily_paisa),
                       "per_payment_paisa": limits.per_txn_paisa, "per_day_paisa": limits.daily_paisa},
            "pin": {"set": guard.pins.is_set(), "locked": locked, "locked_until": until},
            "trusted_payees": [{"id": p.id, "name": p.name, "number": p.number_masked, "method": p.method,
                                "added_via": p.added_via} for p in guard.store.payees()],
            "credit_profile_consent": guard.credit.consent().granted,
        }


@router.post("/stop", response_model=KillSwitchResult)
def stop(body: StopRequest, container=Depends(get_container), _owner: Sender = Depends(require_owner)):
    with controls(container) as (_, owner):
        return owner.stop(reason=body.reason, request_id=new_id("req"))


@router.post("/resume", response_model=KillSwitchResult)
def resume(body: PinRequest, container=Depends(get_container), _owner: Sender = Depends(require_owner)):
    with controls(container) as (_, owner):
        return owner.resume(body.pin, request_id=new_id("req"))


@router.put("/limits", response_model=ControlResult)
def set_limits(body: LimitsRequest, container=Depends(get_container), _owner: Sender = Depends(require_owner)):
    with controls(container) as (_, owner):
        return owner.set_limits(body.pin, per_txn_taka=body.per_txn_taka, daily_taka=body.daily_taka,
                                request_id=new_id("req"))


@router.post("/payees", response_model=ControlResult)
def add_payee(body: PayeeRequest, container=Depends(get_container), _owner: Sender = Depends(require_owner)):
    with controls(container) as (_, owner):
        return owner.add_payee(body.pin, name=body.name, number=body.number, method=body.method,
                               request_id=new_id("req"))


@router.post("/payees/{payee_id}/remove", response_model=ControlResult)
def remove_payee(payee_id: str, body: PinRequest, container=Depends(get_container),
                 _owner: Sender = Depends(require_owner)):
    with controls(container) as (_, owner):
        return owner.remove_payee(body.pin, payee_id=payee_id, request_id=new_id("req"))


@router.post("/block", response_model=ControlResult)
def block(body: BlockRequest, container=Depends(get_container), _owner: Sender = Depends(require_owner)):
    with controls(container) as (_, owner):
        return owner.block_number(body.number, body.reason, request_id=new_id("req"))
