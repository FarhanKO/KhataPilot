"""Parser for bKash, Nagad and bank notification SMS. Pure functions, no I/O.

It lives in the shared kernel because three layers read SMS text: the agent's quarantine
(to describe forwarded content as data), the guard's `record_sms` tool (to book exactly what the
SMS says, never what a model retyped) and the ledger. Regular expressions only: a forwarded SMS
is untrusted text, and parsing it must not involve a model that could be talked into anything.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.clock import BDT
from app.core.masking import normalize_msisdn
from app.core.money import normalize_digits, to_paisa

SmsType = Literal["received", "payment_received", "cash_in", "payment_sent", "send_money", "cash_out",
                  "bank_debit", "bank_credit"]

MONEY = r"(?:Tk\.?|BDT|৳)\s*(?P<{name}>[\d,]+(?:\.\d{{1,2}})?)"
TYPE_PATTERNS: list[tuple[SmsType, str, re.Pattern[str]]] = [
    ("payment_received", "bkash", re.compile(r"You have received payment " + MONEY.format(name="amount"), re.I)),
    ("received", "bkash", re.compile(r"You have received " + MONEY.format(name="amount"), re.I)),
    ("cash_in", "nagad", re.compile(r"Cash In Received\.?\s*Amount:\s*" + MONEY.format(name="amount"), re.I)),
    ("cash_in", "bkash", re.compile(r"Cash In " + MONEY.format(name="amount"), re.I)),
    ("received", "nagad", re.compile(r"Money Received\.?\s*Amount:\s*" + MONEY.format(name="amount"), re.I)),
    ("payment_sent", "nagad", re.compile(r"Payment Successful\.?\s*Amount:\s*" + MONEY.format(name="amount"), re.I)),
    ("send_money", "nagad", re.compile(r"Send Money Successful\.?\s*Amount:\s*" + MONEY.format(name="amount"), re.I)),
    ("cash_out", "nagad", re.compile(r"Cash Out Successful\.?\s*Amount:\s*" + MONEY.format(name="amount"), re.I)),
    ("payment_sent", "bkash", re.compile(r"Payment " + MONEY.format(name="amount") + r" to ", re.I)),
    ("send_money", "bkash", re.compile(r"Send Money " + MONEY.format(name="amount") + r" to ", re.I)),
    ("cash_out", "bkash", re.compile(r"Cash Out " + MONEY.format(name="amount") + r" to ", re.I)),
    ("bank_debit", "bank", re.compile(r"A/C\s+debited\s+" + MONEY.format(name="amount"), re.I)),
    ("bank_credit", "bank", re.compile(r"A/C\s+credited\s+(?:with\s+)?" + MONEY.format(name="amount"), re.I)),
]
DIRECTION = {"received": "in", "payment_received": "in", "cash_in": "in", "bank_credit": "in",
             "payment_sent": "out", "send_money": "out", "cash_out": "out", "bank_debit": "out"}
TRX_RE = re.compile(r"\b(?:TrxID|TxnID|Trx\s*ID|Txn\s*ID)[:\s]+(?P<id>[A-Z0-9]{6,14})\b", re.I)
BANK_REF_RE = re.compile(r"\bRef[:\s]+(?P<id>[A-Z0-9]{6,14})\b")
FEE_RE = re.compile(r"\b(?:Fee|Charge):?\s*(?:Tk\.?|BDT|৳)\s*(?P<v>[\d,]+(?:\.\d{1,2})?)", re.I)
BALANCE_RE = re.compile(r"\b(?:Balance|Avl\.?\s*Bal\.?):?\s*(?:Tk\.?|BDT|৳)\s*(?P<v>[\d,]+(?:\.\d{1,2})?)", re.I)
WHEN_RE = re.compile(r"\b(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})\s+(?P<H>\d{1,2}):(?P<M>\d{2})\b")
REF_RE = re.compile(r"\bRef:?\s+(?P<ref>[^.\n]{1,60}?)(?=\.\s|\s+(?:TxnID|TrxID|Fee|Charge|Balance)|$)", re.I)
NAME_NUMBER_RE = re.compile(r"(?:to|Receiver:)\s+(?P<name>[A-Za-z][A-Za-z0-9&.' -]{1,60}?)\s*\((?P<number>[\d+\s-]{10,16})\)", re.I)
BANK_PAYEE_RE = re.compile(r"transfer to\s+(?P<name>[A-Za-z][A-Za-z0-9&.' -]{1,60}?)\s+A/C", re.I)
NUMBER_AFTER_RE = re.compile(r"(?:from|to|Sender:|Receiver:|Uddokta:)\s*(?P<number>\+?[\d][\d\s-]{9,15})", re.I)
SMS_START_RE = re.compile(
    r"(You have received|Cash In |Payment Tk|Send Money |Cash Out |Money Received|Payment Successful|"
    r"Cash In Received|Your A/C (?:debited|credited))",
    re.I,
)


class ParsedSms(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: Literal["bkash", "nagad", "bank"]
    txn_type: SmsType
    direction: Literal["in", "out"]
    amount_paisa: int
    fee_paisa: int = 0
    balance_paisa: int | None = None
    counterparty_number: str | None = None
    counterparty_name: str | None = None
    reference: str | None = None
    trx_id: str
    occurred_at: datetime | None = None


def parse_sms(text: str) -> ParsedSms | None:
    """Parse one notification SMS, or return None if it is not one we can read with certainty."""
    body = normalize_digits(text).strip()
    for txn_type, provider, pattern in TYPE_PATTERNS:
        match = pattern.search(body)
        if match:
            break
    else:
        return None
    trx = TRX_RE.search(body) or (BANK_REF_RE.search(body) if provider == "bank" else None)
    if trx is None:
        return None
    amount = to_paisa(match.group("amount"))
    if amount <= 0:
        return None

    name = number = None
    if named := NAME_NUMBER_RE.search(body):
        name, number = named.group("name").strip(), normalize_msisdn(named.group("number"))
    elif provider == "bank" and (payee := BANK_PAYEE_RE.search(body)):
        name = payee.group("name").strip()
    if number is None and (found := NUMBER_AFTER_RE.search(body)):
        number = normalize_msisdn(found.group("number"))

    when = None
    if stamp := WHEN_RE.search(body):
        try:
            when = datetime(int(stamp["y"]), int(stamp["m"]), int(stamp["d"]), int(stamp["H"]), int(stamp["M"]),
                            tzinfo=BDT)
        except ValueError:
            when = None
    fee = FEE_RE.search(body)
    balance = BALANCE_RE.search(body)
    ref = REF_RE.search(body) if provider != "bank" else None
    return ParsedSms(
        provider=provider,
        txn_type=txn_type,
        direction=DIRECTION[txn_type],
        amount_paisa=amount,
        fee_paisa=to_paisa(fee.group("v")) if fee else 0,
        balance_paisa=to_paisa(balance.group("v")) if balance else None,
        counterparty_number=number,
        counterparty_name=name,
        reference=ref.group("ref").strip() if ref else None,
        trx_id=trx.group("id").upper(),
        occurred_at=when,
    )


def split_pasted_sms(message: str) -> tuple[str, str | None]:
    """If the owner pasted a notification SMS into their own message, separate it.

    Returns (the owner's own words, the pasted SMS or None). The SMS part is then treated as
    untrusted content even though the owner sent it."""
    start = SMS_START_RE.search(normalize_digits(message))
    if not start:
        return message, None
    candidate = message[start.start():]
    if parse_sms(candidate) is None:
        return message, None
    return message[:start.start()].strip(), candidate.strip()
