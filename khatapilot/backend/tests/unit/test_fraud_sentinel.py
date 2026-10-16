"""Fraud Sentinel (mobile-money edition): the ported dual-layer cascade."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.bootstrap import load_fraud_model
from app.core.decisions import CheckStatus, Outcome
from app.guard.tools import PaymentArgs, payment_action
from app.modules.risk.schemas import PaymentFeatures
from app.modules.risk.sentinel import ModelIntegrityError
from app.modules.risk.service import FraudSentinel, RiskService
from app.modules.risk.training import calculate_psi, train
from data.generate_payments import generate
from tests.guard_harness import SCAMMER, turn

SALES = 14_000_00  # ৳14,000 a day, in paisa


@pytest.fixture(scope="module")
def payments():
    return generate(n_shops=250, days=60, seed=11)


@pytest.fixture(scope="module")
def model(payments) -> FraudSentinel:
    return train(payments, seed=11)


def features(**overrides) -> PaymentFeatures:
    base = dict(amount_paisa=35_000_00, payee_trusted=True, payee_history_count=15, payee_median_paisa=35_000_00,
                average_daily_sales_paisa=SALES, local_hour=11, payments_last_hour=0, payments_last_day=0,
                new_payees_last_day=0)
    base.update(overrides)
    return PaymentFeatures(**base)


def test_it_beats_the_rule_baseline(model):
    metrics = model.metrics
    assert metrics["layer2"]["roc_auc"] > 0.95
    assert metrics["cascade_at_medium"]["recall"] > metrics["rules_baseline_at_medium"]["recall"]
    assert metrics["cascade_at_medium"]["false_alarm_rate"] < 0.03
    assert metrics["cascade_at_medium"]["banking_risk_cost_taka"] < metrics["rules_baseline_at_medium"]["banking_risk_cost_taka"]


def test_the_usual_supplier_payment_is_low_risk(model):
    risk = model.assess(features())
    assert risk.score < 0.5 and risk.model == "fraud-sentinel-mm-v1"


def test_a_large_night_transfer_to_a_stranger_is_blocked(model):
    risk = model.assess(features(amount_paisa=70_000_00, payee_trusted=False, payee_history_count=0,
                                 payee_median_paisa=None, local_hour=2))
    assert risk.score >= 0.85
    assert any("never paid this number" in r or "days of this shop's sales" in r for r in risk.reasons), risk.reasons
    assert risk.layer2_probability is not None and risk.layer1_percentile is not None


def test_splitting_under_the_limit_is_caught(model):
    risk = model.assess(features(amount_paisa=9_500_00, payee_trusted=False, payee_history_count=3,
                                 payee_median_paisa=9_400_00, payments_last_hour=3, payments_last_day=3,
                                 new_payees_last_day=1, local_hour=15))
    assert risk.score >= 0.5, risk


def test_training_is_deterministic(payments, model):
    again = train(payments, seed=11)
    sample = features(amount_paisa=20_000_00, payee_trusted=False, payee_history_count=0, payee_median_paisa=None)
    assert again.assess(sample) == model.assess(sample)


def test_save_and_load_check_integrity(model, tmp_path):
    path = tmp_path / "fraud_sentinel.joblib"
    model.save(path)
    assert FraudSentinel.load(path).assess(features()) == model.assess(features())
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ModelIntegrityError, match="checksum"):
        FraudSentinel.load(path)
    with pytest.raises(ModelIntegrityError, match="No trained model"):
        FraudSentinel.load(tmp_path / "missing.joblib")


def test_the_app_falls_back_to_rules_without_a_model(settings, tmp_path):
    assert load_fraud_model(settings) is None  # tests run with risk_model="rules"
    configured = settings.model_copy(update={"risk_model": "sentinel", "risk_model_path": tmp_path / "none.joblib"})
    assert load_fraud_model(configured) is None


def test_a_broken_model_never_breaks_the_guard(world):
    class Broken:
        def assess(self, _features):
            raise RuntimeError("boom")

    with world.guard() as guard:
        risk = RiskService(guard.session, world.clock, guard.shop_id, Broken()).score_payment(features())
    assert risk.model == "rules-v1"


def _evaluate_stranger_payment(world, model):
    world.deps.fraud_model = model
    t = turn("send 90000 to 01999888777")
    with world.guard() as guard:
        return guard.engine.evaluate(payment_action(PaymentArgs(payee_number=SCAMMER, amount_taka=90000), t), t)


def test_the_guard_escalates_a_large_daytime_transfer_to_a_stranger(world, model):
    decision = _evaluate_stranger_payment(world, model)  # 10:00: genuine big first payments also happen by day
    fraud = decision.check("fraud")
    assert fraud.status is CheckStatus.ESCALATE and "fraud-sentinel-mm-v1" in fraud.detail
    assert decision.outcome is Outcome.HOLD_PIN


def test_the_guard_blocks_the_same_transfer_at_night(world, model):
    world.clock.set(world.clock.now().replace(hour=2) + timedelta(days=1))
    decision = _evaluate_stranger_payment(world, model)
    assert decision.check("fraud").status is CheckStatus.BLOCK
    assert decision.outcome is Outcome.BLOCK


def test_psi_flags_a_shifted_distribution():
    import numpy as np

    rng = np.random.default_rng(0)
    assert calculate_psi(rng.normal(0, 1, 5000), rng.normal(0, 1, 5000)) < 0.1
    assert calculate_psi(rng.normal(0, 1, 5000), rng.normal(1.5, 1, 5000)) > 0.2
