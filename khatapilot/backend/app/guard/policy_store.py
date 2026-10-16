"""The guard's own state: limits, trusted payees, blocked numbers, the kill switch and PIN state.
Reading and writing only; decisions are made in `engine.py` and `checks.py`."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.ids import new_id
from app.core.masking import mask_number, normalize_msisdn
from app.guard.models import (
    BlockedNumber,
    KillSwitchState,
    OwnerSecurity,
    PolicyLimits,
    TrustedPayee,
)


@dataclass(frozen=True)
class Limits:
    per_txn_paisa: int
    daily_paisa: int
    fraud_medium: float
    fraud_high: float
    split_window_hours: int
    velocity_max_per_hour: int


class TrustedPayeeView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    aliases: tuple[str, ...]
    number: str
    method: str
    added_via: str
    added_at: datetime

    @property
    def number_masked(self) -> str:
        return mask_number(self.number)


class KillSwitchView(BaseModel):
    model_config = ConfigDict(frozen=True)

    frozen: bool
    since: datetime | None
    reason: str | None
    actor: str | None


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9ঀ-৿]+", " ", name.lower())).strip()


def normalize_number(number: str) -> str:
    """Wallet numbers become 01XXXXXXXXX; anything else (a bank account) keeps its digits only."""
    return normalize_msisdn(number) or re.sub(r"\D", "", number)


def _payee_view(row: TrustedPayee) -> TrustedPayeeView:
    return TrustedPayeeView(id=row.id, name=row.name, aliases=tuple(json.loads(row.aliases_json)),
                            number=row.number, method=row.method, added_via=row.added_via, added_at=row.added_at)


class PolicyStore:
    def __init__(self, session: Session, clock: Clock, shop_id: str, defaults: Limits) -> None:
        self.session = session
        self.clock = clock
        self.shop_id = shop_id
        self.defaults = defaults

    def ensure(self) -> None:
        """Create the guard's rows for this shop the first time it is used."""
        now = self.clock.now()
        if self.session.get(PolicyLimits, self.shop_id) is None:
            d = self.defaults
            self.session.add(PolicyLimits(
                shop_id=self.shop_id, per_txn_paisa=d.per_txn_paisa, daily_paisa=d.daily_paisa,
                fraud_medium=d.fraud_medium, fraud_high=d.fraud_high, split_window_hours=d.split_window_hours,
                velocity_max_per_hour=d.velocity_max_per_hour, updated_at=now,
            ))
        if self.session.get(KillSwitchState, self.shop_id) is None:
            self.session.add(KillSwitchState(shop_id=self.shop_id, frozen=False))
        if self.session.get(OwnerSecurity, self.shop_id) is None:
            self.session.add(OwnerSecurity(shop_id=self.shop_id, failed_attempts=0, updated_at=now))
        self.session.flush()

    # ---- limits

    def limits(self) -> Limits:
        row = self.session.get(PolicyLimits, self.shop_id)
        if row is None:
            return self.defaults
        return Limits(row.per_txn_paisa, row.daily_paisa, row.fraud_medium, row.fraud_high,
                      row.split_window_hours, row.velocity_max_per_hour)

    def update_limits(self, *, per_txn_paisa: int | None = None, daily_paisa: int | None = None) -> Limits:
        self.ensure()
        row = self.session.get(PolicyLimits, self.shop_id)
        if per_txn_paisa is not None:
            row.per_txn_paisa = per_txn_paisa
        if daily_paisa is not None:
            row.daily_paisa = daily_paisa
        row.updated_at = self.clock.now()
        self.session.flush()
        return self.limits()

    # ---- trusted payees (the allowlist)

    def payees(self) -> list[TrustedPayeeView]:
        rows = self.session.scalars(select(TrustedPayee).where(
            TrustedPayee.shop_id == self.shop_id, TrustedPayee.status == "active").order_by(TrustedPayee.name))
        return [_payee_view(r) for r in rows]

    def payee_by_number(self, number: str | None) -> TrustedPayeeView | None:
        if not number:
            return None
        wanted = normalize_number(number)
        return next((p for p in self.payees() if p.number == wanted), None)

    def payee_by_name(self, name: str | None) -> TrustedPayeeView | None:
        if not name:
            return None
        wanted = normalize_name(name)
        if not wanted:
            return None
        payees = self.payees()
        for payee in payees:
            if wanted == normalize_name(payee.name) or wanted in {normalize_name(a) for a in payee.aliases}:
                return payee
        prefix = [p for p in payees if normalize_name(p.name).startswith(wanted)]
        return prefix[0] if len(prefix) == 1 else None

    def add_payee(self, name: str, number: str, method: str, *, via: str, aliases: tuple[str, ...] = ()) -> TrustedPayeeView:
        row = TrustedPayee(id=new_id("payee"), shop_id=self.shop_id, name=name.strip(),
                           aliases_json=json.dumps(list(aliases)), number=normalize_number(number), method=method,
                           status="active", added_via=via, added_at=self.clock.now())
        self.session.add(row)
        self.session.flush()
        return _payee_view(row)

    def remove_payee(self, payee_id: str) -> bool:
        row = self.session.get(TrustedPayee, payee_id)
        if row is None or row.shop_id != self.shop_id or row.status != "active":
            return False
        row.status = "removed"
        self.session.flush()
        return True

    # ---- blocked numbers

    def blocked_reason(self, number: str | None) -> str | None:
        if not number:
            return None
        row = self.session.get(BlockedNumber, (self.shop_id, normalize_number(number)))
        return row.reason if row else None

    def block_number(self, number: str, reason: str) -> None:
        key = (self.shop_id, normalize_number(number))
        if self.session.get(BlockedNumber, key) is None:
            self.session.add(BlockedNumber(shop_id=key[0], number=key[1], reason=reason, added_at=self.clock.now()))
            self.session.flush()

    # ---- kill switch

    def kill_switch(self) -> KillSwitchView:
        row = self.session.get(KillSwitchState, self.shop_id)
        if row is None:
            return KillSwitchView(frozen=False, since=None, reason=None, actor=None)
        return KillSwitchView(frozen=row.frozen, since=row.since, reason=row.reason, actor=row.actor)

    def set_kill_switch(self, frozen: bool, *, reason: str | None, actor: str | None) -> KillSwitchView:
        self.ensure()
        row = self.session.get(KillSwitchState, self.shop_id)
        row.frozen = frozen
        row.since = self.clock.now() if frozen else None
        row.reason = reason if frozen else None
        row.actor = actor
        self.session.flush()
        return self.kill_switch()

    # ---- owner security (the PIN module owns the logic)

    def security(self) -> OwnerSecurity:
        self.ensure()
        return self.session.get(OwnerSecurity, self.shop_id)
