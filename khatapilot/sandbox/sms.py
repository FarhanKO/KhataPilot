"""Notification SMS in the style bKash and Nagad send to account holders.

These are realistic approximations written by the team, not copies of provider templates.
The same formats are what the backend's SMS parser has to read.
"""

from __future__ import annotations

from datetime import datetime


def tk(paisa: int) -> str:
    whole, frac = divmod(paisa, 100)
    return f"{whole:,}.{frac:02d}"


def stamp(at: datetime) -> str:
    return at.strftime("%d/%m/%Y %H:%M")


def bkash_received(amount: int, sender: str, balance: int, trx_id: str, at: datetime, ref: str | None = None) -> str:
    ref_part = f" Ref {ref}." if ref else ""
    return (
        f"You have received Tk {tk(amount)} from {sender}.{ref_part} Fee Tk 0.00. "
        f"Balance Tk {tk(balance)}. TrxID {trx_id} at {stamp(at)}"
    )


def bkash_payment_received(amount: int, sender: str, balance: int, trx_id: str, at: datetime) -> str:
    return (
        f"You have received payment Tk {tk(amount)} from {sender}. Fee Tk 0.00. "
        f"Balance Tk {tk(balance)}. TrxID {trx_id} at {stamp(at)}"
    )


def bkash_cash_in(amount: int, agent: str, balance: int, trx_id: str, at: datetime) -> str:
    return (
        f"Cash In Tk {tk(amount)} from {agent} successful. Fee Tk 0.00. "
        f"Balance Tk {tk(balance)}. TrxID {trx_id} at {stamp(at)}"
    )


def bkash_payment_sent(
    amount: int, payee_name: str | None, payee: str, balance: int, trx_id: str, at: datetime, ref: str | None = None
) -> str:
    to = f"{payee_name} ({payee})" if payee_name else payee
    ref_part = f" Ref {ref}." if ref else ""
    return (
        f"Payment Tk {tk(amount)} to {to} successful.{ref_part} Fee Tk 0.00. "
        f"Balance Tk {tk(balance)}. TrxID {trx_id} at {stamp(at)}"
    )


def nagad_received(amount: int, sender: str, balance: int, trx_id: str, at: datetime, ref: str | None = None) -> str:
    ref_part = f" Ref: {ref}" if ref else ""
    return (
        f"Money Received. Amount: Tk {tk(amount)} Sender: {sender}{ref_part} TxnID: {trx_id} "
        f"Balance: Tk {tk(balance)} {stamp(at)}"
    )


def nagad_cash_in(amount: int, agent: str, balance: int, trx_id: str, at: datetime) -> str:
    return (
        f"Cash In Received. Amount: Tk {tk(amount)} Uddokta: {agent} TxnID: {trx_id} "
        f"Balance: Tk {tk(balance)} {stamp(at)}"
    )


def nagad_payment_sent(
    amount: int, payee_name: str | None, payee: str, balance: int, trx_id: str, at: datetime
) -> str:
    receiver = f"{payee_name} ({payee})" if payee_name else payee
    return (
        f"Payment Successful. Amount: Tk {tk(amount)} Receiver: {receiver} TxnID: {trx_id} "
        f"Charge: Tk 0.00 Balance: Tk {tk(balance)} {stamp(at)}"
    )


def bank_transfer_sent(amount: int, payee_name: str | None, account: str, balance: int, trx_id: str, at: datetime) -> str:
    masked = f"****{account[-4:]}"
    name = f"{payee_name} " if payee_name else ""
    return (
        f"Your A/C debited BDT {tk(amount)} for transfer to {name}A/C {masked}. "
        f"Avl Bal BDT {tk(balance)}. Ref {trx_id} {stamp(at)}"
    )


def incoming_text(provider: str, txn_type: str, amount: int, sender: str, balance: int, trx_id: str, at: datetime,
                  ref: str | None = None) -> str:
    if provider == "nagad":
        if txn_type == "cash_in":
            return nagad_cash_in(amount, sender, balance, trx_id, at)
        return nagad_received(amount, sender, balance, trx_id, at, ref)
    if txn_type == "cash_in":
        return bkash_cash_in(amount, sender, balance, trx_id, at)
    if txn_type == "payment_received":
        return bkash_payment_received(amount, sender, balance, trx_id, at)
    return bkash_received(amount, sender, balance, trx_id, at, ref)


def outgoing_text(provider: str, amount: int, payee_name: str | None, payee: str, balance: int, trx_id: str,
                  at: datetime, ref: str | None = None) -> str:
    if provider == "nagad":
        return nagad_payment_sent(amount, payee_name, payee, balance, trx_id, at)
    if provider == "bank":
        return bank_transfer_sent(amount, payee_name, payee, balance, trx_id, at)
    return bkash_payment_sent(amount, payee_name, payee, balance, trx_id, at, ref)
