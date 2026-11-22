"""What "delete all my data" covers, and the deterministic invoice parser."""

import importlib
import pkgutil
from datetime import date

import app.guard.models  # noqa: F401  (registers the guard's tables)
import app.modules
from app.core.invoice import parse_invoice
from app.guard import data_rights
from app.infra.db import Base


def _load_all_models() -> None:
    for mod in pkgutil.walk_packages(app.modules.__path__, "app.modules."):
        if mod.name.endswith(".models"):
            importlib.import_module(mod.name)


def test_every_shop_table_is_either_erased_or_deliberately_kept():
    _load_all_models()
    shop_tables = {name for name, table in Base.metadata.tables.items() if "shop_id" in table.c}
    decided = set(data_rights.ERASED) | set(data_rights.KEPT)
    assert shop_tables - decided == set(), "a new table holding shop data: add it to ERASED or KEPT"
    assert not set(data_rights.ERASED) & set(data_rights.KEPT)


def test_the_safety_settings_and_the_audit_trail_are_kept():
    for kept in ("guard_trusted_payees", "guard_blocked_numbers", "guard_owner_security", "reminder_opt_outs"):
        assert kept in data_rights.KEPT
    assert "audit log" in data_rights.KEPT_WORDS


TODAY = date(2026, 10, 12)


def test_the_parser_reads_supplier_number_total_due_and_lines():
    inv = parse_invoice("Meghna Distributors Ltd.\nInvoice No: MD-2210\nRice 50 kg x 10 bags   38,000\n"
                        "Dal 25 kg x 4  4,175\nSub total 42,175\nTotal payable: 42,175\nDue 20/10/2026", TODAY)
    assert inv.supplier == "Meghna Distributors Ltd." and inv.invoice_number == "MD-2210"
    assert inv.total_paisa == 4_217_500 and inv.due == date(2026, 10, 20)
    assert inv.items_paisa == 4_217_500 and len(inv.items) == 2


def test_the_parser_finds_where_it_asks_to_be_paid():
    inv = parse_invoice("Rahim Traders\nInvoice 5\nTotal 3,000\nOur bKash number has changed. Pay 01998877665\n"
                        "Bank A/C: 2051334009999", TODAY)
    assert inv.phone_numbers == ["01998877665"] and inv.bank_accounts == ["2051334009999"]
    assert inv.says_number_changed


def test_bangla_digits_and_a_missing_total():
    inv = parse_invoice("শাপলা প্যাকেজিং\nপলিথিন ৪,২০০\nমোট ৪,২০০", TODAY)
    assert inv.total_paisa == 420_000
    assert parse_invoice("Some note without numbers", TODAY).total_paisa is None
