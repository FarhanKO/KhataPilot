"""Fraud Sentinel, mobile-money edition.

A port of the dual-layer cascade from the team's Fraud Sentinel project
(https://github.com/FarhanKO/Fraud-Detection), retrained on mobile-money payment features. The
original models were trained on the ULB credit-card dataset's anonymised PCA features (V1-V28),
which bKash/Nagad payments do not have, so the design is reused, not the weights:

- Layer 1, unsupervised: an Isolation Forest trained only on normal payments. A payment far
  outside the shop-normal pattern is escalated (99th percentile) or blocked (99.95th) as a
  structural, "zero-day" anomaly, before any pattern matching.
- Layer 2, supervised: a gradient-boosted classifier (LightGBM here; CatBoost in the original)
  with isotonic calibration, so a probability of 0.7 means about 70% of such payments were fraud.
- Amount-aware thresholds, as in the original: a payment larger than two days of sales is held to
  a stricter bar (its probability counts double against the guard's thresholds).

The score feeds the guard's fraud check (medium threshold escalates to tier 3, high blocks). Reasons
come from LightGBM's per-feature contributions (TreeSHAP values computed by LightGBM itself).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app.infra.model_files import ModelIntegrityError, load_checked, save_checked
from app.modules.risk.features import FEATURES, phrase, reassurance, vector
from app.modules.risk.schemas import PaymentFeatures, RiskScore

MODEL_VERSION = "fraud-sentinel-mm-v1"
ESCALATE_PERCENTILE = 0.99
BLOCK_PERCENTILE = 0.9995
HIGH_VALUE_DAYS = 2.0
LOW_SCORE = 0.10  # below this, explain why the payment looks normal instead of listing weak risk factors
MIN_CONTRIBUTION = 0.3  # log-odds a feature must add before it is named as a reason


__all__ = ["FraudSentinel", "ModelIntegrityError"]


@dataclass
class FraudSentinel:
    anomaly: Any  # IsolationForest, fitted on normal payments only
    reference: np.ndarray  # sorted layer 1 scores of the normal training payments
    classifier: Any  # calibrated classifier (layer 2)
    explainer: Any  # the uncalibrated LightGBM model, for per-feature contributions
    version: str = MODEL_VERSION
    metrics: dict[str, Any] = field(default_factory=dict)

    # ---- scoring

    def score_matrix(self, X: np.ndarray, high_value: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Batch scores: (final score, layer 1 percentile, layer 2 calibrated probability)."""
        raw = -self.anomaly.score_samples(X)
        percentile = np.searchsorted(self.reference, raw, side="right") / len(self.reference)
        probability = self.classifier.predict_proba(X)[:, 1]
        score = np.minimum(1.0, probability * np.where(high_value, 2.0, 1.0))
        score = np.where(percentile >= BLOCK_PERCENTILE, np.maximum(score, 0.9), score)
        score = np.where((percentile >= ESCALATE_PERCENTILE) & (percentile < BLOCK_PERCENTILE),
                         np.maximum(score, 0.6), score)
        return score, percentile, probability

    def assess(self, f: PaymentFeatures) -> RiskScore:
        x = np.array([vector(f)], dtype=float)
        high = np.array([f.amount_paisa > HIGH_VALUE_DAYS * max(f.average_daily_sales_paisa, 1)])
        score, percentile, probability = (float(v[0]) for v in self.score_matrix(x, high))
        reasons = []
        if percentile >= BLOCK_PERCENTILE:
            reasons.append("it is extremely unlike this shop's normal payments (layer 1 anomaly)")
        elif percentile >= ESCALATE_PERCENTILE:
            reasons.append("it is unlike this shop's normal payments (layer 1 anomaly)")
        if score < LOW_SCORE and not reasons:
            reasons = reassurance(f) or ["nothing unusual"]
        else:
            contributions = self.explainer.predict(x, pred_contrib=True)[0][: len(FEATURES)]
            for index in np.argsort(-contributions):
                if contributions[index] < MIN_CONTRIBUTION or len(reasons) >= 3:
                    break
                text = phrase(FEATURES[index], f)
                if text and text not in reasons:
                    reasons.append(text)
        return RiskScore(score=round(score, 3), reasons=reasons or ["nothing unusual"], model=self.version,
                         layer1_percentile=round(percentile, 4), layer2_probability=round(probability, 4))

    # ---- persistence (with an integrity check: only load what train_models.py wrote)

    def save(self, path: Path) -> str:
        return save_checked(self, path)

    @classmethod
    def load(cls, path: Path) -> FraudSentinel:
        return load_checked(path, cls, name="Fraud Sentinel", train_with="scripts/train_models.py")
