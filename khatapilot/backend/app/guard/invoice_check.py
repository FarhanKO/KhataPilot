"""The invoice check: before the owner pays a supplier's invoice, compare it with the shop's own books.

Fake and doctored invoices are a common way to take money from a small shop: an invoice that asks
for payment to a "new" number, the same invoice sent twice, a total far above what the supplier
usually charges, or hidden instructions for an AI assistant. The check reads the forwarded invoice
with a deterministic parser (`app/core/invoice.py`, never the model's reading of it) and compares it
with facts the invoice cannot change: the trusted payees and the block list, the suppliers' saved
numbers, the bills already in the books, the supplier's past purchases and the invoices checked before.

Each finding has a level: "stop" (do not pay before calling the supplier on the number you already
know), "check" (look again before paying) or "note". Anything at "check" or worse goes to Alerts as a
safeguard alert. The check never pays, books or sends anything; a payment the owner asks for later
still goes through every guard check, PIN included.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.core.clock import local_date
from app.core.ids import new_id
from app.core.invoice import InvoiceText, parse_invoice
from app.core.masking import mask_number, mask_phone, normalize_msisdn
from app.core.money import fmt_taka
from app.guard.injection import scan
from app.guard.models import InvoiceCheckRecord

LEVELS = ("note", "check", "stop")
JUMP = 1.5  # a total this many times the supplier's usual is worth a second look
MIN_JUMP_PAISA = 100_000  # ...and at least ৳1,000 above it


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _shown(number: str) -> str:
    return mask_phone(number) if normalize_msisdn(number) else mask_number(number)


@dataclass
class Finding:
    level: str
    text: str


@dataclass
class InvoiceCheck:
    invoice: InvoiceText
    supplier_known: str | None = None
    findings: list[Finding] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        worst = max((LEVELS.index(f.level) for f in self.findings), default=-1)
        return "ok" if worst < LEVELS.index("check") else LEVELS[worst]

    def add(self, level: str, text: str) -> None:
        self.findings.append(Finding(level, text))

    def headline(self) -> str:
        total = fmt_taka(self.invoice.total_paisa) if self.invoice.total_paisa else "no total found"
        who = self.supplier_known or self.invoice.supplier
        if self.verdict == "stop":
            return f"Do not pay this invoice yet: {who}, {total}"
        if self.verdict == "check":
            return f"Check this invoice before paying: {who}, {total}"
        return f"Invoice looks consistent with your books: {who}, {total}"

    def view(self) -> dict[str, Any]:
        inv = self.invoice
        return {
            "supplier": inv.supplier, "matched_supplier": self.supplier_known,
            "invoice_number": inv.invoice_number or None,
            "total": fmt_taka(inv.total_paisa) if inv.total_paisa else None,
            "due": f"{inv.due:%a %d %b %Y}" if inv.due else None,
            "items": [{"item": what, "amount": fmt_taka(a)} for what, a in inv.items[:12]],
            "pay_to_numbers": [_shown(n) for n in inv.phone_numbers + inv.bank_accounts],
            "verdict": self.verdict, "headline": self.headline(),
            "findings": [{"level": f.level, "text": f.text} for f in self.findings],
            "note": ("A check, not a payment: nothing was paid, booked or sent. To pay, ask in your own words "
                     "(\"pay Meghna Distributors 42,175\"); every guard check still applies."),
        }


class InvoiceChecker:
    def __init__(self, guard) -> None:
        self.guard = guard

    def _supplier(self, name: str) -> tuple[str | None, str | None, str | None]:
        """(the supplier's name in the books, their saved number, the trusted payee's number)."""
        wanted = _norm(name)
        guard, shop_id = self.guard, self.guard.shop_id
        candidates = [(p.name, p.number) for p in guard.shops.parties(shop_id, "supplier")]
        candidates += [(p.name, p.number) for p in guard.store.payees()]
        best = None
        for known, number in candidates:
            key = _norm(known)
            if key and (key in wanted or wanted.startswith(key) or (len(wanted) >= 5 and key.startswith(wanted))):
                if best is None or len(key) > len(_norm(best[0])):
                    best = (known, number)
        if best is None:
            return None, None, None
        payee = guard.store.payee_by_name(best[0])
        party = guard.shops.find_party(shop_id, best[0], "supplier")
        return best[0], party.number if party else best[1], payee.number if payee else None

    def check(self, text: str) -> InvoiceCheck:
        guard = self.guard
        today = local_date(guard.clock.now())
        inv = parse_invoice(text, today)
        result = InvoiceCheck(inv)
        known, saved_number, trusted_number = self._supplier(inv.supplier)
        result.supplier_known = known

        # 1. Hidden instructions written for an AI assistant
        injection = scan(text)
        if injection.suspected:
            result.add("stop", f"It hides instructions for an AI assistant ({injection.summary().removeprefix('Suspected injection: ').rstrip('.')}). "
                               "A real supplier has no reason to do that; KhataPilot ignored them.")

        # 2. Where it asks to be paid
        pay_to = inv.phone_numbers + inv.bank_accounts
        for number in pay_to:
            if reason := guard.store.blocked_reason(number):
                result.add("stop", f"It asks for payment to {_shown(number)}, a number on your block list ({reason}).")
        known_numbers = {n for n in (saved_number, trusted_number) if n}
        strange = [n for n in pay_to if known_numbers and n not in known_numbers]
        if known and strange:
            result.add("stop", f"It asks for payment to {', '.join(_shown(n) for n in strange)}, but the number you "
                               f"have for {known} is {_shown(next(iter(known_numbers)))}. Call {known} on the number "
                               "you already know before paying.")
        if inv.says_number_changed:
            result.add("stop", "It says the supplier's number or account has changed. That is the commonest invoice "
                               "scam: confirm by calling them on the number you already know.")

        # 3. The same invoice twice
        digest = hashlib.sha256(" ".join(text.split()).encode()).hexdigest()
        earlier = self._earlier(digest, inv, known)
        if earlier is not None:
            when = f"{earlier.checked_at:%a %d %b}"
            result.add("check", f"You already forwarded this invoice on {when}"
                                + (f" (#{earlier.invoice_number})" if earlier.invoice_number else "")
                                + ". Make sure it is not paid twice.")
        if known and inv.total_paisa:
            for bill in guard.ledger.open_obligations(guard.shop_id):
                if (bill.kind != "baki" and _norm(bill.counterparty_name) == _norm(known)
                        and bill.amount_paisa == inv.total_paisa):
                    result.add("check", f"Your books already have {fmt_taka(bill.amount_paisa)} owed to {known}, due "
                                        f"{bill.due_date:%a %d %b}. This may be the same bill: pay it once.")
                    break

        # 4. The total against the supplier's usual and against its own lines
        if inv.total_paisa is None:
            result.add("check", "No total could be read from it. Check the amount on the paper invoice.")
        else:
            if inv.items and abs(inv.items_paisa - inv.total_paisa) > 100:
                result.add("check", f"Its lines add up to {fmt_taka(inv.items_paisa)}, but the total says "
                                    f"{fmt_taka(inv.total_paisa)}.")
            if known:
                usual = guard.ledger.purchases_from(guard.shop_id, known, since=today - timedelta(days=120))
                if len(usual) >= 3:
                    median = int(statistics.median(usual))
                    if inv.total_paisa >= median * JUMP and inv.total_paisa - median >= MIN_JUMP_PAISA:
                        result.add("check", f"{fmt_taka(inv.total_paisa)} is {inv.total_paisa / median:.1f} times what "
                                            f"you usually buy from {known} (about {fmt_taka(median)}). Check the "
                                            "quantities and prices.")
        if known is None:
            result.add("note", f"{inv.supplier} is not one of your suppliers yet. A first payment to them always "
                               "needs your PIN.")
        if inv.due and inv.due < today:
            result.add("note", f"Its due date ({inv.due:%d %b}) has already passed.")

        guard.session.add(InvoiceCheckRecord(
            id=new_id("invchk"), shop_id=guard.shop_id, text_sha256=digest, supplier=(known or inv.supplier)[:128],
            invoice_number=inv.invoice_number, total_paisa=inv.total_paisa, verdict=result.verdict,
            checked_at=guard.clock.now()))
        guard.session.flush()
        return result

    def _earlier(self, digest: str, inv: InvoiceText, known: str | None) -> InvoiceCheckRecord | None:
        rows = self.guard.session.scalars(
            select(InvoiceCheckRecord).where(InvoiceCheckRecord.shop_id == self.guard.shop_id)
            .order_by(InvoiceCheckRecord.checked_at.desc()).limit(500)).all()
        supplier = _norm(known or inv.supplier)
        for row in rows:
            if row.text_sha256 == digest:
                return row
            if inv.invoice_number and row.invoice_number == inv.invoice_number and _norm(row.supplier) == supplier:
                return row
        return None
