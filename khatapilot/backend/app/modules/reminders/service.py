"""Public API of the reminders module: which customers to remind, the exact text, sending it (which
needs the guard's authorization) and who has paid since.

The model never writes a reminder. It may only say how long a customer must have owed, or name
customers; the recipients, numbers and amounts come from the books and the words from a fixed
template, so nothing a forwarded message says can end up in a text to a customer."""

from __future__ import annotations

import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.actions import AuthorizedAction, sha256_hex
from app.core.clock import Clock
from app.core.ids import new_id
from app.core.masking import mask_phone, normalize_msisdn
from app.core.money import fmt_taka
from app.infra.sandbox_client import SandboxClient, SandboxError
from app.modules.ledger.service import LedgerService
from app.modules.reminders.models import ReminderRecord
from app.modules.reminders.schemas import (
    REMINDER_KIND,
    Language,
    NothingToRemind,
    ReminderDraft,
    ReminderPlan,
    ReminderView,
    SendRefused,
    Skipped,
)
from app.modules.shop.service import ShopService

DEFAULT_MIN_DAYS = 14
REMIND_AGAIN_AFTER_DAYS = 7  # a customer gets at most one reminder a week
MAX_PER_BATCH = 20

TEMPLATES: dict[Language, str] = {
    "english": ("Assalamu alaikum {name}, this is {shop}. A friendly reminder: {owed} of baki has been open since "
                "{since}. Please pay at the shop or by bKash when you can. Thank you!"),
    "banglish": ("Assalamu alaikum {name}, {shop} theke bolchi. Apnar {owed} baki {since} theke ache. Shubidha moto "
                 "dokane ba bKash e diye diben. Dhonnobad!"),
}


def _clean(name: str) -> str:
    """Names come from the books, which can hold text from an SMS; keep only what a name needs."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w .'-]", "", name)).strip()[:40] or "there"


def render(language: Language, *, name: str, shop: str, owed_paisa: int, since: date | None) -> str:
    when = f"{since:%d %b}" if since else "some time"
    return TEMPLATES[language].format(name=_clean(name), shop=_clean(shop), owed=fmt_taka(owed_paisa), since=when)


class RemindersService:
    def __init__(self, session: Session, clock: Clock, shop_id: str, sandbox: SandboxClient | None) -> None:
        self.session = session
        self.clock = clock
        self.shop_id = shop_id
        self.sandbox = sandbox
        self.ledger = LedgerService(session, clock)

    # ---- who to remind, and the exact words

    def plan(self, *, min_days: int = DEFAULT_MIN_DAYS, names: list[str] | None = None,
             language: Language = "english") -> ReminderPlan:
        debtors = self.ledger.baki_by_customer(self.shop_id)
        if names:
            wanted = {n.strip().lower() for n in names if n.strip()}
            unknown = sorted(wanted - {d.name.lower() for d in debtors})
            if unknown:
                raise NothingToRemind(f"{', '.join(unknown)} {'does' if len(unknown) == 1 else 'do'} not owe the shop "
                                      "anything in the books, so no reminder was prepared.")
            chosen = [d for d in debtors if d.name.lower() in wanted]
        else:
            chosen = [d for d in debtors if (d.days_outstanding or 0) >= min_days]
        if not chosen:
            raise NothingToRemind(f"Nobody has owed the shop for {min_days} days or more, so there is nobody to remind.")
        shop = ShopService(self.session, self.clock).get(self.shop_id)
        recent = self.recently_reminded()
        drafts, skipped = [], []
        for d in chosen:
            number = normalize_msisdn(d.number) if d.number else None
            if number is None:
                skipped.append(Skipped(name=d.name, why="no mobile number in the books"))
            elif d.name.lower() in recent:
                skipped.append(Skipped(name=d.name, why=f"already reminded in the last {REMIND_AGAIN_AFTER_DAYS} days"))
            elif len(drafts) >= MAX_PER_BATCH:
                skipped.append(Skipped(name=d.name, why=f"at most {MAX_PER_BATCH} reminders at a time"))
            else:
                body = render(language, name=d.name, shop=shop.name, owed_paisa=d.outstanding_paisa,
                              since=d.oldest_open_since)
                drafts.append(ReminderDraft(
                    customer_name=d.name, to_number=number, owed_paisa=d.outstanding_paisa,
                    days_outstanding=d.days_outstanding or 0, open_since=d.oldest_open_since, language=language,
                    from_account=shop.bkash_account, body=body, body_sha256=sha256_hex(body)))
        if not drafts:
            reasons = "; ".join(f"{s.name}: {s.why}" for s in skipped)
            raise NothingToRemind(f"No reminder can go out right now ({reasons}).")
        return ReminderPlan(drafts=drafts, skipped=skipped)

    def recently_reminded(self, days: int = REMIND_AGAIN_AFTER_DAYS) -> set[str]:
        since = self.clock.now() - timedelta(days=days)
        rows = self.session.scalars(select(ReminderRecord.customer_name).where(
            ReminderRecord.shop_id == self.shop_id, ReminderRecord.status == "sent", ReminderRecord.sent_at >= since))
        return {name.lower() for name in rows}

    def outstanding(self, name: str) -> int:
        found = next((b for b in self.ledger.baki_by_customer(self.shop_id) if b.name.lower() == name.lower()), None)
        return found.outstanding_paisa if found else 0

    # ---- sending (only with the guard's authorization)

    @staticmethod
    def auth_params(draft: ReminderDraft) -> dict:
        """Exactly what the guard's token binds (and the messaging gateway checks)."""
        return {"from_account": draft.from_account, "to_number": draft.to_number, "channel": draft.channel,
                "body_sha256": draft.body_sha256}

    def send(self, draft: ReminderDraft, auth: AuthorizedAction, *, decision_id: str) -> ReminderView:
        if sha256_hex(draft.body) != draft.body_sha256:
            raise SendRefused("the reminder's text changed after it was prepared")
        if not auth.binds(REMINDER_KIND, self.auth_params(draft)):
            raise SendRefused("the authorization does not bind this reminder")
        if self.sandbox is None:
            raise SendRefused("no messaging gateway configured")
        row = ReminderRecord(
            id=new_id("rem"), shop_id=self.shop_id, customer_name=draft.customer_name, to_number=draft.to_number,
            owed_paisa=draft.owed_paisa, days_outstanding=draft.days_outstanding, language=draft.language,
            body=draft.body, body_sha256=draft.body_sha256, idempotency_key=auth.idempotency_key, status="sent",
            decision_id=decision_id, sent_at=self.clock.now(),
        )
        try:
            result = self.sandbox.send_message(
                {"from_account": draft.from_account, "to_number": draft.to_number, "channel": draft.channel,
                 "body": draft.body},
                authorization=auth.header, idempotency_key=auth.idempotency_key)
            row.sandbox_ref = result["message_id"]
        except SandboxError as exc:
            row.status, row.failure = "failed", f"{exc.code}: {exc.message}"
        self.session.add(row)
        self.session.flush()
        return self._view(row)

    # ---- who has paid since

    def sent(self, limit: int = 50) -> list[ReminderView]:
        rows = self.session.scalars(select(ReminderRecord).where(ReminderRecord.shop_id == self.shop_id)
                                    .order_by(ReminderRecord.sent_at.desc(), ReminderRecord.customer_name)
                                    .limit(limit))
        return [self._view(r) for r in rows]

    def _view(self, row: ReminderRecord) -> ReminderView:
        paid = self.ledger.repaid_since(self.shop_id, name=row.customer_name, number=row.to_number, since=row.sent_at)
        owes = self.outstanding(row.customer_name)
        if row.status != "sent":
            outcome = "not_sent"
        elif owes == 0:
            outcome = "paid"
        elif paid > 0:
            outcome = "part_paid"
        else:
            outcome = "waiting"
        return ReminderView(
            id=row.id, customer_name=row.customer_name, to_masked=mask_phone(row.to_number), owed_paisa=row.owed_paisa,
            days_outstanding=row.days_outstanding, body=row.body, status=row.status, sandbox_ref=row.sandbox_ref,
            failure=row.failure, sent_at=row.sent_at, decision_id=row.decision_id, paid_since_paisa=paid,
            still_owes_paisa=owes, outcome=outcome)
