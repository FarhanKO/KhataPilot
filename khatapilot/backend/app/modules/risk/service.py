"""Public API of the risk module.

Every proposed payment gets a fraud score from Fraud Sentinel (mobile-money edition, see
`sentinel.py`) when a trained model is loaded, otherwise from a small transparent rule set. The
guard turns the score into pass / escalate / block with its own thresholds.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.clock import Clock, local_date, to_local
from app.modules.ledger.service import LedgerService
from app.modules.payments.service import PaymentsService
from app.modules.risk.schemas import PaymentFeatures, RiskScore
from app.modules.risk.sentinel import FraudSentinel, ModelIntegrityError  # noqa: F401  (public API)

log = logging.getLogger(__name__)


class RiskService:
    def __init__(self, session: Session, clock: Clock, shop_id: str, model: FraudSentinel | None = None) -> None:
        self.clock = clock
        self.shop_id = shop_id
        self.model = model
        self.ledger = LedgerService(session, clock)
        self.payments = PaymentsService(session, clock, shop_id)

    def payment_features(self, payee_number: str | None, amount_paisa: int, *, payee_trusted: bool,
                         new_payees_last_day: int = 0, now: datetime | None = None) -> PaymentFeatures:
        now = now or self.clock.now()
        history = [amount for _, amount in self.ledger.payments_to(self.shop_id, payee_number)] if payee_number else []
        return PaymentFeatures(
            amount_paisa=amount_paisa,
            payee_trusted=payee_trusted,
            payee_history_count=len(history),
            payee_median_paisa=int(statistics.median(history)) if history else None,
            average_daily_sales_paisa=self.ledger.average_daily_sales(self.shop_id, local_date(now)),
            local_hour=to_local(now).hour,
            payments_last_hour=len(self.payments.recent(now - timedelta(hours=1))),
            payments_last_day=len(self.payments.recent(now - timedelta(days=1))),
            new_payees_last_day=new_payees_last_day,
        )

    def score_payment(self, features: PaymentFeatures) -> RiskScore:
        if self.model is not None:
            try:
                return self.model.assess(features)
            except Exception:  # a broken model must never make the guard fail open or crash
                log.exception("Fraud Sentinel failed; using the rule-based score")
        return score_rules(features)


def score_rules(f: PaymentFeatures) -> RiskScore:
    score, reasons = 0.05, []
    if f.payee_history_count == 0:
        score += 0.25
        reasons.append("the shop has never paid this number before")
    elif f.payee_median_paisa and f.amount_paisa > 3 * f.payee_median_paisa:
        score += 0.25
        reasons.append("more than 3x the usual payment to this payee")
    if f.average_daily_sales_paisa and f.amount_paisa > 2 * f.average_daily_sales_paisa:
        score += 0.15
        reasons.append("more than two days of sales")
    if f.local_hour < 6:
        score += 0.15
        reasons.append("at night, when the shop is closed")
    if f.payments_last_hour >= 3:
        score += 0.10 * (f.payments_last_hour - 2)
        reasons.append(f"{f.payments_last_hour} payments in the last hour")
    if not f.payee_trusted:
        score += 0.10
        reasons.append("payee is not on the trusted list")
    return RiskScore(score=round(min(score, 1.0), 3), reasons=reasons or ["nothing unusual"], model="rules-v1")
