"""Public API of the reminders module: which customers to remind, the exact text, sending it (which
needs the guard's authorization) and who has paid since.

The model never writes a reminder. It may only say how long a customer must have owed, or name
customers; the recipients, numbers and amounts come from the books and the words from a fixed
template, so nothing a forwarded message says can end up in a text to a customer.

Every reminder says how to stop them (reply STOP). A customer who did, or who asked the owner, gets
none: they are left out when reminders are prepared, again when the owner approves them, and the
send itself refuses. Only the customer can undo their own STOP (reply START)."""

from __future__ import annotations

import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.actions import AuthorizedAction, sha256_hex
from app.core.clock import Clock, local_date
from app.core.ids import new_id
from app.core.masking import e164, mask_phone, normalize_msisdn
from app.core.money import fmt_taka
from app.infra.phone_gateway import PhoneGateway, PhoneGatewayError
from app.infra.sandbox_client import SandboxClient, SandboxError
from app.modules.ledger.service import LedgerService
from app.modules.reminders.models import ReminderOptOut, ReminderRecord
from app.modules.reminders.schemas import (
    REMINDER_KIND,
    Language,
    NothingToRemind,
    OptOutLocked,
    OptOutView,
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
                "{since}. Please pay at the shop or by bKash when you can. Thank you! Reply STOP for no more "
                "reminders."),
    "banglish": ("Assalamu alaikum {name}, {shop} theke bolchi. Apnar {owed} baki {since} theke ache. Shubidha moto "
                 "dokane ba bKash e diye diben. Dhonnobad! Ar reminder na chaile STOP likhe pathan."),
}

# A customer's reply that means "no more reminders" / "send them again". Only short, unmistakable replies:
# anything longer goes to the owner as an ordinary message, so nobody is opted out on a guess.
_STOP = (r"stop|stop (?:it|this|sms|messages?|reminders?|sending(?: me)?(?: (?:sms|messages?|reminders?))?)|"
         r"unsubscribe|opt ?out|no more (?:sms|messages?|reminders?)|"
         r"(?:please |pls |plz )?(?:don'?t|do not) (?:send|message|text)(?: me)?(?: (?:sms|messages?|reminders?))?"
         r"(?: (?:any ?more|again))?|"
         r"bondho|bondho koren|sms bondho|message bondho|reminder bondho|ar (?:sms|message|reminder) diben na|"
         r"বন্ধ|বন্ধ করুন|এসএমএস বন্ধ|মেসেজ বন্ধ|আর মেসেজ দিবেন না")
_START = r"start|unstop|resume|subscribe|opt ?in|chalu|chalu koren|sms chalu|চালু|চালু করুন"
STOP_REPLY = re.compile(rf"^(?:{_STOP})(?: please| pls| plz)?$", re.I)
START_REPLY = re.compile(rf"^(?:{_START})(?: please| pls| plz)?$", re.I)


def _reply_words(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'ঀ-৿]", " ", text.lower())).strip()


def reply_choice(text: str) -> str | None:
    """'stop' or 'start' when a customer's whole reply asks for that, else None."""
    words = _reply_words(text)
    if STOP_REPLY.match(words):
        return "stop"
    if START_REPLY.match(words):
        return "start"
    return None


def _clean(name: str) -> str:
    """Names come from the books, which can hold text from an SMS; keep only what a name needs."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w .'-]", "", name)).strip()[:40] or "there"


def render(language: Language, *, name: str, shop: str, owed_paisa: int, since: date | None) -> str:
    when = f"{since:%d %b}" if since else "some time"
    return TEMPLATES[language].format(name=_clean(name), shop=_clean(shop), owed=fmt_taka(owed_paisa), since=when)


class RemindersService:
    def __init__(self, session: Session, clock: Clock, shop_id: str, sandbox: SandboxClient | None, *,
                 phone: PhoneGateway | None = None) -> None:
        self.session = session
        self.clock = clock
        self.shop_id = shop_id
        self.sandbox = sandbox
        self.phone = phone  # when set, a reminder the gateway accepted also leaves from the owner's phone
        self.ledger = LedgerService(session, clock)

    # ---- who to remind, and the exact words

    def plan(self, *, min_days: int = DEFAULT_MIN_DAYS, names: list[str] | None = None,
             language: Language = "english") -> ReminderPlan:
        debtors = self.ledger.baki_by_customer(self.shop_id)
        skipped: list[Skipped] = []
        if names:
            chosen, skipped = self.resolve(names)
            if not chosen:
                reasons = "; ".join(f"{s.name}: {s.why}" for s in skipped)
                raise NothingToRemind(f"No reminder was prepared ({reasons}).")
        else:
            chosen = [d for d in debtors if (d.days_outstanding or 0) >= min_days]
        if not chosen:
            raise NothingToRemind(f"Nobody has owed the shop for {min_days} days or more, so there is nobody to remind.")
        shop = ShopService(self.session, self.clock).get(self.shop_id)
        recent = self.recently_reminded()
        drafts = []
        today = local_date(self.clock.now())
        stopped = {o.number: o for o in self._opt_out_rows()}
        for d in chosen:
            number = normalize_msisdn(d.number) if d.number else None
            if number in stopped:  # even when the owner names them: the customer said no
                asked = "asked" if stopped[number].source == "customer" else "asked you"
                since = local_date(stopped[number].opted_out_at)
                skipped.append(Skipped(name=d.name, why=f"{asked} for no reminders (since {since:%d %b})"))
            elif not names and d.expected_by and d.expected_by >= today:
                # They said when they would pay and that day has not passed: no nudge unless the owner names them.
                skipped.append(Skipped(name=d.name, why=f"promised to pay by {d.expected_by:%a %d %b}"))
            elif number is None:
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

    def resolve(self, names: list[str]) -> tuple[list, list[Skipped]]:
        """The customers the owner named, as the books have them: the exact name, else the only debtor whose
        name has all of its words ("Alam" is "Jahangir Alam"). A name that fits nobody who owes, or more than
        one, is left out with the reason, so one unknown name does not stop the others' reminders."""
        chosen, skipped, seen = [], [], set()
        for raw in names:
            name = re.sub(r"\s+", " ", raw).strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            matches = self.ledger.debtors_matching(self.shop_id, name)
            if normalize_msisdn(name):  # a number is never shown in full
                name = mask_phone(name)
            if len(matches) > 1:
                skipped.append(Skipped(name=name, why="more than one customer has that name ("
                                       + ", ".join(m.name for m in matches[:4]) + "); say the full name"))
            elif not matches:
                skipped.append(Skipped(name=name, why="does not owe the shop anything in the books"))
            elif matches[0].name.lower() not in {c.name.lower() for c in chosen}:
                chosen.append(matches[0])
        return chosen, skipped

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
        if self.opted_out(draft.to_number):
            raise SendRefused("the customer asked for no reminders")
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
        if row.status == "sent" and self.phone is not None:
            # The gateway checked the guard's token for exactly this number and text; the phone only carries it.
            try:
                state = self.phone.send_sms([e164(draft.to_number)], draft.body)
                row.sandbox_ref = f"{row.sandbox_ref} phone {str(state.get('id', ''))[:10]}".strip()[:32]
            except PhoneGatewayError as exc:
                row.status, row.failure = "failed", f"PHONE: {exc}"
        self.session.add(row)
        self.session.flush()
        return self._view(row)

    # ---- customers who asked for no reminders

    def _opt_out_rows(self) -> list[ReminderOptOut]:
        return list(self.session.scalars(select(ReminderOptOut).where(ReminderOptOut.shop_id == self.shop_id)
                                         .order_by(ReminderOptOut.opted_out_at.desc())))

    def opted_out(self, number: str | None) -> ReminderOptOut | None:
        normal = normalize_msisdn(number) if number else None
        return self.session.get(ReminderOptOut, (self.shop_id, normal)) if normal else None

    def opt_outs(self) -> list[OptOutView]:
        return [OptOutView(customer_name=o.customer_name, number_masked=mask_phone(o.number), source=o.source,
                           words=o.words, since=o.opted_out_at, owner_can_lift=o.source == "owner")
                for o in self._opt_out_rows()]

    def name_for(self, number: str) -> str | None:
        normal = normalize_msisdn(number)
        found = next((b for b in self.ledger.baki_by_customer(self.shop_id)
                      if b.number and normalize_msisdn(b.number) == normal), None)
        return found.name if found else None

    def customer_number(self, name: str) -> tuple[str, str]:
        """(the name as the books have it, the number) for a customer the owner names."""
        found = next((b for b in self.ledger.baki_by_customer(self.shop_id) if b.name.lower() == name.strip().lower()),
                     None)
        if found is None:
            raise NothingToRemind(f"{name} is not a customer with baki in the books.")
        number = normalize_msisdn(found.number) if found.number else None
        if number is None:
            raise NothingToRemind(f"There is no mobile number for {found.name} in the books, so no reminder can go "
                                  "to them anyway.")
        return found.name, number

    def opt_out(self, number: str, *, source: str, name: str | None = None, words: str | None = None) -> bool:
        """No more reminders to `number`. False if they had already opted out (a customer's own STOP stays
        the customer's even if the owner records it again)."""
        normal = normalize_msisdn(number)
        if normal is None:
            raise ValueError("not a mobile number")
        if existing := self.opted_out(normal):
            if source == "customer" and existing.source != "customer":  # now theirs: only they can lift it
                existing.source, existing.words = "customer", (words or "")[:160] or None
                self.session.flush()
            return False
        self.session.add(ReminderOptOut(shop_id=self.shop_id, number=normal,
                                        customer_name=name or self.name_for(normal), source=source,
                                        words=(words or "")[:160] or None, opted_out_at=self.clock.now()))
        self.session.flush()
        return True

    def opt_in(self, number: str, *, source: str) -> bool:
        """Reminders allowed again. The owner cannot lift a customer's own STOP (OptOutLocked)."""
        row = self.opted_out(number)
        if row is None:
            return False
        if row.source == "customer" and source != "customer":
            who = row.customer_name or mask_phone(row.number)
            raise OptOutLocked(f"{who} replied STOP to a reminder, so only they can turn reminders back on (by "
                               "replying START). KhataPilot will not text them until then.")
        self.session.delete(row)
        self.session.flush()
        return True

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
