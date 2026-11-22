"""Public API of the inbox module: remember each money SMS from the owner's phone once, and what became of it.

This module only keeps records. Whether an SMS is booked is decided by the guard (the `record_sms` tool,
the same path as an SMS the owner forwards in chat), and reading the phone is the interface layer's job."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.actions import sha256_hex
from app.core.clock import Clock
from app.core.ids import new_id
from app.core.sms import ParsedSms
from app.modules.inbox.models import PhoneInboxState, PhoneSms
from app.modules.inbox.schemas import PhoneSmsView, Status


def body_hash(sender: str, body: str) -> str:
    """The same SMS fetched twice (by the webhook and again by a pull, say) has the same hash."""
    return sha256_hex(f"{sender.strip().lower()}\n{' '.join(body.split())}")


class InboxService:
    def __init__(self, session: Session, clock: Clock, shop_id: str) -> None:
        self.session = session
        self.clock = clock
        self.shop_id = shop_id

    def state(self) -> PhoneInboxState:
        state = self.session.get(PhoneInboxState, self.shop_id)
        if state is None:
            state = PhoneInboxState(shop_id=self.shop_id, skipped=0)
            self.session.add(state)
            self.session.flush()
        return state

    def _touch(self, received_at: datetime | None) -> None:
        state = self.state()
        state.last_seen_at = self.clock.now()
        if received_at and (state.newest_received_at is None or received_at > state.newest_received_at):
            state.newest_received_at = received_at
        self.session.flush()

    def seen(self, digest: str) -> PhoneSms | None:
        return self.session.scalar(select(PhoneSms).where(PhoneSms.shop_id == self.shop_id,
                                                          PhoneSms.body_sha256 == digest))

    def skip(self, received_at: datetime | None = None) -> None:
        """An SMS that is not about money: counted, never stored."""
        self.state().skipped += 1
        self._touch(received_at)

    def add(self, *, digest: str, sender: str, sender_kind: str, source: str, body: str,
            received_at: datetime | None, sms: ParsedSms | None, status: Status, detail: str = "") -> PhoneSms:
        row = PhoneSms(id=new_id("psms"), shop_id=self.shop_id, body_sha256=digest, sender=sender[:40],
                       sender_kind=sender_kind, source=source, body=body, received_at=received_at,
                       seen_at=self.clock.now(), provider=sms.provider if sms else None,
                       trx_id=sms.trx_id if sms else None, direction=sms.direction if sms else None,
                       amount_paisa=sms.amount_paisa if sms else None, status=status, detail=detail)
        self.session.add(row)
        self._touch(received_at)
        return row

    def settle(self, row: PhoneSms, status: Status, detail: str, *, decision_id: str | None = None,
               question_id: str | None = None, resolved: bool = False) -> PhoneSms:
        row.status, row.detail = status, detail
        row.decision_id = decision_id or row.decision_id
        row.question_id = question_id or row.question_id
        if resolved:
            row.resolved_at = self.clock.now()
        self.session.flush()
        return row

    def get(self, sms_id: str) -> PhoneSms | None:
        row = self.session.get(PhoneSms, sms_id)
        return row if row and row.shop_id == self.shop_id else None

    def by_question(self, question_id: str) -> PhoneSms | None:
        return self.session.scalar(select(PhoneSms).where(PhoneSms.shop_id == self.shop_id,
                                                          PhoneSms.question_id == question_id))

    def for_trx(self, trx_id: str) -> list[PhoneSms]:
        """Every SMS from the phone with this TrxID, newest first."""
        return list(self.session.scalars(select(PhoneSms).where(PhoneSms.shop_id == self.shop_id,
                                                                PhoneSms.trx_id == trx_id)
                                         .order_by(PhoneSms.seen_at.desc(), PhoneSms.id.desc())))

    def recent(self, limit: int = 30) -> list[PhoneSmsView]:
        rows = self.session.scalars(select(PhoneSms).where(PhoneSms.shop_id == self.shop_id)
                                    .order_by(PhoneSms.seen_at.desc(), PhoneSms.id.desc()).limit(limit))
        return [PhoneSmsView.model_validate(r) for r in rows]

    def counts(self) -> dict[str, int]:
        rows = self.session.execute(select(PhoneSms.status, func.count()).where(PhoneSms.shop_id == self.shop_id)
                                    .group_by(PhoneSms.status))
        found = {status: n for status, n in rows}
        found["skipped"] = self.state().skipped
        return found

    def pulled(self) -> None:
        self.state().last_pull_at = self.clock.now()
        self.session.flush()
