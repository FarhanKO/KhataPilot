"""The kill switch. Turning it on needs no PIN (anyone acting for the owner may pull the brake);
turning it off needs the PIN. While it is on, the guard blocks every payment and outgoing message,
scheduled payments are paused (not deleted), and the provider freezes the shop's accounts too."""

from __future__ import annotations

from pydantic import BaseModel

from app.core.clock import Clock
from app.core.ids import new_id
from app.guard.capability import CapabilitySigner
from app.guard.pin import PinManager
from app.guard.policy_store import PolicyStore
from app.guard.recorder import Recorder
from app.infra.sandbox_client import SandboxClient, SandboxError
from app.modules.payments.service import PaymentsService
from app.modules.shop.service import ShopService


class KillSwitchResult(BaseModel):
    ok: bool
    frozen: bool
    message: str
    paused_payments: int = 0
    resumed_payments: int = 0
    provider: list[str] = []
    attempts_left: int | None = None


class KillSwitchService:
    def __init__(self, *, clock: Clock, shop_id: str, store: PolicyStore, pins: PinManager, payments: PaymentsService,
                 shops: ShopService, sandbox: SandboxClient | None, signer: CapabilitySigner, recorder: Recorder) -> None:
        self.clock = clock
        self.shop_id = shop_id
        self.store = store
        self.pins = pins
        self.payments = payments
        self.shops = shops
        self.sandbox = sandbox
        self.signer = signer
        self.recorder = recorder

    def is_on(self) -> bool:
        return self.store.kill_switch().frozen

    def _accounts(self) -> list[str]:
        shop = self.shops.get(self.shop_id)
        return [shop.bkash_account, shop.nagad_account, shop.bank_account] if shop else []

    def _provider(self, freeze: bool, reason: str, decision_id: str) -> list[str]:
        """Freeze or unfreeze every shop account at the provider. Failures are reported, never fatal:
        the guard's own freeze already blocks everything."""
        if self.sandbox is None:
            return ["provider not configured"]
        kind = "account.freeze" if freeze else "account.unfreeze"
        notes = []
        for account in self._accounts():
            auth = self.signer.mint(kind=kind, params={"account_id": account}, idempotency_key=new_id("idem"),
                                    tier=0 if freeze else 3, decision_id=decision_id)
            try:
                if freeze:
                    self.sandbox.freeze(account, reason, authorization=auth.header, idempotency_key=auth.idempotency_key)
                else:
                    self.sandbox.unfreeze(account, authorization=auth.header, idempotency_key=auth.idempotency_key)
                notes.append(f"{account}: {'frozen' if freeze else 'active'}")
            except SandboxError as exc:
                notes.append(f"{account}: provider said {exc.code}")
            except Exception as exc:  # provider unreachable: the local freeze still holds
                notes.append(f"{account}: provider unreachable ({type(exc).__name__})")
        return notes

    def activate(self, *, reason: str, actor: str, channel: str, request_id: str) -> KillSwitchResult:
        if self.is_on():
            return KillSwitchResult(ok=True, frozen=True, message="STOP is already on. Everything is frozen.")
        decision_id = new_id("ks")
        self.store.set_kill_switch(True, reason=reason, actor=actor)
        paused = self.payments.pause_scheduled()
        provider = self._provider(True, reason, decision_id)
        message = (f"STOP is on. Every payment and outgoing message is frozen; {len(paused)} scheduled "
                   f"payment{'s' if len(paused) != 1 else ''} paused (not deleted). Resume with your PIN.")
        self.recorder.event(request_id=request_id, channel=channel, actor=actor, provenance="owner", event="kill_switch",
                            summary="Kill switch ON", decision="FROZEN", reason=f"{message} Reason: {reason}",
                            decision_id=decision_id, action={"paused": [p.id for p in paused], "provider": provider})
        return KillSwitchResult(ok=True, frozen=True, message=message, paused_payments=len(paused), provider=provider)

    def deactivate(self, *, pin: str | None, actor: str, channel: str, request_id: str) -> KillSwitchResult:
        if not self.is_on():
            return KillSwitchResult(ok=True, frozen=False, message="STOP is already off.")
        check = self.pins.verify(pin)
        self.recorder.event(request_id=request_id, channel=channel, actor=actor, provenance="owner", event="pin",
                            summary="PIN entered to resume after STOP",
                            decision="PIN_OK" if check.ok else ("PIN_LOCKED" if check.locked else "PIN_WRONG"),
                            reason=check.message)
        if not check.ok:
            return KillSwitchResult(ok=False, frozen=True, message=check.message, attempts_left=check.attempts_left)
        decision_id = new_id("ks")
        self.store.set_kill_switch(False, reason=None, actor=actor)
        provider = self._provider(False, "", decision_id)
        resumed = self.payments.resume_paused()
        message = (f"Resumed. {len(resumed)} paused payment{'s' if len(resumed) != 1 else ''} back on schedule; "
                   "each is checked again before it goes out.")
        self.recorder.event(request_id=request_id, channel=channel, actor=actor, provenance="owner", event="kill_switch",
                            summary="Kill switch OFF", decision="RESUMED", reason=message, decision_id=decision_id,
                            action={"resumed": [p.id for p in resumed], "provider": provider})
        return KillSwitchResult(ok=True, frozen=False, message=message, resumed_payments=len(resumed), provider=provider)
