"""The owner's right to have their data deleted: "delete all my data".

Two doors lead here, and both need the owner's PIN: the Delete button under Export in the Credit tab
(`OwnerControls.erase_data`) and a request in chat, which the guard holds as a tier 3 settings change
until the owner confirms it with the PIN in the approval dialog (`control_tools.py`).

What goes: everything the shop's books are made of. The ledger, customers' baki and the bills, the
payment records (a scheduled payment is cancelled), suppliers and customers, the chats, baki reminders,
the phone inbox, savings plan and goals, the credit consent and profiles, loan applications, and
remembered invoices.

What stays, and why:
- the shop's profile and the owner's PIN, so the owner can still sign in and start again;
- the safety settings (limits, trusted payees, blocked numbers, STOP, the safety buffer), so the guard
  keeps protecting the shop from the first new message;
- customers' own STOP replies to reminders: that choice is theirs, not the shop's to delete;
- the hash-chained audit log, with every phone number masked. It is append-only by design: it is the
  record that proves what KhataPilot did and refused, including this deletion;
- the month's AI usage count (it holds no shop data);
- the provider's own records of real transactions, which belong to the provider, not to KhataPilot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.guard.models import PendingAction
from app.infra.db import Base

# The owner's own words asking for their data to go, in English, Banglish or Bangla. Deleting needs these in
# the owner's own message (checks.owner_settings): a forwarded invoice that says "delete all data" cannot start it.
ERASE_WORDS = re.compile(
    r"\b(?:delete|erase|wipe|remove|clear|destroy)\b|\b(?:muche|muchhe|mucha|muse)\s*(?:dao|felo|fel|den)\b"
    r"|\bdelete\s*(?:koro|kore\s*dao|kor)\b|মুছ|ডিলিট", re.I)

# Every table of the shop's own data, with the words the owner sees. A new table holding the owner's
# data must be added here (tests/unit/test_data_rights.py checks that no shop table is forgotten).
ERASED: dict[str, str] = {
    "ledger_entries": "entries in the books",
    "ledger_obligations": "baki and dues",
    "ledger_recurring_bills": "regular bills",
    "ledger_sms_questions": "questions about SMS",
    "payments": "payment records",
    "shop_parties": "suppliers and customers",
    "chat_messages": "chat messages",
    "chat_turn_cards": "chat cards",
    "baki_reminders": "baki reminders",
    "phone_sms": "SMS from your phone",
    "savings_plans": "savings plan",
    "savings_goals": "savings goals",
    "credit_consents": "credit consent records",
    "credit_profiles": "credit profiles",
    "loan_applications": "loan applications",
    "guard_owner_intents": "remembered payment requests",
    "guard_invoice_checks": "checked invoices",
}

KEPT: dict[str, str] = {
    "guard_limits": "payment limits",
    "guard_trusted_payees": "trusted payees",
    "guard_blocked_numbers": "blocked numbers",
    "guard_owner_security": "your PIN",
    "guard_kill_switch": "STOP",
    "guard_pending_actions": "the approval record of this deletion",
    "forecast_buffer_settings": "your safety buffer",
    "reminder_opt_outs": "customers who asked for no reminders",
    "phone_inbox_state": "where the phone inbox last read up to",
    "llm_usage_months": "the month's AI usage count",
}

KEPT_WORDS = ("Kept: your shop profile and PIN, your safety settings (limits, trusted payees, blocked numbers), "
              "customers' own requests for no reminders, and the audit log (append-only, numbers masked), which "
              "records this deletion too.")


@dataclass
class ErasePreview:
    counts: dict[str, int] = field(default_factory=dict)  # table -> rows that would go
    scheduled_payments: int = 0

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def lines(self) -> list[str]:
        return [f"{n:,} {ERASED[t]}" for t, n in self.counts.items() if n]

    def view(self) -> dict[str, Any]:
        return {"total": self.total, "items": [{"what": ERASED[t], "count": n} for t, n in self.counts.items() if n],
                "scheduled_payments": self.scheduled_payments, "kept": KEPT_WORDS}


def _tables() -> dict[str, Any]:
    tables = Base.metadata.tables
    return {name: tables[name] for name in ERASED if name in tables and "shop_id" in tables[name].c}


def preview(session: Session, shop_id: str) -> ErasePreview:
    found = ErasePreview()
    for name, table in _tables().items():
        found.counts[name] = session.scalar(select(func.count()).select_from(table)
                                            .where(table.c.shop_id == shop_id)) or 0
    payments = Base.metadata.tables.get("payments")
    if payments is not None:
        found.scheduled_payments = session.scalar(
            select(func.count()).select_from(payments).where(payments.c.shop_id == shop_id,
                                                             payments.c.status.in_(("scheduled", "paused")))) or 0
    return found


def erase(session: Session, shop_id: str, *, keep_decision: str | None = None) -> ErasePreview:
    """Delete the shop's data. Anything else still waiting for approval is cancelled too (a held
    reminder or payment must not go out after its books are gone); `keep_decision` is the held
    deletion itself, when the owner approved it from chat."""
    done = preview(session, shop_id)
    for table in _tables().values():
        session.execute(delete(table).where(table.c.shop_id == shop_id))
    for row in session.scalars(select(PendingAction).where(PendingAction.shop_id == shop_id,
                                                           PendingAction.status == "pending")):
        if row.decision_id != keep_decision:
            row.status = "rejected"
    session.flush()
    return done


def summary_words(found: ErasePreview) -> str:
    what = ", ".join(found.lines()) or "nothing (the books are already empty)"
    cancelled = (f" {found.scheduled_payments} scheduled payment{'s' if found.scheduled_payments != 1 else ''} "
                 "will be cancelled." if found.scheduled_payments else "")
    return f"Delete all your shop's data: {what}.{cancelled}"
