"""The owner's PIN. Hashed with argon2id, never logged, never shown and never passed through the
LLM: it arrives only through the PIN dialog's dedicated endpoint. Three wrong attempts lock every
money action for 15 minutes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.clock import Clock, to_local
from app.core.money import normalize_digits
from app.guard.policy_store import PolicyStore

_HASHER = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1)


class PinPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class PinCheck:
    ok: bool
    locked: bool
    attempts_left: int
    locked_until: datetime | None
    message: str


def validate_new_pin(pin: str) -> str:
    pin = normalize_digits(pin.strip())
    if not re.fullmatch(r"\d{4,6}", pin):
        raise PinPolicyError("The PIN must be 4 to 6 digits.")
    if len(set(pin)) == 1:
        raise PinPolicyError("Use different digits, not one digit repeated.")
    steps = {int(b) - int(a) for a, b in zip(pin, pin[1:], strict=False)}
    if steps in ({1}, {-1}):
        raise PinPolicyError("Avoid sequences such as 1234 or 4321.")
    return pin


class PinManager:
    def __init__(self, store: PolicyStore, clock: Clock, *, max_attempts: int = 3, lock_minutes: int = 15) -> None:
        self.store = store
        self.clock = clock
        self.max_attempts = max_attempts
        self.lock_minutes = lock_minutes

    def is_set(self) -> bool:
        return bool(self.store.security().pin_hash)

    def set_pin(self, new_pin: str) -> None:
        security = self.store.security()
        security.pin_hash = _HASHER.hash(validate_new_pin(new_pin))
        security.failed_attempts = 0
        security.locked_until = None
        security.updated_at = self.clock.now()
        self.store.session.flush()

    def lock_state(self) -> tuple[bool, datetime | None]:
        security = self.store.security()
        if security.locked_until and self.clock.now() < security.locked_until:
            return True, security.locked_until
        return False, None

    def verify(self, pin: str | None) -> PinCheck:
        security = self.store.security()
        now = self.clock.now()
        if security.locked_until and now < security.locked_until:
            until = to_local(security.locked_until).strftime("%H:%M")
            return PinCheck(False, True, 0, security.locked_until,
                            f"Too many wrong PINs. Money actions are locked until {until}.")
        security.locked_until = None
        if not security.pin_hash:
            return PinCheck(False, False, 0, None, "No PIN has been set for this shop.")
        try:
            ok = _HASHER.verify(security.pin_hash, normalize_digits((pin or "").strip()))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            ok = False
        security.updated_at = now
        if ok:
            security.failed_attempts = 0
            self.store.session.flush()
            return PinCheck(True, False, self.max_attempts, None, "PIN accepted.")
        security.failed_attempts += 1
        if security.failed_attempts >= self.max_attempts:
            security.failed_attempts = 0
            security.locked_until = now + timedelta(minutes=self.lock_minutes)
            self.store.session.flush()
            return PinCheck(False, True, 0, security.locked_until,
                            f"Wrong PIN {self.max_attempts} times. Money actions are locked for "
                            f"{self.lock_minutes} minutes.")
        self.store.session.flush()
        left = self.max_attempts - security.failed_attempts
        return PinCheck(False, False, left, None, f"Wrong PIN. {left} attempt{'s' if left != 1 else ''} left.")

    def change_pin(self, old_pin: str, new_pin: str) -> PinCheck:
        check = self.verify(old_pin)
        if check.ok:
            self.set_pin(new_pin)
        return check
