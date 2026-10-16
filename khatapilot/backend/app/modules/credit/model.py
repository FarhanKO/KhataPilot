"""The credit model: a small LightGBM with monotone constraints, calibrated with Platt scaling.

Monotone constraints keep the model economically sensible (paying suppliers on time can never
raise the risk), which also keeps its explanations sensible. The calibrated output is the
probability that the shop would fail to repay a 12-month working-capital loan. SHAP values are
LightGBM's own TreeSHAP (`pred_contrib`), checked against the `shap` library at training time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np

from app.infra.model_files import load_checked, save_checked
from app.modules.credit.calibration import apply_platt

MODEL_VERSION = "khata-credit-v1"
# Upper bounds on the probability of default for each band.
BANDS: list[tuple[str, float, str]] = [
    ("A", 0.05, "very low risk"),
    ("B", 0.10, "low risk"),
    ("C", 0.20, "moderate risk"),
    ("D", 0.35, "high risk"),
    ("E", 1.01, "very high risk"),
]
APPROVE_BELOW = 0.20  # the illustrative lending policy used for the fairness check: bands A to C


def band_for(probability: float) -> tuple[str, str]:
    for letter, upper, words in BANDS:
        if probability < upper:
            return letter, words
    return BANDS[-1][0], BANDS[-1][2]


@dataclass
class CreditModel:
    booster: lgb.Booster
    platt: tuple[float, float]
    features: list[str]
    peers: dict[str, dict[str, float]]  # per feature: p25, median and p75 across the training shops
    trained_on: dict[str, Any] = field(default_factory=dict)
    version: str = MODEL_VERSION

    def raw(self, x: np.ndarray) -> np.ndarray:
        return self.booster.predict(x, raw_score=True)

    def probability(self, x: np.ndarray) -> np.ndarray:
        return apply_platt(self.raw(x), *self.platt)

    def contributions(self, x: np.ndarray) -> tuple[dict[str, float], float]:
        """Each feature's push on the log-odds of default for one shop, and the base value."""
        row = self.booster.predict(x, pred_contrib=True)[0]
        return dict(zip(self.features, (float(v) for v in row[:-1]), strict=True)), float(row[-1])

    def save(self, path: Path) -> str:
        return save_checked(self, path)

    @classmethod
    def load(cls, path: Path) -> CreditModel:
        return load_checked(path, cls, name="credit", train_with="scripts/train_credit.py")
