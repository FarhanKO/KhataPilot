"""Owner-only controls: limits, the trusted-payee list, the PIN, and the kill switch.

None of these are LLM tools. They are reachable only from the interface layer's PIN-protected
endpoints (an architecture test enforces that nothing else imports this module). Making things
safer (STOP, blocking a number) needs no PIN; making things less safe always does.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.core.clock import Clock
from app.core.masking import mask_phone, normalize_msisdn
from app.core.money import fmt_taka, to_paisa
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
        if (per_txn_taka is not None and per_txn_taka <= 0) or (daily_taka is not None and daily_taka <= 0):
            return ControlResult(ok=False, message="Limits must be positive amounts.")
        limits = self.store.update_limits(
            per_txn_paisa=to_paisa(per_txn_taka) if per_txn_taka is not None else None,
            daily_paisa=to_paisa(daily_taka) if daily_taka is not None else None,
        )
        message = f"Limits set: {fmt_taka(limits.per_txn_paisa)} per payment, {fmt_taka(limits.daily_paisa)} per day."
        self._audit(request_id, channel, "Changed payment limits", "CHANGED", message,
                    {"per_txn_paisa": limits.per_txn_paisa, "daily_paisa": limits.daily_paisa})
        return ControlResult(ok=True, message=message)

    def add_payee(self, pin: str | None, *, name: str, number: str, method: str, aliases: tuple[str, ...] = (),
                  request_id: str, channel: str = "web") -> ControlResult:
        if refused := self._pin_ok(pin, request_id, channel, "add a trusted payee"):
            return refused
        normal = normalize_number(number)
        if method in ("bkash", "nagad") and not normalize_msisdn(number):
            return ControlResult(ok=False, message="That is not a valid bKash/Nagad number.")
        if self.store.payee_by_number(normal):
            return ControlResult(ok=False, message="That number is already on your trusted list.")
        payee = self.store.add_payee(name, normal, method, via="owner_pin", aliases=aliases)
        message = f"{payee.name} ({payee.number_masked}, {method}) is now a trusted payee."
        self._audit(request_id, channel, "Added a trusted payee", "CHANGED", message, {"payee_id": payee.id})
        return ControlResult(ok=True, message=message, data={"payee_id": payee.id})

    def remove_payee(self, pin: str | None, *, payee_id: str, request_id: str, channel: str = "web") -> ControlResult:
        if refused := self._pin_ok(pin, request_id, channel, "remove a trusted payee"):
            return refused
        if not self.store.remove_payee(payee_id):
            return ControlResult(ok=False, message="No such trusted payee.")
        self._audit(request_id, channel, "Removed a trusted payee", "CHANGED", "Removed from the trusted list.",
                    {"payee_id": payee_id})
        return ControlResult(ok=True, message="Removed from the trusted list.")

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
        self.store.block_number(number, reason)
        message = f"{mask_phone(number)} is blocked: KhataPilot will never pay it."
        self._audit(request_id, channel, "Blocked a number", "CHANGED", message)
        return ControlResult(ok=True, message=message)

    def stop(self, *, reason: str, request_id: str, channel: str = "web") -> KillSwitchResult:
        return self.kill_switch.activate(reason=reason, actor="owner", channel=channel, request_id=request_id)

    def resume(self, pin: str | None, *, request_id: str, channel: str = "web") -> KillSwitchResult:
        return self.kill_switch.deactivate(pin=pin, actor="owner", channel=channel, request_id=request_id)

