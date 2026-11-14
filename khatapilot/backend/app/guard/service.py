"""Wires the guard for one unit of work (one database session).

The owner screen's controls are deliberately not built here: only the interface layer's PIN-protected
routes construct them, from the parts below. Settings changes asked for in chat are guard-held
proposals (`control_tools.py`) applied by the executor only after the owner approves them.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial

from sqlalchemy.orm import Session

from app.core.actions import ActionKind
from app.core.clock import Clock
from app.core.config import Settings
from app.core.money import to_paisa
from app.guard.approvals import ApprovalService
from app.guard.capability import CapabilitySigner
from app.guard.engine import PolicyEngine
from app.core.provenance import TurnContext
from app.guard.executor import ActionExecutor
from app.guard.gateway import ToolCall, ToolDescriptor, ToolGateway, ToolOutcome
from app.guard.intent import IntentStore
from app.guard.killswitch import KillSwitchService
from app.guard.owner_sms import OwnerSms
from app.guard.pin import PinManager
from app.guard.policy_store import Limits, PolicyStore
from app.guard.recorder import Recorder
from app.guard.rechecks import HeldActionRechecks
from app.guard.scheduler import PaymentScheduler
from app.guard.control_tools import apply_control, register_control_tools
from app.guard.tools import register_core_tools
from app.infra.audit_log import AuditLog
from app.infra.db import Database
from app.infra.phone_gateway import PhoneGateway
from app.infra.sandbox_client import SandboxClient
from app.modules.credit.service import CreditModel, CreditService
from app.modules.forecast.service import ForecastService
from app.modules.inbox.service import InboxService
from app.modules.loans.service import LoansService
from app.modules.ledger.service import LedgerService
from app.modules.payments.service import PaymentsService
from app.modules.reminders.service import RemindersService
from app.modules.reports.service import ReportsService
from app.modules.risk.service import FraudSentinel, RiskService
from app.modules.growth.service import GrowthService
from app.modules.savings.service import SavingsService
from app.modules.shop.service import ShopService


@dataclass
class GuardDeps:
    """Long-lived things the guard needs; built once by the composition root."""

    settings: Settings
    clock: Clock
    audit: AuditLog
    signer: CapabilitySigner
    sandbox: SandboxClient | None = None
    fraud_model: FraudSentinel | None = None  # None: the transparent rule-based score
    credit_model: CreditModel | None = None  # None: credit profiles are unavailable until it is trained
    phone: PhoneGateway | None = None  # the owner's phone, when SMS also go out from it (settings.sms_via_phone)


def default_limits(settings: Settings) -> Limits:
    return Limits(
        per_txn_paisa=to_paisa(settings.per_txn_limit_taka),
        daily_paisa=to_paisa(settings.daily_limit_taka),
        fraud_medium=settings.fraud_medium,
        fraud_high=settings.fraud_high,
        split_window_hours=settings.split_window_hours,
        velocity_max_per_hour=settings.velocity_max_per_hour,
    )


class Guard:
    def __init__(self, session: Session, deps: GuardDeps, shop_id: str | None = None) -> None:
        settings, clock = deps.settings, deps.clock
        self.shop_id = shop_id or settings.shop_id
        self.session = session
        self.clock = clock
        self.store = PolicyStore(session, clock, self.shop_id, default_limits(settings))
        self.store.ensure()
        self.pins = PinManager(self.store, clock, max_attempts=settings.pin_max_attempts,
                               lock_minutes=settings.pin_lock_minutes)
        self.shops = ShopService(session, clock)
        self.ledger = LedgerService(session, clock)
        self.payments = PaymentsService(session, clock, self.shop_id, deps.sandbox)
        self.risk = RiskService(session, clock, self.shop_id, deps.fraud_model)
        self.forecasts = ForecastService(session, clock, self.shop_id,
                                         standard_buffer_paisa=to_paisa(settings.forecast_buffer_taka))
        self.forecast_days = settings.forecast_days
        self.savings = SavingsService(session, clock, self.shop_id,
                                      standard_buffer_paisa=to_paisa(settings.forecast_buffer_taka))
        self.growth = GrowthService(session, clock, self.shop_id,
                                    standard_buffer_paisa=to_paisa(settings.forecast_buffer_taka))
        self.credit = CreditService(session, clock, self.shop_id, deps.credit_model)
        self.loans = LoansService(session, clock, self.shop_id, credit=self.credit, sandbox=deps.sandbox,
                                  standard_buffer_paisa=to_paisa(settings.forecast_buffer_taka))
        self.reminders = RemindersService(session, clock, self.shop_id, deps.sandbox, phone=deps.phone)
        self.inbox = InboxService(session, clock, self.shop_id)
        self.settings = settings
        self.reports = ReportsService(session, clock, self.shop_id, savings=self.savings, growth=self.growth,
                                      credit=self.credit, loans=self.loans, payments=self.payments,
                                      forecasts=self.forecasts)
        self.intents = IntentStore(session, clock, self.shop_id, settings.intent_ttl_minutes)
        self.recorder = Recorder(deps.audit)
        self.owner_sms = OwnerSms(signer=deps.signer, sandbox=deps.sandbox, phone=deps.phone, recorder=self.recorder)
        self.engine = PolicyEngine(clock=clock, shop_id=self.shop_id, store=self.store, pins=self.pins,
                                   intents=self.intents, risk=self.risk, payments=self.payments, shops=self.shops)
        self.executor = ActionExecutor(clock=clock, payments=self.payments, signer=deps.signer, recorder=self.recorder)
        self.executor.register(ActionKind.LOAN_SUBMIT, partial(self.executor.submit_loan, self.loans))
        self.executor.register(ActionKind.MESSAGE, partial(self.executor.send_reminders, self.reminders))
        self.executor.register(ActionKind.CONTROL_CHANGE, partial(apply_control, self))
        self.executor.register(ActionKind.CONTROL_BLOCK, partial(apply_control, self))
        self.approvals = ApprovalService(session=session, clock=clock, shop_id=self.shop_id, store=self.store,
                                         pins=self.pins, executor=self.executor, recorder=self.recorder,
                                         recheck=HeldActionRechecks(store=self.store, payments=self.payments,
                                                                    savings_status=lambda: self.savings.status()))
        self.kill_switch = KillSwitchService(clock=clock, shop_id=self.shop_id, store=self.store, pins=self.pins,
                                             payments=self.payments, shops=self.shops, sandbox=deps.sandbox,
                                             signer=deps.signer, recorder=self.recorder)
        self.scheduler = PaymentScheduler(clock=clock, store=self.store, payments=self.payments,
                                          executor=self.executor, recorder=self.recorder)
        self.gateway = ToolGateway(engine=self.engine, approvals=self.approvals, executor=self.executor,
                                   recorder=self.recorder)
        register_core_tools(self)
        register_control_tools(self)


class GuardPort:
    """The `ToolPort` handed to the agent. Every tool call is its own unit of work: it is decided,
    executed, audited and committed before the model hears back, so a slow model never holds a
    database lock and an executed payment is never lost to a later error."""

    def __init__(self, db: Database, deps: GuardDeps, shop_id: str | None = None) -> None:
        self._db = db
        self._deps = deps
        self._shop_id = shop_id

    def descriptors(self) -> list[ToolDescriptor]:
        with self._db.session() as session:
            return Guard(session, self._deps, self._shop_id).gateway.descriptors()

    def submit(self, call: ToolCall, turn: TurnContext) -> ToolOutcome:
        with self._db.session() as session:
            return Guard(session, self._deps, self._shop_id).gateway.submit(call, turn)
