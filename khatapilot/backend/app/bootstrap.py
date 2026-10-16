"""Composition root: registers every module's tables and builds the long-lived services.

Like `app.main`, this file may import from every layer; nothing else may.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.agent.llm.provider import make_provider
from app.agent.llm.types import LLMProvider
from app.agent.orchestrator import Orchestrator
from app.core.clock import Clock, SimClock, SystemClock
from app.core.config import Settings
from app.guard.capability import CapabilitySigner
from app.guard.service import GuardDeps, GuardPort
from app.infra.audit_log import AuditEntry, AuditLog
from app.infra.db import Database
from app.infra.sandbox_client import SandboxClient
from app.infra.trace_bus import TraceBus
from app.modules.credit.service import CreditModel
from app.modules.risk.service import FraudSentinel, ModelIntegrityError

log = logging.getLogger(__name__)


def register_models() -> None:
    """Import every module's models so `Base.metadata` knows all tables."""
    import app.guard.models  # noqa: F401
    import app.modules.chat.models  # noqa: F401
    import app.modules.credit.models  # noqa: F401
    import app.modules.ledger.models  # noqa: F401
    import app.modules.loans.models  # noqa: F401
    import app.modules.payments.models  # noqa: F401
    import app.modules.reminders.models  # noqa: F401
    import app.modules.shop.models  # noqa: F401


def make_clock(settings: Settings) -> Clock:
    return SimClock(settings.sim_time) if settings.sim_time else SystemClock()


def make_database(settings: Settings) -> Database:
    register_models()
    database = Database(settings.db_url)
    database.create_all()
    return database


def trace_card(entry: AuditEntry) -> dict:
    return {"type": "audit", **entry.model_dump()}


def load_credit_model(settings: Settings) -> CreditModel | None:
    """The trained credit model if its file checks out; otherwise None (profiles are unavailable)."""
    try:
        return CreditModel.load(settings.credit_model_path)
    except ModelIntegrityError as problem:
        log.warning("%s Credit profiles are unavailable until it is trained.", problem)
        return None


def load_fraud_model(settings: Settings) -> FraudSentinel | None:
    """Fraud Sentinel if it is configured and its file checks out; otherwise the rule-based score."""
    if settings.risk_model != "sentinel":
        return None
    try:
        return FraudSentinel.load(settings.risk_model_path)
    except ModelIntegrityError as problem:
        log.warning("%s Falling back to the rule-based fraud score.", problem)
        return None


@dataclass
class Container:
    settings: Settings
    clock: Clock
    db: Database
    audit: AuditLog
    trace: TraceBus
    sandbox: SandboxClient
    signer: CapabilitySigner
    provider: LLMProvider
    agent: Orchestrator
    fraud_model: FraudSentinel | None = None
    credit_model: CreditModel | None = None

    @property
    def guard_deps(self) -> GuardDeps:
        return GuardDeps(settings=self.settings, clock=self.clock, audit=self.audit, signer=self.signer,
                         sandbox=self.sandbox, fraud_model=self.fraud_model, credit_model=self.credit_model)

    @property
    def risk_model_name(self) -> str:
        return self.fraud_model.version if self.fraud_model else "rules-v1"

    def tool_port(self) -> GuardPort:
        return GuardPort(self.db, self.guard_deps)

    def close(self) -> None:
        self.audit.dispose()
        self.db.dispose()


def build_container(settings: Settings, *, clock: Clock | None = None, sandbox: SandboxClient | None = None,
                    signer: CapabilitySigner | None = None, provider: LLMProvider | None = None) -> Container:
    clock = clock or make_clock(settings)
    audit = AuditLog(settings.audit_db_url, clock)
    trace = TraceBus()
    audit.subscribe(lambda entry: trace.publish(trace_card(entry)))
    provider = provider or make_provider(settings)
    return Container(
        settings=settings,
        clock=clock,
        db=make_database(settings),
        audit=audit,
        trace=trace,
        sandbox=sandbox or SandboxClient(settings.sandbox_url, admin_key=settings.sandbox_admin_key.get_secret_value()),
        signer=signer or CapabilitySigner(ttl_seconds=settings.token_ttl_seconds),
        provider=provider,
        agent=Orchestrator(provider, max_steps=settings.agent_max_steps),
        fraud_model=load_fraud_model(settings),
        credit_model=load_credit_model(settings),
    )
