"""Public API of the loans module: options matched to the shop, the pre-filled application, and
submitting it to the (simulated) lender, which needs the guard's authorization."""

from __future__ import annotations

import json
import math
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.actions import AuthorizedAction, canonical_json, sha256_hex
from app.core.clock import Clock, local_date
from app.core.ids import new_id
from app.core.money import fmt_taka
from app.infra.sandbox_client import SandboxClient, SandboxError
from app.modules.credit.schemas import CreditProfile
from app.modules.credit.service import CreditService
from app.modules.forecast.schemas import CashForecast
from app.modules.forecast.service import PRE_EID_FACTOR, ForecastService
from app.modules.ledger.service import LedgerService
from app.modules.loans import application, matcher
from app.modules.loans.models import LoanApplicationRecord
from app.modules.loans.products import BY_ID, PRODUCTS
from app.modules.loans.schemas import (
    Affordability,
    ApplicationDraft,
    LoanApplicationView,
    LoanOptions,
    LoanProduct,
    NotEligible,
    Purpose,
)
from app.modules.shop.service import ShopService

LOAN_KIND = "loan.submit"
INCOME_DAYS = 90


class SubmissionRefused(Exception):
    """The authorization does not match the application, or no provider is configured."""


def _view(row: LoanApplicationRecord) -> LoanApplicationView:
    return LoanApplicationView(id=row.id, product_id=row.product_id, product_name=row.product_name,
                               amount_paisa=row.amount_paisa, tenure_months=row.tenure_months, purpose=row.purpose,
                               status=row.status, sandbox_ref=row.sandbox_ref, failure=row.failure,
                               submitted_at=row.submitted_at, decision_id=row.decision_id,
                               payload=json.loads(row.payload_json))


class LoansService:
    def __init__(self, session: Session, clock: Clock, shop_id: str, *, credit: CreditService,
                 sandbox: SandboxClient | None, buffer_paisa: int) -> None:
        self.session = session
        self.clock = clock
        self.shop_id = shop_id
        self.credit = credit
        self.sandbox = sandbox
        self.buffer_paisa = buffer_paisa
        self._forecast: CashForecast | None = None

    def products(self) -> list[LoanProduct]:
        return PRODUCTS

    # ---- what the shop can carry

    def forecast(self) -> CashForecast:
        if self._forecast is None:
            self._forecast = ForecastService(self.session, self.clock, self.shop_id,
                                             buffer_paisa=self.buffer_paisa).forecast()
        return self._forecast

    def affordability(self) -> Affordability:
        """Monthly business income over the last 90 days (sales minus stock and running costs, before
        household withdrawals), the instalment a lender would accept from it, and the forecast gap."""
        yesterday = local_date(self.clock.now()) - timedelta(days=1)
        books = LedgerService(self.session, self.clock).book_days(self.shop_id, yesterday - timedelta(days=INCOME_DAYS - 1),
                                                                  yesterday)
        net = sum(b.sales_cash + b.sales_mobile + b.sales_baki - b.purchases - b.expenses for b in books)
        income = max(0, round(net / len(books) * 30 / 100) * 100)
        forecast = self.forecast()
        gap = max(0, self.buffer_paisa - forecast.lowest_balance_paisa) if forecast.first_shortfall else 0
        return Affordability(monthly_business_income_paisa=income,
                             max_instalment_paisa=int(income * matcher.DEBT_SERVICE_SHARE) // 100 * 100,
                             shortfall_gap_paisa=gap,
                             shortfall_day=f"{forecast.first_shortfall.day:%a %d %b}" if forecast.first_shortfall else None)

    # ---- matching

    def profile(self) -> CreditProfile:
        """Today's credit profile (built now if there is none from today). Needs the owner's consent."""
        latest = self.credit.latest()
        if latest and local_date(latest.created_at) == local_date(self.clock.now()):
            return latest
        return self.credit.build()

    def options(self, amount_paisa: int | None = None, purpose: Purpose = "working_capital") -> LoanOptions:
        profile = self.profile()
        afford = self.affordability()
        facts = {f.name: f.value for f in profile.features}
        if amount_paisa:
            requested, basis = amount_paisa, "the amount you asked for"
        else:
            shop = ShopService(self.session, self.clock).get(self.shop_id)
            requested, basis = self._default_amount(facts, afford, purpose, shop.shop_type if shop else "grocery")

        def run(amount: int, why: str) -> LoanOptions:
            return matcher.match(PRODUCTS, requested=amount, amount_basis=why, purpose=purpose, facts=facts,
                                 band=profile.band, probability=profile.probability_of_default, afford=afford)

        options = run(requested, basis)
        if options.recommended is None and not amount_paisa:
            # Nothing carries the suggested amount: offer the most the shop's income can carry instead.
            eligible = [m.product for m in options.matches if m.eligible]
            most = max((matcher.largest_affordable(p, afford.max_instalment_paisa) for p in eligible), default=0)
            most = min(requested, most // 5_000_00 * 5_000_00)
            if most >= 5_000_00:
                options = run(most, f"the most your income can carry ({basis}, {fmt_taka(requested)}, is too much "
                                    "right now)")
        return options

    @staticmethod
    def _default_amount(facts: dict[str, float], afford: Affordability, purpose: Purpose,
                        shop_type: str) -> tuple[int, str]:
        def up(paisa: float) -> int:
            return int(math.ceil(paisa / 5_000_00)) * 5_000_00

        daily_stock = facts["avg_daily_sales"] * (1 - facts["gross_margin"]) * 100
        gap = up(afford.shortfall_gap_paisa)
        if purpose == "cover_shortfall" and gap:
            return gap, f"enough to cover the shortfall on {afford.shortfall_day}"
        if purpose == "eid_stock":
            extra = PRE_EID_FACTOR.get(shop_type, 1.2) - 1
            return up(daily_stock * 7 * extra), f"the extra stock for Eid week (about {extra:.0%} more sales for 7 days)"
        week = up(daily_stock * 7)
        if gap > week:
            return gap, f"enough to cover the shortfall on {afford.shortfall_day}"
        return week, "about one week of stock purchases"

    # ---- the application

    def prepare(self, product_id: str, *, amount_paisa: int | None = None, tenure_months: int | None = None,
                purpose: Purpose = "working_capital") -> ApplicationDraft:
        product = BY_ID.get(product_id)
        if product is None:
            raise NotEligible(f"There is no product {product_id!r}. Options: {', '.join(BY_ID)}.")
        options = self.options(amount_paisa, purpose)
        found = next(m for m in options.matches if m.product.id == product_id)
        if not found.eligible:
            raise NotEligible(found.explanation)
        if tenure_months is not None:
            if tenure_months not in product.tenures:
                raise NotEligible(f"{product.name} comes in {', '.join(map(str, product.tenures))}-month terms.")
            instalment, cost = matcher.repayment(product, found.amount_paisa, tenure_months)
            found = found.model_copy(update={"tenure_months": tenure_months, "monthly_instalment_paisa": instalment,
                                             "total_cost_paisa": cost,
                                             "affordable": instalment <= options.affordability.max_instalment_paisa})
        if not found.affordable:
            raise NotEligible(f"{product.name}: the repayment of {fmt_taka(found.monthly_instalment_paisa)} is more than "
                              f"your income can safely carry (about "
                              f"{fmt_taka(options.affordability.max_instalment_paisa)} a month). Try a smaller amount.")
        shop = ShopService(self.session, self.clock).get(self.shop_id)
        return application.build(shop=shop, profile=self.profile(), consent=self.credit.consent(), match=found,
                                 purpose=purpose, afford=options.affordability,
                                 forecast_headline=self.forecast().headline, now=self.clock.now())

    @staticmethod
    def auth_params(draft: ApplicationDraft) -> dict:
        """Exactly what the guard's token binds (and the lender checks)."""
        return {"applicant_account": draft.applicant_account, "product_id": draft.product_id,
                "amount_paisa": draft.amount_paisa, "tenure_months": draft.tenure_months,
                "payload_sha256": draft.payload_sha256}

    def submit(self, draft: ApplicationDraft, auth: AuthorizedAction, *, decision_id: str) -> LoanApplicationView:
        if sha256_hex(canonical_json(draft.payload)) != draft.payload_sha256:
            raise SubmissionRefused("the application changed after it was prepared")
        if not auth.binds(LOAN_KIND, self.auth_params(draft)):
            raise SubmissionRefused("the authorization does not bind this application")
        if self.sandbox is None:
            raise SubmissionRefused("no lender configured")
        row = LoanApplicationRecord(
            id=new_id("loan"), shop_id=self.shop_id, product_id=draft.product_id, product_name=draft.product_name,
            amount_paisa=draft.amount_paisa, tenure_months=draft.tenure_months, purpose=draft.purpose,
            payload_json=json.dumps(draft.payload, ensure_ascii=False), payload_sha256=draft.payload_sha256,
            idempotency_key=auth.idempotency_key, status="submitted", decision_id=decision_id,
            submitted_at=self.clock.now(),
        )
        try:
            result = self.sandbox.submit_application(
                {"applicant_account": draft.applicant_account, "shop_name": draft.shop_name,
                 "product_id": draft.product_id, "amount_paisa": draft.amount_paisa,
                 "tenure_months": draft.tenure_months, "payload": draft.payload},
                authorization=auth.header, idempotency_key=auth.idempotency_key)
            row.sandbox_ref = result["application_id"]
        except SandboxError as exc:
            row.status, row.failure = "failed", f"{exc.code}: {exc.message}"
        self.session.add(row)
        self.session.flush()
        return _view(row)

    def applications(self) -> list[LoanApplicationView]:
        rows = self.session.scalars(select(LoanApplicationRecord).where(LoanApplicationRecord.shop_id == self.shop_id)
                                    .order_by(LoanApplicationRecord.submitted_at.desc()))
        return [_view(r) for r in rows]
