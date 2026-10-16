"""Public API of the ledger module."""

from __future__ import annotations

import statistics
from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import date, datetime, timedelta

from sqlalchemy import func, insert, or_, select
from sqlalchemy.orm import Session

from app.core.clock import Clock, local_date
from app.core.ids import new_id
from app.modules.ledger.models import LedgerEntry, Obligation, RecurringBill
from app.core.sms import ParsedSms
from app.modules.ledger.categories import categorize  # noqa: F401  (re-exported: part of the public API)
from app.modules.ledger.schemas import (
    CASHFLOW_EXCLUDED_KINDS,
    LIQUID_METHODS,
    BookDay,
    BookedSms,
    CustomerBalance,
    DailyFlow,
    DayTotals,
    DuplicateSms,
    RepaymentProfile,
    SupplierCycle,
    LedgerEntryView,
    NewLedgerEntry,
    NewObligation,
    ObligationView,
    RecurringBillView,
)
from app.modules.shop.service import ShopService


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

    def book_sms(self, shop_id: str, sms: ParsedSms, *, at: datetime, source_ref: str | None) -> BookedSms:
        """Book a parsed notification SMS. Deterministic: money from a customer who owes baki is a
        baki repayment, otherwise a sale; money to a known supplier settles their payable; cash in and
        cash out are transfers between the drawer and the wallet; fees are an expense."""
        if self.has_trx(shop_id, sms.trx_id):
            raise DuplicateSms(sms.trx_id)
        shops = ShopService(self.session, self.clock)
        when = sms.occurred_at or at
        method = sms.provider
        entries: list[LedgerEntryView] = []

        def add(**fields) -> None:
            entries.append(self.add_entry(NewLedgerEntry(shop_id=shop_id, occurred_at=when, source="sms",
                                                         source_ref=source_ref, **fields)))

        customer = shops.find_party_by_number(shop_id, sms.counterparty_number, kind="customer")
        supplier = shops.find_party_by_number(shop_id, sms.counterparty_number, kind="supplier") or (
            shops.find_party(shop_id, sms.counterparty_name, kind="supplier") if sms.counterparty_name else None)
        outstanding, settled = None, []

        if sms.direction == "in" and sms.txn_type != "cash_in":
            owes = customer and next((b for b in self.baki_by_customer(shop_id) if b.name == customer.name), None)
            kind = "baki_repayment" if owes else "sale"
            add(kind=kind, direction="in", method=method, amount_paisa=sms.amount_paisa,
                category="Baki collected" if owes else "Sales",
                counterparty_name=customer.name if customer else sms.counterparty_name,
                counterparty_number=sms.counterparty_number, trx_id=sms.trx_id, note=sms.reference)
            if owes:
                after = next((b for b in self.baki_by_customer(shop_id) if b.name == customer.name), None)
                outstanding = after.outstanding_paisa if after else 0
            who = customer.name if customer else sms.counterparty_number
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
                counterparty_number=sms.counterparty_number, trx_id=sms.trx_id, note=sms.reference)
            if supplier:
                settled = self.apply_payment_to_payables(shop_id, supplier.name, sms.amount_paisa)
            who = supplier.name if supplier else (sms.counterparty_name or sms.counterparty_number)

        if sms.fee_paisa:
            add(kind="expense", direction="out", method=method, amount_paisa=sms.fee_paisa,
                category="Mobile money fees", note=f"Fee for {sms.trx_id}")
        return BookedSms(kind=kind, entries=entries, counterparty=who, customer_outstanding_paisa=outstanding,
                         settled_payables=settled)

    def has_trx(self, shop_id: str, trx_id: str) -> bool:
        query = select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.shop_id == shop_id, LedgerEntry.trx_id == trx_id
        )
        return self.session.scalar(query) > 0

    def add_obligation(self, obligation: NewObligation) -> ObligationView:
        row = Obligation(id=new_id("obl"), created_at=self.clock.now(), status="open", **obligation.model_dump())
        self.session.add(row)
        self.session.flush()
        return ObligationView.model_validate(row)

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

    def count(self, shop_id: str) -> int:
        return self.session.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.shop_id == shop_id))

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
        """Outstanding customer credit, oldest unpaid purchase first-in-first-out."""
        today = today or local_date(self.clock.now())
        rows = self.session.execute(
            select(LedgerEntry.counterparty_name, LedgerEntry.counterparty_number, LedgerEntry.kind,
                   LedgerEntry.amount_paisa, LedgerEntry.local_date)
            .where(LedgerEntry.shop_id == shop_id, LedgerEntry.kind.in_(("baki_sale", "baki_repayment")))
            .order_by(LedgerEntry.occurred_at)
        ).all()
        queues: dict[str, deque[list]] = defaultdict(deque)
        numbers: dict[str, str | None] = {}
        for name, number, kind, amount, day in rows:
            key = name or number or "Unknown"
            numbers.setdefault(key, number)
            queue = queues[key]
            if kind == "baki_sale":
                queue.append([day, amount])
                continue
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
            balances.append(CustomerBalance(
                name=name, number=numbers.get(name), outstanding_paisa=outstanding,
                oldest_open_since=oldest, days_outstanding=(today - oldest).days,
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
                   LedgerEntry.amount_paisa, LedgerEntry.local_date)
            .where(LedgerEntry.shop_id == shop_id, LedgerEntry.kind.in_(("baki_sale", "baki_repayment")))
            .order_by(LedgerEntry.occurred_at)
        ).all()
        matured_before = today - timedelta(days=45)
        state: dict[str, dict] = {}
        for name, number, kind, amount, day in rows:
            key = name or number or "Unknown"
            s = state.setdefault(key, {"number": number, "queue": deque(), "given": 0, "repaid": 0,
                                       "weighted_days": 0.0, "matured_given": 0, "matured_repaid": 0})
            if kind == "baki_sale":
                s["queue"].append([day, amount, amount])  # date, remaining, original
                s["given"] += amount
                if day < matured_before:
                    s["matured_given"] += amount
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
