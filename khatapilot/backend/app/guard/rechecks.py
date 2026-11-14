"""Time-of-check versus time-of-use for held actions: what the owner approves may have been prepared up
to 30 minutes earlier, so the facts that can change in between are checked again on approval.

- The payee's number may have been blocked since.
- The same payment (same number, same amount) may already have gone out since it was prepared: two
  taps on Pay or Move, or the same request asked twice. The second is refused, not paid twice.
- A savings transfer worked out by the plan may no longer fit: this month's savings already moved,
  or cash changed so the amount is no longer safe. The owner prepares it again to get today's figure.

Each check returns a reason to refuse, or None. Nothing here can make an action run that the guard
held; it can only stop one.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.core.actions import ActionKind, ProposedAction
from app.core.clock import to_local
from app.core.masking import mask_number
from app.core.money import fmt_taka
from app.guard.policy_store import PolicyStore
from app.modules.payments.service import PaymentsService

SAVINGS_TOOL = "save_to_savings"


class HeldActionRechecks:
    def __init__(self, *, store: PolicyStore, payments: PaymentsService, savings_status: Callable[[], object]) -> None:
        self.store = store
        self.payments = payments
        self.savings_status = savings_status  # SavingsService.status, called only for savings transfers

    def __call__(self, action: ProposedAction, held_at: datetime) -> str | None:
        if action.kind is not ActionKind.PAYMENT or action.payment is None:
            return None
        p = action.payment
        if p.payee_number and (blocked := self.store.blocked_reason(p.payee_number)):
            return (f"{mask_number(p.payee_number)} was put on your blocked list after this was prepared "
                    f"({blocked}), so nothing was paid.")
        same = [r for r in self.payments.recent(held_at)
                if r.payee_number == p.payee_number and r.amount_paisa == p.amount_paisa]
        if same:
            when = to_local(same[0].created_at).strftime("%H:%M")
            what = "moved to your savings" if p.purpose == "savings" else f"to {p.payee_name or 'this payee'}"
            return (f"The same {fmt_taka(p.amount_paisa)} {what} already went out at {when}, after this one was "
                    "prepared, so it was not paid twice. Ask again if you really want a second payment.")
        if action.tool == SAVINGS_TOOL and (action.params.get("savings") or {}).get("source") == "plan":
            status = self.savings_status()
            if status.remaining_paisa <= 0:
                return "This month's savings were already moved, so this transfer was not made."
            if p.amount_paisa > status.remaining_paisa:
                return (f"Only {fmt_taka(status.remaining_paisa)} is left to save this month, not "
                        f"{fmt_taka(p.amount_paisa)}. Prepare it again to move the right amount.")
            if p.amount_paisa > status.safe_now_paisa:
                return (f"Cash has changed since this was prepared: {fmt_taka(status.safe_now_paisa)} is safe to move "
                        "now. Prepare it again to move today's amount.")
        return None
