"""The owner screen's controls: limits, the trusted-payee list, the PIN, and the kill switch.

This class is reachable only from the interface layer's PIN-protected endpoints (an architecture
test enforces that nothing else imports this module). Making things safer (STOP, blocking a number)
needs no PIN; making things less safe always does. The owner can also ask for limit, payee and
block-list changes in chat: those are guard-held proposals (`control_tools.py`) that only take
effect when the owner approves them in the app, with the PIN where this screen asks for one. The
PIN itself and resuming after STOP stay on this screen only.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.core.clock import Clock
from app.core.masking import mask_number, normalize_msisdn
from app.core.money import to_paisa
from app.guard import control_changes as changes
from app.guard.killswitch import KillSwitchResult, KillSwitchService
from app.guard.pin import PinManager, PinPolicyError
from app.guard.policy_store import PolicyStore, normalize_number
from app.guard.recorder import Recorder


class ControlResult(BaseModel):
    ok: bool
    message: str
    attempts_left: int | None = None
    data: dict[str, Any] = {}


class OwnerControls:
    def __init__(self, *, clock: Clock, store: PolicyStore, pins: PinManager, kill_switch: KillSwitchService,
                 recorder: Recorder) -> None:
        self.clock = clock
        self.store = store
        self.pins = pins
        self.kill_switch = kill_switch
        self.recorder = recorder

    def _audit(self, request_id: str, channel: str, summary: str, decision: str, reason: str,
               action: dict[str, Any] | None = None) -> None:
        self.recorder.event(request_id=request_id, channel=channel, actor="owner", provenance="owner",
                            event="owner_control", summary=summary, decision=decision, reason=reason, action=action)

    def _pin_ok(self, pin: str | None, request_id: str, channel: str, what: str) -> ControlResult | None:
        check = self.pins.verify(pin)
        self.recorder.event(request_id=request_id, channel=channel, actor="owner", provenance="owner", event="pin",
                            summary=f"PIN entered to {what}",
                            decision="PIN_OK" if check.ok else ("PIN_LOCKED" if check.locked else "PIN_WRONG"),
                            reason=check.message)
        return None if check.ok else ControlResult(ok=False, message=check.message, attempts_left=check.attempts_left)

    def set_limits(self, pin: str | None, *, per_txn_taka: int | None, daily_taka: int | None, request_id: str,
                   channel: str = "web") -> ControlResult:
        if refused := self._pin_ok(pin, request_id, channel, "change limits"):
            return refused
        result = changes.set_limits(self.store, per_txn_paisa=to_paisa(per_txn_taka) if per_txn_taka is not None else None,
                                    daily_paisa=to_paisa(daily_taka) if daily_taka is not None else None)
        if result.ok:
            self._audit(request_id, channel, "Changed payment limits", "CHANGED", result.message, result.data)
        return ControlResult(ok=result.ok, message=result.message)

    def add_payee(self, pin: str | None, *, name: str, number: str, method: str, aliases: tuple[str, ...] = (),
                  request_id: str, channel: str = "web") -> ControlResult:
        if refused := self._pin_ok(pin, request_id, channel, "add a trusted payee"):
            return refused
        result = changes.add_payee(self.store, name=name, number=number, method=method, via="owner_pin",
                                   aliases=aliases)
        if result.ok:
            self._audit(request_id, channel, "Added a trusted payee", "CHANGED", result.message, result.data)
        return ControlResult(ok=result.ok, message=result.message, data=result.data)

    def remove_payee(self, pin: str | None, *, payee_id: str, request_id: str, channel: str = "web") -> ControlResult:
        if refused := self._pin_ok(pin, request_id, channel, "remove a trusted payee"):
            return refused
        result = changes.remove_payee(self.store, payee_id=payee_id)
        if result.ok:
            self._audit(request_id, channel, "Removed a trusted payee", "CHANGED", result.message, result.data)
        return ControlResult(ok=result.ok, message=result.message)

    def set_savings_account(self, pin: str | None, *, savings, name: str, number: str, method: str, request_id: str,
                            channel: str = "web") -> ControlResult:
        """Where the owner's monthly savings may go. It decides where money can leave to, so it needs the PIN.
        (Chat can only propose it: `set_savings_account` is held until the owner enters the PIN in the app.)"""
        normal = normalize_number(number) or ""
        if problem := changes.savings_account_problem(self.store, normal, method):
            return ControlResult(ok=False, message=problem)
        if refused := self._pin_ok(pin, request_id, channel, "set the savings account"):
            return refused
        account = savings.set_account(name=name, number=normal, method=method)
        message = f"Savings will go to {account.name} ({account.number_masked}, {method})."
        self._audit(request_id, channel, "Set the savings account", "CHANGED", message,
                    {"name": account.name, "number": account.number_masked, "method": method})
        return ControlResult(ok=True, message=message)

    def change_pin(self, old_pin: str | None, new_pin: str, *, request_id: str, channel: str = "web") -> ControlResult:
        if refused := self._pin_ok(old_pin, request_id, channel, "change the PIN"):
            return refused
        try:
            self.pins.set_pin(new_pin)
        except PinPolicyError as exc:
            return ControlResult(ok=False, message=str(exc))
        self._audit(request_id, channel, "Changed the PIN", "CHANGED", "The PIN was changed.")
        return ControlResult(ok=True, message="Your PIN was changed.")

    def block_number(self, number: str, reason: str, *, request_id: str, channel: str = "web") -> ControlResult:
        result = changes.block_number(self.store, number=number, reason=reason)
        if result.ok:
            self._audit(request_id, channel, "Blocked a number", "CHANGED", result.message)
        return ControlResult(ok=result.ok, message=result.message)

    def stop(self, *, reason: str, request_id: str, channel: str = "web") -> KillSwitchResult:
        return self.kill_switch.activate(reason=reason, actor="owner", channel=channel, request_id=request_id)

    def resume(self, pin: str | None, *, request_id: str, channel: str = "web") -> KillSwitchResult:
        return self.kill_switch.deactivate(pin=pin, actor="owner", channel=channel, request_id=request_id)

