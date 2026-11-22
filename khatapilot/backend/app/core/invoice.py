"""Reading a supplier invoice the owner forwarded, without a model.

A deterministic parser, so the guard's invoice check never depends on what an LLM extracted: the
supplier (the first line), the invoice number, the total, the due date, the item lines, and every
payment number printed on it. The invoice is untrusted text; nothing here acts on what it says.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.core.masking import find_phone_numbers
from app.core.money import normalize_digits

AMOUNT = r"(?:৳|tk\.?|taka)?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:/-|taka|tk)?"
TOTAL = re.compile(rf"\b(?:grand\s+|net\s+|sub\s*)?total(?:\s+(?:due|payable|amount))?\b[^\d\n]{{0,15}}{AMOUNT}"
                   rf"|মোট[^\d\n]{{0,15}}{AMOUNT}", re.I)
NUMBER = re.compile(r"\b(?:invoice|inv|bill|memo|challan)\s*(?:no\.?|number|#)?\s*[:#.\-]?\s*#?\s*"
                    r"(?=[A-Za-z0-9\-/]*\d)([A-Za-z0-9\-/]{1,20})", re.I)
DUE = re.compile(r"\b(?:due|pay by|payment by|last date)\b[^\d\n]{0,12}(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?",
                 re.I)
ITEM = re.compile(rf"^(?P<what>[^\d\n][^\n]*?)[\s.:\-]+{AMOUNT}\s*$", re.I)
BANK_ACCOUNT = re.compile(r"\b(?:a/?c|account|acc(?:ount)?\s*no\.?|bank)\b[^\d\n]{0,20}(\d{10,17})\b", re.I)
CHANGED_ACCOUNT = re.compile(
    r"\b(?:new|changed?|updated?|different)\s+(?:bkash\s+|nagad\s+|bank\s+|payment\s+|mobile\s+)?"
    r"(?:number|no\.?|account|a/?c)\b|\b(?:number|account)\s+(?:has\s+|is\s+)?(?:changed|updated|new)\b"
    r"|\bpay\s+(?:to\s+)?this\s+(?:number|account)\s+instead\b|\bdo\s+not\s+(?:pay|send)\s+(?:to\s+)?the\s+old\b"
    r"|নতুন\s*(?:নম্বর|নাম্বার|একাউন্ট|অ্যাকাউন্ট)", re.I)
HEADER_WORDS = re.compile(r"\b(?:invoice|inv|bill|memo|challan|cash memo|tax invoice)\b.*$", re.I)


def _paisa(raw: str) -> int | None:
    try:
        return round(float(raw.replace(",", "")) * 100)
    except ValueError:
        return None


@dataclass
class InvoiceText:
    supplier: str
    invoice_number: str = ""
    total_paisa: int | None = None
    due: date | None = None
    items: list[tuple[str, int]] = field(default_factory=list)
    phone_numbers: list[str] = field(default_factory=list)
    bank_accounts: list[str] = field(default_factory=list)
    says_number_changed: bool = False

    @property
    def items_paisa(self) -> int:
        return sum(a for _, a in self.items)


def parse_invoice(text: str, today: date) -> InvoiceText:
    body = normalize_digits(text)
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    supplier = HEADER_WORDS.sub("", first).strip(" :#-–|") or first[:60] or "Unknown supplier"
    number = m.group(1).strip("-/") if (m := NUMBER.search(body)) else ""

    totals = [(_paisa(next(g for g in m.groups() if g)) or 0) for m in TOTAL.finditer(body)]
    total = totals[-1] if totals else None  # "Sub total ... Total ..." : the last one is what is owed

    due = None
    if m := DUE.search(body):
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        year = year + 2000 if year < 100 else year
        try:
            due = date(year, month, day)
            if not m.group(3) and (due - today).days < -180:  # "Due 20/01" written in December means next year
                due = date(year + 1, month, day)
        except ValueError:
            due = None

    items = []
    for line in lines[1:]:
        if TOTAL.search(line) or DUE.search(line) or NUMBER.search(line) or BANK_ACCOUNT.search(line):
            continue
        if find_phone_numbers(line):
            continue
        if (m := ITEM.match(line)) and (amount := _paisa(m.group(2))):
            items.append((m.group("what").strip(" .:-"), amount))

    return InvoiceText(
        supplier=supplier[:80], invoice_number=number[:40], total_paisa=total, due=due, items=items,
        phone_numbers=list(dict.fromkeys(find_phone_numbers(body))),
        bank_accounts=list(dict.fromkeys(m.group(1) for m in BANK_ACCOUNT.finditer(body))),
        says_number_changed=bool(CHANGED_ACCOUNT.search(body)),
    )
