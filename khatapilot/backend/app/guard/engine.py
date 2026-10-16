"""The policy engine: gathers the facts, runs every check and decides the tier and outcome.

Nothing here comes from the LLM except the proposal itself. Who is speaking comes from the channel
adapter; limits, the allowlist, the kill switch and PIN state come from the guard's own store;
the fraud score comes from the risk module; payment intents come from the owner's own words.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.actions import ActionKind, PaymentRequest, ProposedAction
from app.core.clock import Clock, local_date
from app.core.decisions import CheckStatus, Decision, Outcome
from app.core.ids import new_id
from app.core.masking import mask_number, normalize_msisdn
from app.core.money import fmt_taka
from app.core.provenance import TurnContext
from app.guard import checks as C
from app.guard.injection import CLEAN, scan, scan_owner
from app.guard.intent import IntentMatch, IntentStore, extract_payment_intents, match_payment
from app.guard.pin import PinManager
from app.guard.policy_store import PolicyStore, normalize_number
from app.guard.tiers import decide
from app.modules.payments.service import PaymentsService
from app.modules.risk.service import RiskService
from app.modules.shop.service import ShopService

ALERT_CHECKS = {"provenance", "hidden_instructions", "payee", "fraud"}


class PolicyEngine:
    def __init__(self, *, clock: Clock, shop_id: str, store: PolicyStore, pins: PinManager, intents: IntentStore,
                 risk: RiskService, payments: PaymentsService, shops: ShopService) -> None:
        self.clock = clock
        self.shop_id = shop_id
        self.store = store
        self.pins = pins
        self.intents = intents
        self.risk = risk
        self.payments = payments
        self.shops = shops

    def known_names(self) -> list[str]:
        names = []
        for payee in self.store.payees():
            names += [payee.name, *payee.aliases]
        names += [party.name for party in self.shops.parties(self.shop_id)]
        return names

    def resolve(self, action: ProposedAction) -> ProposedAction:
        """Fill a trusted payee's saved number and method when the proposal names them without one."""
        p = action.payment
        if p is None:
            return action
        number = normalize_number(p.payee_number) if p.payee_number else None
        trusted = self.store.payee_by_name(p.payee_name) if p.payee_name else None
        party_method = None
        if number is None and trusted is not None:
            number = trusted.number
        elif number is None and p.payee_name:
            # A supplier or customer the shop knows but has not trusted: use the number in its own books.
            # It stays a new payee for the payee check, so it still needs the owner's PIN.
            party = self.shops.find_party(self.shop_id, p.payee_name)
            if party and party.number and normalize_msisdn(party.number):
                number = normalize_msisdn(party.number)
                party_method = party.method if party.method in ("bkash", "nagad") else None
        saved = self.store.payee_by_number(number) if number else None
        method = p.method or (saved.method if saved and saved.method in ("bkash", "nagad", "bank") else None) \
            or party_method or "bkash"
        resolved = p.model_copy(update={"payee_number": number, "method": method})
        who = resolved.payee_name or "payee"
        summary = (f"Pay {who} ({mask_number(number) if number else 'no number'}) {fmt_taka(resolved.amount_paisa)}"
                   + (f" on {resolved.execute_on:%a %d %b}" if resolved.execute_on else " now") + f" by {resolved.method}")
        return action.model_copy(update={"payment": resolved, "summary": summary})

    def evaluate(self, action: ProposedAction, turn: TurnContext) -> Decision:
        now = self.clock.now()
        today = local_date(now)
        action = self.resolve(action)
        p: PaymentRequest | None = action.payment
        if p is not None and p.execute_on == today:
            p = p.model_copy(update={"execute_on": None})
            action = action.model_copy(update={"payment": p})

        if turn.is_owner_turn:
            self.intents.record_turn(turn, extract_payment_intents(turn.owner_text, today=today,
                                                                    known_names=self.known_names()))
        active = self.intents.active(turn.conversation_id) if turn.is_owner_turn else []
        match: IntentMatch | None = None
        if action.kind is ActionKind.PAYMENT and p is not None:
            match = match_payment(p, [intent for _, intent in active], today=today,
                                  payee_by_name=self.store.payee_by_name)

        trusted_by_number = self.store.payee_by_number(p.payee_number) if p else None
        trusted_by_name = self.store.payee_by_name(p.payee_name) if p and p.payee_name else None
        limits = self.store.limits()
        recent_same, recent_new, last_hour, committed, risk = (), (), 0, 0, None
        if action.kind is ActionKind.PAYMENT and p is not None:
            window_start = now - timedelta(hours=limits.split_window_hours)
            recent = self.payments.recent(window_start)
            trusted_numbers = {payee.number for payee in self.store.payees()}
            recent_same = tuple(r.amount_paisa for r in recent if p.payee_number and r.payee_number == p.payee_number)
            recent_new = tuple(r.amount_paisa for r in recent if r.payee_number not in trusted_numbers)
            last_hour = len(self.payments.recent(now - timedelta(hours=1)))
            committed = self.payments.committed_for_day(today)
            new_payees = {r.payee_number for r in recent if r.payee_number not in trusted_numbers
                          and r.created_at >= now - timedelta(days=1)}
            features = self.risk.payment_features(p.payee_number, p.amount_paisa,
                                                  payee_trusted=trusted_by_number is not None,
                                                  new_payees_last_day=len(new_payees), now=now)
            risk = self.risk.score_payment(features)

        pin_locked, locked_until = self.pins.lock_state()
        ctx = C.CheckContext(
            turn=turn,
            action=action,
            today=today,
            limits=limits,
            kill_switch=self.store.kill_switch(),
            pin_locked_until=locked_until if pin_locked else None,
            untrusted_scan=scan(turn.untrusted_text) if turn.untrusted_text else CLEAN,
            owner_scan=scan_owner(turn.owner_text) if turn.owner_text else CLEAN,
            intent=match,
            trusted_by_number=trusted_by_number,
            trusted_by_name=trusted_by_name,
            blocked_reason=self.store.blocked_reason(p.payee_number) if p else None,
            committed_today_paisa=committed,
            recent_same_payee_paisa=recent_same,
            recent_new_payees_paisa=recent_new,
            payments_last_hour=last_hour,
            risk=risk,
        )
        results = tuple(check(ctx) for check in C.PIPELINE)
        tier, outcome = decide(action.kind, results)
        decision = Decision(
            decision_id=new_id("dec"),
            request_id=turn.request_id,
            action=action,
            tier=tier,
            outcome=outcome,
            checks=results,
            reason=self._reason(outcome, tier, results),
            alert_owner=outcome is Outcome.BLOCK and (
                not turn.is_owner_turn
                or any(c.status is CheckStatus.BLOCK and c.name in ALERT_CHECKS for c in results)
            ),
            created_at=now,
        )
        if match and match.matched and outcome in (Outcome.ALLOW, Outcome.HOLD_PIN):
            record_id = active[match.index][0]
            self.intents.consume(record_id, decision.decision_id)
        return decision

    @staticmethod
    def _reason(outcome: Outcome, tier: int | None, results) -> str:
        if outcome is Outcome.BLOCK:
            first = next(c for c in results if c.status is CheckStatus.BLOCK)
            return f"Refused: {first.detail}"
        if outcome is Outcome.HOLD_PIN:
            why = " ".join(c.detail for c in results if c.status is CheckStatus.ESCALATE)
            return f"Needs your confirmation and PIN. {why}"
        if outcome is Outcome.HOLD_APPROVAL:
            return "Drafted for you: nothing is sent until you tap Approve."
        if tier == 2:
            return "Trusted payee, within your limits and exactly what you asked: KhataPilot can do this alone and will tell you."
        return "Read-only or your own books: nothing leaves the shop."
