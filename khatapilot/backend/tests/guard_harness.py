"""A small, fully wired world for guard tests: business DB, audit DB, the real sandbox app in-process
(sharing the signing key), a shop with history, trusted payees and a PIN."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.bootstrap import register_models
from app.core.clock import SimClock
from app.core.config import Settings
from app.core.ids import new_id
from app.core.money import taka
from app.core.provenance import Provenance, Segment, Sender, SenderKind, TurnContext
from app.core.sms import parse_sms
from app.guard.capability import CapabilitySigner
from app.guard.gateway import ToolCall, ToolOutcome
from app.guard.owner_controls import OwnerControls
from app.guard.service import Guard, GuardDeps
from app.infra.audit_log import AuditLog
from app.infra.db import Database
from app.infra.sandbox_client import SandboxClient
from app.modules.ledger.schemas import NewLedgerEntry, NewObligation
from app.modules.ledger.service import LedgerService
from app.modules.shop.schemas import NewParty, ShopProfile
from app.modules.shop.service import ShopService
from sandbox.app import create_app as create_sandbox
from sandbox.config import SandboxSettings

SIGNING_KEY = "s" * 48
ADMIN_KEY = "a" * 48
SHOP = "rahman-store"
WALLET = "bkash:01711000222"
RAHIM, BISMILLAH, MEGHNA, DAIRY = "01712345678", "01819876543", "2051334007812", "01556677889"
SCAMMER = "01999888777"
PIN = "7391"


@dataclass
class World:
    settings: Settings
    clock: SimClock
    db: Database
    audit: AuditLog
    sandbox: SandboxClient
    deps: GuardDeps

    @contextmanager
    def guard(self) -> Iterator[Guard]:
        with self.db.session() as session:
            yield Guard(session, self.deps)

    def owner(self, guard: Guard) -> OwnerControls:
        return OwnerControls(clock=self.clock, store=guard.store, pins=guard.pins, kill_switch=guard.kill_switch,
                             recorder=guard.recorder)

    def submit(self, name: str, turn: TurnContext, **arguments) -> ToolOutcome:
        with self.guard() as guard:
            return guard.gateway.submit(ToolCall(name=name, arguments=arguments), turn)

    def pay(self, turn: TurnContext, **arguments) -> ToolOutcome:
        return self.submit("propose_payment", turn, **arguments)

    def wallet_balance(self) -> int:
        return self.sandbox.account(WALLET)["balance_paisa"]

    def provider_saw(self, sms_text: str) -> None:
        """Put the transaction an SMS describes on the shop's statement at the provider, so the SMS is
        genuine (KhataPilot books an SMS only when the provider confirms its TrxID and amount)."""
        sms = parse_sms(sms_text)
        account = {"bkash": WALLET, "nagad": "nagad:01811000222", "bank": "bank:1701500012345"}[sms.provider]
        self.sandbox.import_transactions([{
            "trx_id": sms.trx_id, "account_id": account, "direction": "credit" if sms.direction == "in" else "debit",
            "txn_type": sms.txn_type, "amount_paisa": sms.amount_paisa, "fee_paisa": sms.fee_paisa,
            "balance_after_paisa": sms.balance_paisa or 0, "counterparty_number": sms.counterparty_number,
            "counterparty_name": sms.counterparty_name, "occurred_at": (sms.occurred_at or self.clock.now()).isoformat(),
            "sms_text": sms_text}])


def turn(owner_text: str = "", *, conversation: str = "conv-1", untrusted: tuple[tuple[str, str], ...] = (),
         sender: SenderKind = SenderKind.OWNER, sender_display: str = "Owner (web)", channel: str = "web",
         at: datetime | None = None) -> TurnContext:
    segments = []
    if owner_text:
        provenance = Provenance.OWNER if sender is SenderKind.OWNER else Provenance.UNTRUSTED
        segments.append(Segment(text=owner_text, provenance=provenance,
                                source="chat" if sender is SenderKind.OWNER else "inbound_message"))
    segments += [Segment(text=text, provenance=Provenance.UNTRUSTED, source=source) for source, text in untrusted]
    return TurnContext(request_id=new_id("req"), conversation_id=conversation, shop_id=SHOP, channel=channel,
                       sender=Sender(kind=sender, display=sender_display), segments=tuple(segments),
                       received_at=at or datetime.now().astimezone())


def stranger(text: str, number: str = SCAMMER) -> TurnContext:
    return turn(text, sender=SenderKind.EXTERNAL, sender_display=number, channel="sms_gateway")


def seed_shop(session, clock: SimClock) -> None:
    shops, ledger = ShopService(session, clock), LedgerService(session, clock)
    shops.create_shop(ShopProfile(
        id=SHOP, name="Rahman Store", owner_name="Mizanur Rahman", shop_type="grocery", area="Mohakhali",
        district="Dhaka", division="Dhaka", urbanicity="urban", owner_phone="01711000111", bkash_account=WALLET,
        nagad_account="nagad:01811000222", bank_account="bank:1701500012345", data_since=date(2025, 11, 1)))
    session.flush()
    for name, number, method, category in [("Rahim Traders", RAHIM, "bkash", "Rice, lentils & flour"),
                                           ("Meghna Distributors", MEGHNA, "bank", "Toiletries & snacks"),
                                           ("Bismillah Oil Agency", BISMILLAH, "bkash", "Oil & sugar"),
                                           ("Fresh Dairy Point", DAIRY, "cash", "Dairy, eggs & bread")]:
        shops.add_party(SHOP, NewParty(kind="supplier", name=name, number=number, method=method, category=category))
    now = clock.now()
    for days_ago in range(1, 41):  # ~৳14,000 a day of sales and a weekly Rahim Traders payment
        at = now - timedelta(days=days_ago)
        ledger.add_entry(NewLedgerEntry(shop_id=SHOP, occurred_at=at, kind="sale", direction="in", method="cash",
                                        amount_paisa=taka(14_000), category="Sales", source="seed"))
        if days_ago % 7 == 0:
            ledger.add_entry(NewLedgerEntry(shop_id=SHOP, occurred_at=at, kind="supplier_payment", direction="out",
                                            method="bkash", amount_paisa=taka(35_000), category="Rice, lentils & flour",
                                            counterparty_name="Rahim Traders", counterparty_number=RAHIM, source="seed"))
            ledger.add_entry(NewLedgerEntry(shop_id=SHOP, occurred_at=at, kind="supplier_payment", direction="out",
                                            method="bkash", amount_paisa=taka(9_000), category="Oil & sugar",
                                            counterparty_name="Bismillah Oil Agency", counterparty_number=BISMILLAH,
                                            source="seed"))
    ledger.add_obligation(NewObligation(shop_id=SHOP, kind="supplier_payable", counterparty_name="Rahim Traders",
                                        counterparty_number=RAHIM, method="bkash", category="Rice, lentils & flour",
                                        amount_paisa=taka(34_751), due_date=date(2026, 10, 17)))


def build_world(tmp_path, settings: Settings, clock: SimClock) -> tuple[World, TestClient]:
    sandbox_http = TestClient(create_sandbox(SandboxSettings(
        _env_file=None, sandbox_data_dir=tmp_path / "sandbox", guard_signing_key=SecretStr(SIGNING_KEY),
        sandbox_admin_key=SecretStr(ADMIN_KEY), sim_time=clock.now())))
    sandbox_http.__enter__()
    sandbox = SandboxClient("http://testserver", admin_key=ADMIN_KEY, http=sandbox_http)
    accounts = [
        {"id": WALLET, "provider": "bkash", "number": "01711000222", "holder_name": "Rahman Store", "kind": "merchant",
         "balance_paisa": taka(60_000)},
        {"id": "nagad:01811000222", "provider": "nagad", "number": "01811000222", "holder_name": "Rahman Store",
         "kind": "merchant", "balance_paisa": taka(5_000)},
        {"id": "bank:1701500012345", "provider": "bank", "number": "1701500012345", "holder_name": "Rahman Store",
         "kind": "bank", "balance_paisa": taka(100_000)},
    ]
    sandbox.seed_accounts(accounts)
    register_models()
    db = Database(settings.db_url)
    db.create_all()
    audit = AuditLog(settings.audit_db_url, clock)
    deps = GuardDeps(settings=settings, clock=clock, audit=audit, signer=CapabilitySigner(SIGNING_KEY.encode()),
                     sandbox=sandbox)
    world = World(settings=settings, clock=clock, db=db, audit=audit, sandbox=sandbox, deps=deps)
    with db.session() as session:
        seed_shop(session, clock)
    with world.guard() as guard:
        guard.pins.set_pin(PIN)
        guard.store.add_payee("Rahim Traders", RAHIM, "bkash", via="seed", aliases=("Rahim", "রহিম ট্রেডার্স"))
        guard.store.add_payee("Meghna Distributors", MEGHNA, "bank", via="seed", aliases=("Meghna",))
        guard.store.add_payee("Bismillah Oil Agency", BISMILLAH, "bkash", via="seed", aliases=("Bismillah",))
    return world, sandbox_http
