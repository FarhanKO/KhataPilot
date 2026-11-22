"""Public API of the ledger module."""

from __future__ import annotations

import re
import statistics
from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import date, datetime, timedelta

from sqlalchemy import func, insert, or_, select
from sqlalchemy.orm import Session

from app.core.clock import Clock, local_date
from app.core.ids import new_id
from app.core.masking import normalize_msisdn
from app.modules.ledger.models import LedgerEntry, Obligation, RecurringBill, SmsQuestion
from app.core.sms import ParsedSms
from app.modules.ledger.categories import categorize  # noqa: F401  (re-exported: part of the public API)
from app.modules.ledger.schemas import (  # AmbiguousBill is re-exported: callers catch it
    CASHFLOW_EXCLUDED_KINDS,
    AmbiguousBill,
    LIQUID_METHODS,
    BookDay,
    BookedSms,
    CustomerBalance,
    DailyFlow,
    SmsPurpose,
    SmsQuestionView,
    DayTotals,
    DuplicateSms,
    RepaymentProfile,
    SupplierCycle,
    LedgerEntryView,
    NewLedgerEntry,
    NewObligation,
    ObligationView,
    RecurringBillView,
    SAVINGS_CATEGORY,
)
from app.modules.shop.service import ShopService


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


# Entries that change what customers owe. A baki_adjustment is the owner forgiving baki or correcting it:
# "out" lowers what the customer owes, "in" raises it. It is method "credit": no money moves.
BAKI_KINDS = ("baki_sale", "baki_repayment", "baki_adjustment")


def raises_baki(kind: str, direction: str) -> bool:
    return kind == "baki_sale" or (kind == "baki_adjustment" and direction == "in")


class LedgerService:
    def __init__(self, session: Session, clock: Clock) -> None:
        self.session = session
        self.clock = clock

    # ---- writing

    def _row(self, entry: NewLedgerEntry) -> dict:
        return {
            "id": new_id("led"),
            "local_date": local_date(entry.occurred_at),
            "created_at": self.clock.now(),
            **entry.model_dump(),
        }

    def add_entry(self, entry: NewLedgerEntry) -> LedgerEntryView:
        row = LedgerEntry(**self._row(entry))
        self.session.add(row)
        self.session.flush()
        return LedgerEntryView.model_validate(row)

    def bulk_import(self, entries: Iterable[NewLedgerEntry]) -> int:
        rows = [self._row(e) for e in entries]
        if rows:
            self.session.execute(insert(LedgerEntry), rows)
        return len(rows)

    def debtor_for_sms(self, shop_id: str, sms: ParsedSms) -> CustomerBalance | None:
        """The customer who owes baki and sent this money in, if the books can tell: a saved customer with this
        number; else the one debtor whose baki entries carry it; else the one debtor whose name is exactly the
        name the SMS gives. Only ever used to ask the owner, or with the owner's own word on what it is for."""
        if sms.direction != "in" or sms.txn_type == "cash_in":
            return None
        owing = self.baki_by_customer(shop_id)
        number = sms.counterparty_number
        customer = ShopService(self.session, self.clock).find_party_by_number(shop_id, number, kind="customer")
        if customer is not None:
            return next((b for b in owing if b.name == customer.name), None)
        by_number = [b for b in owing if number and b.number == number]
        if by_number:
            return by_number[0] if len(by_number) == 1 else None
        if sms.counterparty_name and not normalize_msisdn(sms.counterparty_name):
            named = [b for b in owing if _norm(b.name) == _norm(sms.counterparty_name)]
            if len(named) == 1:
                return named[0]
        return None

    def book_sms(self, shop_id: str, sms: ParsedSms, *, at: datetime, source_ref: str | None,
                 purpose: SmsPurpose = "auto", product_note: str | None = None,
                 customer: str | None = None) -> BookedSms:
        """Book a parsed notification SMS. Deterministic: money from a customer who owes baki is a
        baki repayment (unless `purpose` says it is for something else; anything over what they owe
        is a sale), otherwise a sale; money to a known supplier settles their payable; cash in and
        cash out are transfers between the drawer and the wallet; fees are an expense. `customer` is
        who money in is from when the owner said so (their name as the books have it); otherwise the
        sender is found from the books (see `debtor_for_sms`)."""
        if self.has_trx(shop_id, sms.trx_id):
            raise DuplicateSms(sms.trx_id)
        shops = ShopService(self.session, self.clock)
        when = sms.occurred_at or at
        method = sms.provider
        entries: list[LedgerEntryView] = []

        def add(**fields) -> None:
            entries.append(self.add_entry(NewLedgerEntry(shop_id=shop_id, occurred_at=when, source="sms",
                                                         source_ref=source_ref, **fields)))

        party = shops.find_party_by_number(shop_id, sms.counterparty_number, kind="customer")
        supplier = shops.find_party_by_number(shop_id, sms.counterparty_number, kind="supplier") or (
            shops.find_party(shop_id, sms.counterparty_name, kind="supplier") if sms.counterparty_name else None)
        outstanding, settled, over = None, [], 0

        if sms.direction == "in" and sms.txn_type != "cash_in":
            known = customer
            if known is None and purpose != "other":
                known = debtor.name if (debtor := self.debtor_for_sms(shop_id, sms)) else None
            if known is None and party:
                known = party.name
            booked = self.book_customer_payment(
                shop_id, name=known or sms.counterparty_name,
                number=sms.counterparty_number, amount_paisa=sms.amount_paisa, method=method,
                purpose="other" if purpose == "other" or known is None else "baki", occurred_at=when,
                source="sms", source_ref=source_ref, trx_id=sms.trx_id, note=sms.reference,
                product_note=product_note)
            entries += booked.entries
            kind, outstanding, over = booked.kind, booked.customer_outstanding_paisa, booked.over_baki_paisa
            who = known or sms.counterparty_number
        elif sms.txn_type in ("cash_in", "cash_out"):
            kind, wallet_in = "transfer", sms.txn_type == "cash_in"
            note = f"Cash in to {method}" if wallet_in else f"Cash out from {method}"
            add(kind="transfer", direction="out", method="cash" if wallet_in else method, amount_paisa=sms.amount_paisa,
                category="Transfer", note=note, trx_id=None if wallet_in else sms.trx_id,
                counterparty_number=None if wallet_in else sms.counterparty_number)
            add(kind="transfer", direction="in", method=method if wallet_in else "cash", amount_paisa=sms.amount_paisa,
                category="Transfer", note=note, trx_id=sms.trx_id if wallet_in else None,
                counterparty_number=sms.counterparty_number if wallet_in else None)
            who = None
        else:  # payment_sent, send_money, bank_debit
            kind = "supplier_payment" if supplier else "other_out"
            add(kind=kind, direction="out", method=method, amount_paisa=sms.amount_paisa,
                category=(supplier.category or "Stock purchases") if supplier else "Payments",
                counterparty_name=supplier.name if supplier else sms.counterparty_name,
                counterparty_number=sms.counterparty_number, trx_id=sms.trx_id, note=sms.reference,
                product_note=product_note)
            if supplier:
                settled = self.apply_payment_to_payables(shop_id, supplier.name, sms.amount_paisa)
            who = supplier.name if supplier else (sms.counterparty_name or sms.counterparty_number)

        if sms.fee_paisa:
            add(kind="expense", direction="out", method=method, amount_paisa=sms.fee_paisa,
                category="Mobile money fees", note=f"Fee for {sms.trx_id}")
        return BookedSms(kind=kind, entries=entries, counterparty=who, customer_outstanding_paisa=outstanding,
                         settled_payables=settled, over_baki_paisa=over)

    # ---- money in from a customer: against their baki, or a sale

    def debtors_matching(self, shop_id: str, name: str) -> list[CustomerBalance]:
        """Customers who owe baki and whom `name` can mean: the exact name, else every one whose name has
        all of its words ("Babul" finds "Babul Mia"). More than one is for the caller to ask about."""
        wanted = _norm(name)
        owing = self.baki_by_customer(shop_id)
        exact = [b for b in owing if _norm(b.name) == wanted]
        if exact or not wanted:
            return exact
        words = wanted.split()
        return [b for b in owing if all(w in _norm(b.name).split() for w in words)]

    def book_customer_payment(self, shop_id: str, *, name: str | None, number: str | None, amount_paisa: int,
                              method: str, purpose: SmsPurpose, occurred_at: datetime, source: str,
                              source_ref: str | None, trx_id: str | None = None, note: str | None = None,
                              product_note: str | None = None) -> BookedSms:
        """Money a customer gave the shop. Against their baki unless `purpose` is "other": up to what they owe,
        and anything over that is a sale. Someone who owes nothing (or "other") is a sale. `name` must already be
        the customer's name as the books have it. A product note goes on the sale part when there is one, else
        on the repayment."""
        owes = None
        if purpose != "other" and name:
            owes = next((b for b in self.baki_by_customer(shop_id) if b.name == name), None)
        entries: list[LedgerEntryView] = []

        def add(**fields) -> None:
            entries.append(self.add_entry(NewLedgerEntry(
                shop_id=shop_id, occurred_at=occurred_at, source=source, source_ref=source_ref, direction="in",
                method=method, counterparty_name=name, counterparty_number=number, **fields)))

        repaid = min(amount_paisa, owes.outstanding_paisa) if owes else amount_paisa
        split = bool(owes) and amount_paisa > repaid
        add(kind="baki_repayment" if owes else "sale", amount_paisa=repaid,
            category="Baki collected" if owes else "Sales", trx_id=trx_id, note=note,
            product_note=None if split else product_note)
        over = 0
        if split:  # paid more than they owed: the rest is a sale
            over = amount_paisa - repaid
            add(kind="sale", amount_paisa=over, category="Sales", product_note=product_note,
                note=f"Rest of TrxID {trx_id}, over the baki owed" if trx_id else "The part over the baki owed")
        outstanding = None
        if owes:
            after = next((b for b in self.baki_by_customer(shop_id) if b.name == name), None)
            outstanding = after.outstanding_paisa if after else 0
        return BookedSms(kind="baki_repayment" if owes else "sale", entries=entries, counterparty=name,
                         customer_outstanding_paisa=outstanding, over_baki_paisa=over)

    def ask_about_typed(self, shop_id: str, *, debtor: CustomerBalance, amount_paisa: int, method: str,
                        request_id: str | None, conversation_id: str | None,
                        decision_id: str | None, product_note: str | None = None) -> SmsQuestionView:
        """Money the owner typed in from a customer who owes baki, held unbooked like a forwarded SMS."""
        row = SmsQuestion(id=new_id("smsq"), shop_id=shop_id, request_id=request_id, conversation_id=conversation_id,
                          decision_id=decision_id, trx_id="", sms_text="", customer_name=debtor.name,
                          owed_paisa=debtor.outstanding_paisa, amount_paisa=amount_paisa, created_at=self.clock.now(),
                          source="typed", method=method, product_note=product_note)
        self.session.add(row)
        self.session.flush()
        return SmsQuestionView.model_validate(row)

    # ---- money in from a customer who owes: held until the owner says what it is for

    def ask_about_sms(self, shop_id: str, *, sms_text: str, sms: ParsedSms, debtor: CustomerBalance,
                      request_id: str | None, conversation_id: str | None,
                      decision_id: str | None, product_note: str | None = None) -> SmsQuestionView:
        """Hold this SMS unbooked. Forwarding the same SMS again while it is waiting reuses the question."""
        open_one = self.session.scalar(select(SmsQuestion).where(
            SmsQuestion.shop_id == shop_id, SmsQuestion.trx_id == sms.trx_id, SmsQuestion.status == "open"))
        if open_one is None:
            open_one = SmsQuestion(id=new_id("smsq"), shop_id=shop_id, request_id=request_id,
                                   conversation_id=conversation_id, decision_id=decision_id,
                                   trx_id=sms.trx_id, sms_text=sms_text, customer_name=debtor.name,
                                   owed_paisa=debtor.outstanding_paisa, amount_paisa=sms.amount_paisa,
                                   created_at=self.clock.now(), product_note=product_note)
            self.session.add(open_one)
            self.session.flush()
        elif product_note:  # forwarded again, now with what it was for
            open_one.product_note = product_note
            self.session.flush()
        return SmsQuestionView.model_validate(open_one)

    def sms_question(self, shop_id: str, question_id: str) -> SmsQuestion | None:
        row = self.session.get(SmsQuestion, question_id)
        return row if row is not None and row.shop_id == shop_id else None

    def sms_questions(self, shop_id: str, limit: int = 50) -> list[SmsQuestionView]:
        query = (select(SmsQuestion).where(SmsQuestion.shop_id == shop_id)
                 .order_by(SmsQuestion.created_at.desc()).limit(limit))
        return [SmsQuestionView.model_validate(q) for q in self.session.scalars(query)]

    def mark_answered(self, row: SmsQuestion, purpose: SmsPurpose) -> None:
        row.status, row.answered_at = purpose, self.clock.now()
        self.session.flush()

    def has_trx(self, shop_id: str, trx_id: str) -> bool:
        query = select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.shop_id == shop_id, LedgerEntry.trx_id == trx_id
        )
        return self.session.scalar(query) > 0

    def entries_for_trx(self, shop_id: str, trx_id: str) -> list[LedgerEntryView]:
        """Everything one SMS booked: the entry with its TrxID, the part over the baki booked as a sale, and
        the fee (those two carry the TrxID in their note), oldest first."""
        query = select(LedgerEntry).where(LedgerEntry.shop_id == shop_id, or_(
            LedgerEntry.trx_id == trx_id,
            LedgerEntry.note.in_([f"Rest of TrxID {trx_id}, over the baki owed", f"Fee for {trx_id}"])))
        rows = self.session.scalars(query.order_by(LedgerEntry.created_at, LedgerEntry.id))
        return [LedgerEntryView.model_validate(r) for r in rows]

    def open_question_for_trx(self, shop_id: str, trx_id: str) -> SmsQuestion | None:
        return self.session.scalar(select(SmsQuestion).where(
            SmsQuestion.shop_id == shop_id, SmsQuestion.trx_id == trx_id, SmsQuestion.status == "open"))

    def add_obligation(self, obligation: NewObligation) -> ObligationView:
        row = Obligation(id=new_id("obl"), created_at=self.clock.now(), status="open", **obligation.model_dump())
        self.session.add(row)
        self.session.flush()
        return ObligationView.model_validate(row)

    # ---- bills the owner manages from chat

    def find_open_bill(self, shop_id: str, name: str, *, due_date: date | None = None) -> ObligationView | None:
        """The open bill the owner means by `name` ("Meghna" for "Meghna Distributors"): the same name first,
        then a name starting with it, then a name or note containing it as whole words. `due_date` picks
        between bills of one payee. None when nothing fits; AmbiguousBill when more than one still does."""
        wanted = _norm(name)
        if not wanted:
            return None
        rows = [o for o in self.open_obligations(shop_id) if due_date is None or o.due_date == due_date]
        for fits in (lambda o: _norm(o.counterparty_name) == wanted,
                     lambda o: _norm(o.counterparty_name).startswith(wanted),
                     lambda o: f" {wanted} " in f" {_norm(o.counterparty_name)} {_norm(o.note or '')} "):
            matches = [o for o in rows if fits(o)]
            if len(matches) == 1:
                return matches[0]
            if matches:
                raise AmbiguousBill(name, matches)
        return None

    def update_obligation(self, shop_id: str, obligation_id: str, *, amount_paisa: int | None = None,
                          due_date: date | None = None, priority: bool | None = None) -> ObligationView:
        row = self._open_obligation(shop_id, obligation_id)
        if amount_paisa is not None:
            row.amount_paisa = amount_paisa
        if due_date is not None:
            row.due_date = due_date
        if priority is not None:
            row.priority = priority
        self.session.flush()
        return ObligationView.model_validate(row)

    def reduce_obligation(self, shop_id: str, obligation_id: str, paid_paisa: int) -> ObligationView:
        """Part (or all) of a bill was paid: lower what is left, and settle it when nothing is."""
        row = self._open_obligation(shop_id, obligation_id)
        row.amount_paisa = max(0, row.amount_paisa - paid_paisa)
        if row.amount_paisa == 0:
            row.status, row.settled_at = "settled", self.clock.now()
        self.session.flush()
        return ObligationView.model_validate(row)

    def cancel_obligation(self, shop_id: str, obligation_id: str) -> ObligationView:
        """The owner removed the bill. Kept as cancelled (not deleted) so the audit trail still makes sense."""
        row = self._open_obligation(shop_id, obligation_id)
        row.status, row.settled_at = "cancelled", self.clock.now()
        self.session.flush()
        return ObligationView.model_validate(row)

    def _open_obligation(self, shop_id: str, obligation_id: str) -> Obligation:
        row = self.session.get(Obligation, obligation_id)
        if row is None or row.shop_id != shop_id or row.status != "open":
            raise LookupError(f"no open bill {obligation_id}")
        return row

    def add_recurring_bill(self, shop_id: str, name: str, payee: str, category: str, amount_paisa: int,
                           day_of_month: int, method: str) -> RecurringBillView:
        row = RecurringBill(id=new_id("rbill"), shop_id=shop_id, name=name, payee=payee, category=category,
                            amount_paisa=amount_paisa, day_of_month=day_of_month, method=method,
                            created_at=self.clock.now())
        self.session.add(row)
        self.session.flush()
        return RecurringBillView.model_validate(row)

    # ---- reading

    def entries(self, shop_id: str, *, day: date | None = None, limit: int = 100, offset: int = 0) -> list[LedgerEntryView]:
        query = select(LedgerEntry).where(LedgerEntry.shop_id == shop_id)
        if day is not None:
            query = query.where(LedgerEntry.local_date == day)
        query = query.order_by(LedgerEntry.occurred_at.desc(), LedgerEntry.id.desc()).limit(limit).offset(offset)
        return [LedgerEntryView.model_validate(r) for r in self.session.scalars(query)]

    def count(self, shop_id: str, *, day: date | None = None) -> int:
        query = select(func.count()).select_from(LedgerEntry).where(LedgerEntry.shop_id == shop_id)
        if day is not None:
            query = query.where(LedgerEntry.local_date == day)
        return self.session.scalar(query)

    def entry_days(self, shop_id: str, day: date | None = None) -> tuple[date | None, date | None, date | None]:
        """(day, newer, older): the day itself and its nearest neighbours that have entries.

        With no `day`, the latest day with entries up to today, or failing that the earliest after it.
        """
        in_shop = LedgerEntry.shop_id == shop_id
        if day is None:
            today = local_date(self.clock.now())
            day = self.session.scalar(select(func.max(LedgerEntry.local_date)).where(in_shop, LedgerEntry.local_date <= today))
            if day is None:
                day = self.session.scalar(select(func.min(LedgerEntry.local_date)).where(in_shop))
            if day is None:
                return None, None, None
        newer = self.session.scalar(select(func.min(LedgerEntry.local_date)).where(in_shop, LedgerEntry.local_date > day))
        older = self.session.scalar(select(func.max(LedgerEntry.local_date)).where(in_shop, LedgerEntry.local_date < day))
        return day, newer, older

    def day_totals(self, shop_id: str, day: date) -> DayTotals:
        rows = self.session.scalars(
            select(LedgerEntry).where(LedgerEntry.shop_id == shop_id, LedgerEntry.local_date == day)
        ).all()
        sales = sum(r.amount_paisa for r in rows if r.kind == "sale")
        baki_given = sum(r.amount_paisa for r in rows if r.kind == "baki_sale")
        collected = sum(r.amount_paisa for r in rows if r.kind == "baki_repayment")
        cash_rows = [r for r in rows if r.method != "credit" and r.kind not in CASHFLOW_EXCLUDED_KINDS]
        money_in = sum(r.amount_paisa for r in cash_rows if r.direction == "in")
        money_out = sum(r.amount_paisa for r in cash_rows if r.direction == "out")
        return DayTotals(
            day=day,
            sales_paisa=sales,
            baki_given_paisa=baki_given,
            baki_collected_paisa=collected,
            money_in_paisa=money_in,
            money_out_paisa=money_out,
            net_paisa=money_in - money_out,
            entries=len(rows),
        )

    def balances(self, shop_id: str) -> dict[str, int]:
        """Money on hand by method: every in minus every out, including transfers and openings."""
        query = (
            select(LedgerEntry.method, LedgerEntry.direction, func.sum(LedgerEntry.amount_paisa))
            .where(LedgerEntry.shop_id == shop_id, LedgerEntry.method != "credit")
            .group_by(LedgerEntry.method, LedgerEntry.direction)
        )
        balances = dict.fromkeys(LIQUID_METHODS, 0)
        for method, direction, total in self.session.execute(query):
            balances[method] += total if direction == "in" else -total
        return balances

    def baki_by_customer(self, shop_id: str, today: date | None = None) -> list[CustomerBalance]:
        """Outstanding customer credit, oldest unpaid purchase first-in-first-out. A baki sale's
        `due_date` is the day the customer promised to pay it back; the earliest promise on what is
        still open becomes `expected_by`."""
        today = today or local_date(self.clock.now())
        rows = self.session.execute(
            select(LedgerEntry.counterparty_name, LedgerEntry.counterparty_number, LedgerEntry.kind,
                   LedgerEntry.direction, LedgerEntry.amount_paisa, LedgerEntry.local_date, LedgerEntry.due_date)
            .where(LedgerEntry.shop_id == shop_id, LedgerEntry.kind.in_(BAKI_KINDS))
            .order_by(LedgerEntry.occurred_at)
        ).all()
        queues: dict[str, deque[list]] = defaultdict(deque)
        numbers: dict[str, str | None] = {}
        for name, number, kind, direction, amount, day, due in rows:
            key = name or number or "Unknown"
            numbers[key] = numbers.get(key) or number  # the first number any of their entries carries
            queue = queues[key]
            if raises_baki(kind, direction):
                queue.append([day, amount, due])
                continue
            # A repayment, or the owner forgiving or correcting baki down: the oldest open baki goes first.
            while amount > 0 and queue:
                take = min(amount, queue[0][1])
                queue[0][1] -= take
                amount -= take
                if queue[0][1] == 0:
                    queue.popleft()
        balances = []
        for name, queue in queues.items():
            outstanding = sum(item[1] for item in queue)
            if outstanding <= 0:
                continue
            oldest = queue[0][0]
            promises = [item[2] for item in queue if item[2] is not None]
            balances.append(CustomerBalance(
                name=name, number=numbers.get(name), outstanding_paisa=outstanding,
                oldest_open_since=oldest, days_outstanding=(today - oldest).days,
                expected_by=min(promises) if promises else None,
            ))
        return sorted(balances, key=lambda b: -b.outstanding_paisa)

    def apply_payment_to_payables(self, shop_id: str, supplier_name: str, amount_paisa: int) -> list[str]:
        """Reduce a supplier's open payables, earliest due first. Returns the ids fully settled."""
        rows = self.session.scalars(
            select(Obligation)
            .where(Obligation.shop_id == shop_id, Obligation.kind == "supplier_payable",
                   Obligation.status == "open", Obligation.counterparty_name == supplier_name)
            .order_by(Obligation.due_date)
        ).all()
        settled = []
        for row in rows:
            if amount_paisa <= 0:
                break
            take = min(amount_paisa, row.amount_paisa)
            row.amount_paisa -= take
            amount_paisa -= take
            if row.amount_paisa == 0:
                row.status, row.settled_at = "settled", self.clock.now()
                settled.append(row.id)
        self.session.flush()
        return settled

    def payments_to(self, shop_id: str, number: str) -> list[tuple[date, int]]:
        """Every past payment out to this counterparty number: (day, amount)."""
        rows = self.session.execute(
            select(LedgerEntry.local_date, LedgerEntry.amount_paisa)
            .where(LedgerEntry.shop_id == shop_id, LedgerEntry.counterparty_number == number,
                   LedgerEntry.direction == "out", LedgerEntry.method != "credit")
            .order_by(LedgerEntry.occurred_at)
        ).all()
        return [(day, amount) for day, amount in rows]

    def purchases_from(self, shop_id: str, name: str, *, since: date, limit: int = 20) -> list[int]:
        """The latest stock purchases booked from this supplier since `since`, newest first (amounts)."""
        rows = self.session.scalars(
            select(LedgerEntry.amount_paisa)
            .where(LedgerEntry.shop_id == shop_id, LedgerEntry.kind == "purchase", LedgerEntry.local_date >= since,
                   func.lower(LedgerEntry.counterparty_name) == name.lower())
            .order_by(LedgerEntry.occurred_at.desc()).limit(limit)
        ).all()
        return [int(a) for a in rows]

    def repaid_since(self, shop_id: str, *, name: str, number: str | None, since: datetime) -> int:
        """Baki this customer paid back from `since` on, matched by name or by number."""
        who = func.lower(LedgerEntry.counterparty_name) == name.lower()
        if number:
            who = or_(who, LedgerEntry.counterparty_number == number)
        return int(self.session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount_paisa), 0)).where(
                LedgerEntry.shop_id == shop_id, LedgerEntry.kind == "baki_repayment", LedgerEntry.occurred_at >= since,
                who)
        ) or 0)

    def average_daily_sales(self, shop_id: str, today: date, days: int = 28) -> int:
        """Mean daily sales (cash, mobile and baki) over the last `days` full days."""
        start = today - timedelta(days=days)
        total = self.session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount_paisa), 0)).where(
                LedgerEntry.shop_id == shop_id, LedgerEntry.kind.in_(("sale", "baki_sale")),
                LedgerEntry.local_date >= start, LedgerEntry.local_date < today)
        )
        return int(total) // days

    def daily_flows(self, shop_id: str, start: date, end: date,
                    bill_categories: set[str] = frozenset()) -> dict[date, DailyFlow]:
        """Per-day money movements from `start` to `end` inclusive (days with nothing are present, empty)."""
        rows = self.session.execute(
            select(LedgerEntry.local_date, LedgerEntry.kind, LedgerEntry.method, LedgerEntry.category,
                   LedgerEntry.amount_paisa)
            .where(LedgerEntry.shop_id == shop_id, LedgerEntry.local_date >= start, LedgerEntry.local_date <= end)
        ).all()
        flows = {start + timedelta(days=i): DailyFlow() for i in range((end - start).days + 1)}
        for day, kind, method, category, amount in rows:
            flow = flows[day]
            if kind == "sale" and method != "credit":
                flow.sales_in += amount
            elif kind == "baki_sale":
                flow.baki_given += amount
            elif kind == "baki_repayment":
                flow.baki_collected += amount
            elif kind == "other_in":
                flow.other_in += amount
            elif kind == "supplier_payment":
                flow.supplier_payments += amount
            elif kind == "expense" and category in bill_categories:
                flow.bills_paid += amount
            elif kind == "owner_drawing" and category == SAVINGS_CATEGORY:
                flow.savings += amount
            elif kind == "owner_drawing":
                flow.owner_drawings += amount
            elif (kind == "purchase" and method != "credit") or kind in ("expense", "other_out"):
                flow.routine_out += amount
        return flows

    def book_days(self, shop_id: str, start: date, end: date) -> list[BookDay]:
        """Every day from `start` to `end` with totals and end-of-day positions (debts, payables, cash),
        the same columns the synthetic shops' daily.csv has, so the credit model sees one format."""
        rows = self.session.execute(
            select(LedgerEntry.local_date, LedgerEntry.kind, LedgerEntry.direction, LedgerEntry.method,
                   LedgerEntry.category, LedgerEntry.amount_paisa, LedgerEntry.days_late)
            .where(LedgerEntry.shop_id == shop_id, LedgerEntry.local_date <= end)
            .order_by(LedgerEntry.local_date)
        ).all()
        days = {start + timedelta(days=i): BookDay(day=start + timedelta(days=i)) for i in range((end - start).days + 1)}
        baki = payables = balance = 0
        before = BookDay(day=start)  # totals before the window are not reported, only the positions they leave
        touched: set[date] = set()
        for day, kind, direction, method, category, amount, days_late in rows:
            book = days.get(day, before)
            if method != "credit":
                balance += amount if direction == "in" else -amount
            if kind == "sale" and method == "cash":
                book.sales_cash += amount
            elif kind == "sale" and method != "credit":
                book.sales_mobile += amount
            elif kind == "baki_sale":
                book.sales_baki += amount
                baki += amount
            elif kind == "baki_repayment":
                book.baki_collected += amount
                baki -= amount
            elif kind == "baki_adjustment":  # forgiven or corrected by the owner: no sale, no money
                baki += amount if direction == "in" else -amount
            elif kind == "purchase":
                book.purchases += amount
                if method == "credit":
                    payables += amount
                else:
                    book.supplier_paid += amount
                    book.supplier_payments_on_time += 1
            elif kind == "supplier_payment":
                book.supplier_paid += amount
                payables -= amount
                if days_late:
                    book.supplier_payments_late += 1
                else:
                    book.supplier_payments_on_time += 1
            elif kind == "expense":
                book.expenses += amount
            elif kind == "owner_drawing":
                book.drawings += amount
            elif kind == "other_in" and category == "Informal loan":
                book.informal_borrowed += amount
            elif kind == "other_out" and category == "Informal loan":
                book.informal_repaid += amount
            if day < start:
                before.baki_outstanding, before.payables_outstanding, before.closing_balance = baki, payables, balance
            else:
                book.baki_outstanding, book.payables_outstanding, book.closing_balance = baki, payables, balance
                touched.add(day)
        carry = before
        for book in days.values():  # a day with no entries keeps the previous day's positions
            if book.day not in touched:
                book.baki_outstanding, book.payables_outstanding = carry.baki_outstanding, carry.payables_outstanding
                book.closing_balance = carry.closing_balance
            carry = book
        return list(days.values())

    def category_totals(self, shop_id: str, start: date, end: date) -> list[tuple[str, str, str, int]]:
        """(kind, method, category, total paisa) for entries from `start` to `end` inclusive."""
        rows = self.session.execute(
            select(LedgerEntry.kind, LedgerEntry.method, LedgerEntry.category,
                   func.coalesce(func.sum(LedgerEntry.amount_paisa), 0))
            .where(LedgerEntry.shop_id == shop_id, LedgerEntry.local_date >= start, LedgerEntry.local_date <= end)
            .group_by(LedgerEntry.kind, LedgerEntry.method, LedgerEntry.category)).all()
        return [(kind, method, category or "", int(total)) for kind, method, category, total in rows]

    def categories_paid(self, shop_id: str, start: date, end: date) -> set[str]:
        """Expense categories with at least one payment between `start` and `end` (to avoid double-counting bills)."""
        rows = self.session.scalars(
            select(LedgerEntry.category).where(
                LedgerEntry.shop_id == shop_id, LedgerEntry.kind == "expense",
                LedgerEntry.local_date >= start, LedgerEntry.local_date <= end).distinct())
        return set(rows)

    def repayment_profiles(self, shop_id: str, today: date) -> list[RepaymentProfile]:
        """How each credit customer actually repays: speed (amount-weighted days) and reliability."""
        rows = self.session.execute(
            select(LedgerEntry.counterparty_name, LedgerEntry.counterparty_number, LedgerEntry.kind,
                   LedgerEntry.direction, LedgerEntry.amount_paisa, LedgerEntry.local_date)
            .where(LedgerEntry.shop_id == shop_id, LedgerEntry.kind.in_(BAKI_KINDS))
            .order_by(LedgerEntry.occurred_at)
        ).all()
        matured_before = today - timedelta(days=45)
        state: dict[str, dict] = {}
        for name, number, kind, direction, amount, day in rows:
            key = name or number or "Unknown"
            s = state.setdefault(key, {"number": number, "queue": deque(), "given": 0, "repaid": 0,
                                       "weighted_days": 0.0, "matured_given": 0, "matured_repaid": 0})
            if raises_baki(kind, direction):
                s["queue"].append([day, amount, amount])  # date, remaining, original
                s["given"] += amount
                if day < matured_before:
                    s["matured_given"] += amount
                continue
            if kind == "baki_adjustment":  # forgiven, not repaid: it leaves the queue but counts as never paid
                while amount > 0 and s["queue"]:
                    take = min(amount, s["queue"][0][1])
                    s["queue"][0][1] -= take
                    amount -= take
                    if s["queue"][0][1] == 0:
                        s["queue"].popleft()
                continue
            s["repaid"] += amount
            while amount > 0 and s["queue"]:
                item = s["queue"][0]
                take = min(amount, item[1])
                item[1] -= take
                amount -= take
                s["weighted_days"] += take * (day - item[0]).days
                if item[0] < matured_before:
                    s["matured_repaid"] += take
                if item[1] == 0:
                    s["queue"].popleft()
        profiles = []
        for name, s in state.items():
            outstanding = sum(item[1] for item in s["queue"])
            profiles.append(RepaymentProfile(
                name=name, number=s["number"], given_paisa=s["given"], repaid_paisa=s["repaid"],
                outstanding_paisa=outstanding,
                oldest_open_since=s["queue"][0][0] if s["queue"] else None,
                average_days_to_repay=round(s["weighted_days"] / s["repaid"], 1) if s["repaid"] else None,
                repaid_share=round(s["matured_repaid"] / s["matured_given"], 3) if s["matured_given"] else 0.9,
            ))
        return sorted(profiles, key=lambda p: -p.outstanding_paisa)

    def supplier_credit_cycles(self, shop_id: str, today: date, lookback_days: int = 84) -> list[SupplierCycle]:
        """Suppliers who deliver on credit on a regular cycle: how often, how much, how long to pay."""
        rows = self.session.execute(
            select(LedgerEntry.counterparty_name, LedgerEntry.counterparty_number, LedgerEntry.local_date,
                   LedgerEntry.amount_paisa, LedgerEntry.due_date)
            .where(LedgerEntry.shop_id == shop_id, LedgerEntry.kind == "purchase", LedgerEntry.method == "credit",
                   LedgerEntry.local_date >= today - timedelta(days=lookback_days),
                   LedgerEntry.counterparty_name.is_not(None))
            .order_by(LedgerEntry.local_date)
        ).all()
        by_supplier: dict[str, list] = defaultdict(list)
        for name, number, day, amount, due in rows:
            by_supplier[name].append((number, day, amount, due))
        methods = {p.name: p.method for p in ShopService(self.session, self.clock).parties(shop_id, "supplier")}
        cycles = []
        for name, deliveries in by_supplier.items():
            days = sorted({d for _, d, _, _ in deliveries})
            if len(days) < 3:
                continue
            gaps = [(b - a).days for a, b in zip(days, days[1:], strict=False)]
            terms = [(due - d).days for _, d, _, due in deliveries if due]
            if not terms or statistics.median(terms) < 3:  # waiting a day or two for cash is not a credit line
                continue
            cycles.append(SupplierCycle(
                name=name, number=deliveries[-1][0], method=methods.get(name),
                cycle_days=max(1, int(round(statistics.median(gaps)))),
                usual_paisa=int(statistics.median([a for _, _, a, _ in deliveries[-4:]])),
                terms_days=int(round(statistics.median(terms))),
                last_delivery=days[-1],
            ))
        return cycles

    def open_obligations(self, shop_id: str) -> list[ObligationView]:
        query = (
            select(Obligation)
            .where(Obligation.shop_id == shop_id, Obligation.status == "open")
            .order_by(Obligation.due_date)
        )
        return [ObligationView.model_validate(o) for o in self.session.scalars(query)]

    def recurring_bills(self, shop_id: str) -> list[RecurringBillView]:
        query = select(RecurringBill).where(RecurringBill.shop_id == shop_id).order_by(RecurringBill.day_of_month)
        return [RecurringBillView.model_validate(b) for b in self.session.scalars(query)]
