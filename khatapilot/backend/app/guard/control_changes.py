"""Applying changes to the owner's safety settings: limits, trusted payees and blocked numbers.

Two doors lead here, and both have already checked the owner: the PIN-protected owner screen
(`owner_controls.py`) and a change the owner asked for in chat, held by the guard and approved in the
app's approval dialog (with the PIN for anything that makes the shop less safe). This module only
validates and writes; it never checks a PIN itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.masking import mask_number, mask_phone, normalize_msisdn
from app.core.money import fmt_taka
from app.guard.policy_store import PolicyStore, normalize_number

METHODS = ("bkash", "nagad", "bank")


@dataclass
class ChangeResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)


def payee_problem(store: PolicyStore, number: str, method: str) -> str | None:
    """Why this number cannot be added as a trusted payee, or None if it can."""
    if method not in METHODS:
        return "The payment method must be bKash, Nagad or bank."
    if method in ("bkash", "nagad") and not normalize_msisdn(number):
        return "That is not a valid bKash/Nagad number."
    normal = normalize_number(number)
    if method == "bank" and not 6 <= len(normal) <= 20:
        return "That does not look like a bank account number."
    if existing := store.payee_by_number(normal):
        return f"That number is already on your trusted list ({existing.name})."
    return None


def limits_problem(per_txn_paisa: int | None, daily_paisa: int | None) -> str | None:
    if per_txn_paisa is None and daily_paisa is None:
        return "Say which limit to change: per payment, per day, or both."
    if (per_txn_paisa is not None and per_txn_paisa <= 0) or (daily_paisa is not None and daily_paisa <= 0):
        return "Limits must be positive amounts."
    return None


def set_limits(store: PolicyStore, *, per_txn_paisa: int | None, daily_paisa: int | None) -> ChangeResult:
    if problem := limits_problem(per_txn_paisa, daily_paisa):
        return ChangeResult(False, problem)
    limits = store.update_limits(per_txn_paisa=per_txn_paisa, daily_paisa=daily_paisa)
    return ChangeResult(True, f"Limits set: {fmt_taka(limits.per_txn_paisa)} per payment, "
                              f"{fmt_taka(limits.daily_paisa)} per day.",
                        {"per_txn_paisa": limits.per_txn_paisa, "daily_paisa": limits.daily_paisa})


def add_payee(store: PolicyStore, *, name: str, number: str, method: str, via: str,
              aliases: tuple[str, ...] = ()) -> ChangeResult:
    if problem := payee_problem(store, number, method):
        return ChangeResult(False, problem)
    payee = store.add_payee(name, normalize_number(number), method, via=via, aliases=aliases)
    return ChangeResult(True, f"{payee.name} ({payee.number_masked}, {method}) is now a trusted payee.",
                        {"payee_id": payee.id})


def remove_payee(store: PolicyStore, *, payee_id: str) -> ChangeResult:
    payee = next((p for p in store.payees() if p.id == payee_id), None)
    if payee is None or not store.remove_payee(payee_id):
        return ChangeResult(False, "No such trusted payee.")
    return ChangeResult(True, f"{payee.name} ({payee.number_masked}) is no longer a trusted payee: paying them "
                              "needs your PIN again.", {"payee_id": payee_id})


def block_number(store: PolicyStore, *, number: str, reason: str) -> ChangeResult:
    normal = normalize_number(number)
    if len(normal) < 6:
        return ChangeResult(False, "That is not a phone or account number.")
    store.block_number(normal, reason)
    shown = mask_phone(normal) if normalize_msisdn(normal) else mask_number(normal)
    return ChangeResult(True, f"{shown} is blocked: KhataPilot will never pay it.", {"number": shown})
