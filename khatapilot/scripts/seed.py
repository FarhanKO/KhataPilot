"""Reset KhataPilot and load the demo shop, Rahman Store (Mohakhali), into the backend and the sandbox.

    python scripts/seed.py                  # needs the sandbox running (scripts\\dev.ps1 -Seed does both)
    python scripts/seed.py --regenerate     # regenerate the synthetic data first
    python scripts/seed.py --backend-only   # leave the sandbox alone
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

import httpx  # noqa: E402
from pydantic import SecretStr  # noqa: E402
from pydantic_settings import BaseSettings, SettingsConfigDict  # noqa: E402

from app.bootstrap import make_clock, register_models  # noqa: E402
from app.core.clock import BDT, Clock  # noqa: E402
from app.core.config import REPO_ROOT, Settings, get_settings  # noqa: E402
from app.core.money import fmt_taka, taka  # noqa: E402
from app.guard.pin import PinManager  # noqa: E402
from app.guard.policy_store import PolicyStore  # noqa: E402
from app.guard.service import default_limits  # noqa: E402
from app.infra.audit_log import AuditLog, AuditRecord  # noqa: E402
from app.infra.db import Database  # noqa: E402
from app.infra.sandbox_client import SandboxClient  # noqa: E402
from app.modules.ledger.schemas import NewLedgerEntry, NewObligation  # noqa: E402
from app.modules.ledger.service import LedgerService  # noqa: E402
from app.modules.shop.schemas import NewParty, ShopProfile  # noqa: E402
from app.modules.shop.service import ShopService  # noqa: E402
from data.generate_shops import AS_OF, OUT_DIR, generate  # noqa: E402

STATEMENT_DAYS = 30
WALLET_METHODS = ("bkash", "nagad", "bank")
TXN_TYPES = {  # ledger kind -> sandbox transaction type, per direction
    ("sale", "in"): "payment_received",
    ("baki_repayment", "in"): "received",
    ("opening_balance", "in"): "opening_balance",
    ("transfer", "in"): "cash_in",
    ("transfer", "out"): "cash_out",
    ("other_in", "in"): "received",
}


def ensure_generated(regenerate: bool = False, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    meta = out_dir / "meta.json"
    if regenerate or not meta.exists():
        return generate(out_dir=out_dir)
    return json.loads(meta.read_text(encoding="utf-8"))


def _read_json(out_dir: Path, name: str) -> dict[str, Any]:
    return json.loads((out_dir / name).read_text(encoding="utf-8"))


def _opt(value: str | None) -> str | None:
    return value if value not in (None, "") else None


def _opt_int(value: str | None) -> int | None:
    return int(float(value)) if value not in (None, "") else None


def hero_ledger_rows(out_dir: Path) -> list[dict[str, str]]:
    with (out_dir / "hero_ledger.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return sorted(rows, key=lambda r: (r["occurred_at"], r["ref"]))


def shop_profile(shop_id: str, shop: dict[str, Any]) -> ShopProfile:
    return ShopProfile(
        id=shop_id,
        name=shop["name"],
        owner_name=shop["owner_name"],
        shop_type=shop["shop_type"],
        area=shop["area"],
        district=shop["district"],
        division=shop["division"],
        urbanicity=shop["urbanicity"],
        owner_phone=shop["owner_phone"],
        bkash_account=f"bkash:{shop['bkash_number']}",
        nagad_account=f"nagad:{shop['nagad_number']}",
        bank_account=f"bank:{shop['bank_account']}",
        data_since=AS_OF - timedelta(days=shop["history_days"] - 1),
    )


def seed_backend(db: Database, clock: Clock, shop_id: str, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """Wipe the business database and load the hero shop's profile, parties, books and bills."""
    parties = _read_json(out_dir, "hero_parties.json")
    obligations = _read_json(out_dir, "hero_obligations.json")
    supplier_category = {s["name"]: s["category"] for s in parties["suppliers"]}
    register_models()
    db.reset()
    with db.session() as session:
        shops = ShopService(session, clock)
        ledger = LedgerService(session, clock)
        shops.create_shop(shop_profile(shop_id, parties["shop"]))
        session.flush()
        for s in parties["suppliers"]:
            shops.add_party(shop_id, NewParty(kind="supplier", name=s["name"], number=s["number"], method=s["method"],
                                              category=s["category"], terms_days=s["terms_days"]))
        for c in parties["customers"]:
            shops.add_party(shop_id, NewParty(kind="customer", name=c["name"], number=c["number"]))
        imported = ledger.bulk_import(
            NewLedgerEntry(
                shop_id=shop_id,
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                kind=row["kind"],
                direction=row["direction"],
                method=row["method"],
                amount_paisa=taka(row["amount"]),
                category=row["category"],
                counterparty_name=_opt(row["counterparty_name"]),
                counterparty_number=_opt(row["counterparty_number"]),
                note=_opt(row["note"]),
                due_date=date.fromisoformat(row["due_date"]) if _opt(row["due_date"]) else None,
                days_late=_opt_int(row["days_late"]),
                trx_id=_opt(row["trx_id"]),
                source="seed",
                source_ref=row["ref"],
            )
            for row in hero_ledger_rows(out_dir)
        )
        for payable in obligations["supplier_payables"]:
            ledger.add_obligation(NewObligation(
                shop_id=shop_id, kind="supplier_payable", counterparty_name=payable["supplier"],
                counterparty_number=payable["number"], method=payable["method"],
                category=supplier_category.get(payable["supplier"], "Stock purchases"),
                amount_paisa=taka(payable["amount"]), due_date=date.fromisoformat(payable["due_on"]),
                note=f"Delivery of {payable['delivered_on']}", source_ref=payable["entry_ref"],
            ))
        for bill in obligations["upcoming_bills"]:
            ledger.add_obligation(NewObligation(
                shop_id=shop_id, kind="bill", counterparty_name=bill["payee"], method=bill["method"],
                category=bill["category"], amount_paisa=taka(bill["amount"]),
                due_date=date.fromisoformat(bill["due_on"]), note=bill["name"],
            ))
        for bill in parties["expenses"]:
            ledger.add_recurring_bill(shop_id, bill["name"], bill["payee"], bill["category"], taka(bill["amount"]),
                                      bill["day_of_month"], bill["method"])
        balances = ledger.balances(shop_id)
    expected = {m: taka(v) for m, v in obligations["balances"].items()}
    if balances != expected:
        raise RuntimeError(f"seeded balances {balances} do not match the generator's {expected}")
    return {"ledger_entries": imported, "balances_paisa": balances,
            "open_payables": len(obligations["supplier_payables"]), "upcoming_bills": len(obligations["upcoming_bills"])}


class ScriptSecrets(BaseSettings):
    """Secrets only the scripts read. The running app never loads the demo PIN."""

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    demo_owner_pin: SecretStr = SecretStr("")


def seed_guard(db: Database, clock: Clock, settings: Settings, pin: str, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """Default limits, the shop's wallet and bank suppliers as trusted payees, and the owner's PIN."""
    parties = _read_json(out_dir, "hero_parties.json")
    with db.session() as session:
        store = PolicyStore(session, clock, settings.shop_id, default_limits(settings))
        store.ensure()
        trusted = []
        for supplier in parties["suppliers"]:
            if supplier["method"] in WALLET_METHODS and supplier["number"]:
                store.add_payee(supplier["name"], supplier["number"], supplier["method"], via="seed",
                                aliases=tuple(supplier.get("aliases") or ()))
                trusted.append(supplier["name"])
        PinManager(store, clock).set_pin(pin)
        limits = store.limits()
    return {"trusted_payees": trusted, "per_payment_limit": fmt_taka(limits.per_txn_paisa),
            "daily_limit": fmt_taka(limits.daily_paisa), "pin": "set (argon2id hash only)"}


def reset_audit(settings: Settings, clock: Clock, note: str) -> str:
    """Start a fresh hash chain for the demo, opening with one system entry. Returns the head hash."""
    audit = AuditLog(settings.audit_db_url, clock)
    audit.reset()
    entry = audit.append(AuditRecord(request_id="seed", channel="system", actor="system", provenance="system",
                                     event="system", summary="Demo shop seeded", decision="INFO", reason=note))
    audit.dispose()
    return entry.hash


def statement_rows(shop: dict[str, Any], rows: list[dict[str, str]], days: int = STATEMENT_DAYS) -> list[dict[str, Any]]:
    """The last `days` of wallet and bank movements, with running balances, for the sandbox statement."""
    accounts = {"bkash": f"bkash:{shop['bkash_number']}", "nagad": f"nagad:{shop['nagad_number']}",
                "bank": f"bank:{shop['bank_account']}"}
    since = (AS_OF - timedelta(days=days - 1)).isoformat()
    running = dict.fromkeys(WALLET_METHODS, 0)
    statement = []
    for row in rows:
        method = row["method"]
        if method not in WALLET_METHODS:
            continue
        amount = taka(row["amount"])
        running[method] += amount if row["direction"] == "in" else -amount
        if row["occurred_at"][:10] < since or row["kind"] == "opening_balance":
            continue
        trx_id = _opt(row["trx_id"]) or "T" + hashlib.sha256(row["ref"].encode()).hexdigest()[:9].upper()
        statement.append({
            "trx_id": trx_id,
            "account_id": accounts[method],
            "direction": "credit" if row["direction"] == "in" else "debit",
            "txn_type": TXN_TYPES.get((row["kind"], row["direction"]), "payment"),
            "amount_paisa": amount,
            "balance_after_paisa": running[method],
            "counterparty_number": _opt(row["counterparty_number"]),
            "counterparty_name": _opt(row["counterparty_name"]) or _opt(row["note"]),
            "reference": _opt(row["category"]),
            "occurred_at": row["occurred_at"],
        })
    return statement


def seed_sandbox(client: SandboxClient, sim_now: datetime, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """Reset the sandbox and open the shop's wallets, bank account and supplier accounts."""
    parties = _read_json(out_dir, "hero_parties.json")
    balances = _read_json(out_dir, "hero_obligations.json")["balances"]
    shop = parties["shop"]
    client.reset()
    accounts = [
        {"id": f"bkash:{shop['bkash_number']}", "provider": "bkash", "number": shop["bkash_number"],
         "holder_name": shop["name"], "kind": "merchant", "balance_paisa": taka(balances["bkash"])},
        {"id": f"nagad:{shop['nagad_number']}", "provider": "nagad", "number": shop["nagad_number"],
         "holder_name": shop["name"], "kind": "merchant", "balance_paisa": taka(balances["nagad"])},
        {"id": f"bank:{shop['bank_account']}", "provider": "bank", "number": shop["bank_account"],
         "holder_name": shop["name"], "kind": "bank", "balance_paisa": taka(balances["bank"])},
    ]
    for supplier in parties["suppliers"]:
        if supplier["method"] in WALLET_METHODS and supplier["number"]:
            accounts.append({"id": f"{supplier['method']}:{supplier['number']}", "provider": supplier["method"],
                             "number": supplier["number"], "holder_name": supplier["name"],
                             "kind": "bank" if supplier["method"] == "bank" else "merchant", "balance_paisa": 0})
    client.seed_accounts(accounts)
    statement = statement_rows(shop, hero_ledger_rows(out_dir))
    client.import_transactions(statement)
    client.set_clock(sim_now)
    return {"sandbox_accounts": len(accounts), "statement_rows": len(statement)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reset KhataPilot and load the demo shop.")
    parser.add_argument("--regenerate", action="store_true", help="regenerate the synthetic data first")
    parser.add_argument("--backend-only", action="store_true", help="do not touch the sandbox")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    settings = get_settings()
    pin = ScriptSecrets().demo_owner_pin.get_secret_value()
    if not pin:
        print("DEMO_OWNER_PIN is missing from .env. Run `python scripts/make_env.py` first.")
        return 1
    meta = ensure_generated(args.regenerate)
    clock = make_clock(settings)
    database = Database(settings.db_url)
    summary: dict[str, Any] = {"generated": {k: meta[k] for k in ("shops", "default_rate", "hero_ledger_entries")}}
    summary["backend"] = seed_backend(database, clock, settings.shop_id)
    summary["guard"] = seed_guard(database, clock, settings, pin)
    summary["audit_head"] = reset_audit(settings, clock, (
        f"Fresh audit chain for {settings.shop_id}: {summary['backend']['ledger_entries']} ledger entries, "
        f"{len(summary['guard']['trusted_payees'])} trusted payees, limits {summary['guard']['per_payment_limit']} "
        f"per payment and {summary['guard']['daily_limit']} per day."))

    if not args.backend_only:
        client = SandboxClient(settings.sandbox_url, admin_key=settings.sandbox_admin_key.get_secret_value())
        try:
            client.health()
        except httpx.HTTPError:
            print(f"The sandbox is not reachable at {settings.sandbox_url}. Start it first "
                  "(scripts\\dev.ps1), or run with --backend-only.")
            return 1
        summary["sandbox"] = seed_sandbox(client, settings.sim_time or datetime.now(BDT))
    print(json.dumps(summary, indent=2, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
