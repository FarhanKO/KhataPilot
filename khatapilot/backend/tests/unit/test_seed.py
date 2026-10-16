"""The seed loads the same shop into the backend and the sandbox, and the two agree."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.bootstrap import register_models
from app.core.money import taka
from app.guard.pin import PinManager
from app.guard.policy_store import PolicyStore
from app.guard.service import default_limits
from app.infra.audit_log import AuditLog
from app.infra.db import Database
from app.infra.sandbox_client import SandboxClient
from app.modules.ledger.service import LedgerService
from app.modules.shop.service import ShopService
from sandbox.app import create_app as create_sandbox
from sandbox.config import SandboxSettings
from scripts.seed import reset_audit, seed_backend, seed_guard, seed_sandbox
from tests.conftest import DEMO_NOW

SHOP = "rahman-store"
ADMIN_KEY = "a" * 40


@pytest.fixture
def seeded(generated_dir, tmp_path, clock):
    register_models()
    db = Database(f"sqlite:///{(tmp_path / 'kp.db').as_posix()}")
    summary = seed_backend(db, clock, SHOP, generated_dir)
    yield db, summary
    db.dispose()


@pytest.fixture
def sandbox(tmp_path):
    settings = SandboxSettings(_env_file=None, sandbox_data_dir=tmp_path / "sandbox",
                               guard_signing_key=SecretStr("k" * 40), sandbox_admin_key=SecretStr(ADMIN_KEY))
    with TestClient(create_sandbox(settings)) as http:
        yield SandboxClient("http://testserver", admin_key=ADMIN_KEY, http=http)


def test_backend_seed_loads_the_hero_shop(seeded, clock, generated_dir):
    db, summary = seeded
    obligations = json.loads((generated_dir / "hero_obligations.json").read_text(encoding="utf-8"))
    assert summary["ledger_entries"] > 5000
    with db.session() as session:
        shops, ledger = ShopService(session, clock), LedgerService(session, clock)
        assert shops.get(SHOP).name == "Rahman Store"
        assert shops.find_party(SHOP, "rahim traders").number == "01712345678"
        assert shops.find_party(SHOP, "Meghna").name == "Meghna Distributors"
        kinds = {o.kind for o in ledger.open_obligations(SHOP)}
        assert kinds == {"supplier_payable", "bill"}
        baki = ledger.baki_by_customer(SHOP, today=date(2026, 10, 12))
        assert sum(b.outstanding_paisa for b in baki) == taka(sum(b["amount"] for b in obligations["open_baki"]))
        assert len(ledger.recurring_bills(SHOP)) == 3


def test_guard_seed_trusts_the_wallet_suppliers_and_sets_the_pin(seeded, clock, settings, generated_dir):
    db, _ = seeded
    summary = seed_guard(db, clock, settings.model_copy(update={"shop_id": SHOP}), "7391", generated_dir)
    assert summary["trusted_payees"] == ["Rahim Traders", "Meghna Distributors", "Bismillah Oil Agency"]
    assert summary["per_payment_limit"] == "৳10,000"
    with db.session() as session:
        store = PolicyStore(session, clock, SHOP, default_limits(settings))
        assert store.payee_by_name("রহিম ট্রেডার্স").name == "Rahim Traders"
        assert store.payee_by_name("Meghna").method == "bank"
        assert PinManager(store, clock).verify("7391").ok


def test_seeding_starts_a_fresh_audit_chain(settings, clock):
    head = reset_audit(settings, clock, "test")
    audit = AuditLog(settings.audit_db_url, clock)
    report = audit.verify()
    audit.dispose()
    assert report.ok and report.entries == 1 and report.head_hash == head


def test_sandbox_balances_match_the_books(seeded, sandbox, generated_dir):
    _, summary = seeded
    result = seed_sandbox(sandbox, DEMO_NOW, generated_dir)
    assert result["statement_rows"] > 50
    for method, account in [("bkash", "bkash:01711000222"), ("nagad", "nagad:01811000222"),
                            ("bank", "bank:1701500012345")]:
        assert sandbox.account(account)["balance_paisa"] == summary["balances_paisa"][method]
    latest = sandbox.statement("bkash:01711000222", limit=1)[0]
    assert latest["balance_after_paisa"] == summary["balances_paisa"]["bkash"]
    assert sandbox.account("bkash:01712345678")["holder_name"] == "Rahim Traders"
