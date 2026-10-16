"""Training and evaluating Fraud Sentinel (mobile-money edition), following the original project's
methodology: chronological split (no future leakage), calibration, the banking risk-cost function,
and population stability (PSI) between the training and evaluation windows."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from app.modules.risk.features import FEATURES, vector
from app.modules.risk.schemas import PaymentFeatures
from app.modules.risk.sentinel import HIGH_VALUE_DAYS, FraudSentinel

ALPHA = 1.0  # weight on every taka of missed fraud
BETA = 50.0  # taka-equivalent cost of one false alarm (the owner has to stop and enter a PIN)
MEDIUM, HIGH = 0.50, 0.85  # the guard's default thresholds
RAW_COLUMNS = ["amount_paisa", "payee_trusted", "payee_history_count", "payee_median_paisa",
               "average_daily_sales_paisa", "local_hour", "payments_last_hour", "payments_last_day",
               "new_payees_last_day"]


def features_of(row: dict[str, Any]) -> PaymentFeatures:
    median = row["payee_median_paisa"]
    return PaymentFeatures(
        amount_paisa=int(row["amount_paisa"]), payee_trusted=bool(row["payee_trusted"]),
        payee_history_count=int(row["payee_history_count"]),
        payee_median_paisa=None if pd.isna(median) or not median else int(median),
        average_daily_sales_paisa=int(row["average_daily_sales_paisa"]), local_hour=int(row["local_hour"]),
        payments_last_hour=int(row["payments_last_hour"]), payments_last_day=int(row["payments_last_day"]),
        new_payees_last_day=int(row["new_payees_last_day"]),
    )


def matrix(frame: pd.DataFrame) -> tuple[np.ndarray, list[PaymentFeatures]]:
    rows = [features_of(r) for r in frame[RAW_COLUMNS].to_dict("records")]
    return np.array([vector(f) for f in rows], dtype=float), rows


def calculate_psi(expected: np.ndarray, actual: np.ndarray, buckets: int = 10) -> float:
    """Population Stability Index, as in the original project's utils.calculate_psi."""
    breakpoints = np.unique(np.percentile(expected, np.linspace(0, 100, buckets + 1)))
    if len(breakpoints) < 3:
        return 0.0
    breakpoints[0], breakpoints[-1] = -np.inf, np.inf
    expected_pct = np.clip(np.histogram(expected, bins=breakpoints)[0] / len(expected), 1e-4, None)
    actual_pct = np.clip(np.histogram(actual, bins=breakpoints)[0] / len(actual), 1e-4, None)
    return float(np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct)))


def _booster(seed: int) -> LGBMClassifier:
    return LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=20,
                          class_weight="balanced", random_state=seed, n_jobs=1, deterministic=True,
                          force_row_wise=True, verbose=-1)


def train(frame: pd.DataFrame, *, seed: int = 42, test_days: int = 15) -> FraudSentinel:
    frame = frame.sort_values(["day", "local_hour"]).reset_index(drop=True)
    cutoff = frame["day"].max() - test_days + 1
    train_frame, test_frame = frame[frame["day"] < cutoff], frame[frame["day"] >= cutoff]
    X_train, _ = matrix(train_frame)
    y_train = train_frame["label"].to_numpy()

    anomaly = IsolationForest(n_estimators=200, random_state=seed, n_jobs=1).fit(X_train[y_train == 0])
    reference = np.sort(-anomaly.score_samples(X_train[y_train == 0]))
    classifier = CalibratedClassifierCV(_booster(seed), method="isotonic", cv=3).fit(X_train, y_train)
    explainer = _booster(seed).fit(X_train, y_train)
    model = FraudSentinel(anomaly=anomaly, reference=reference, classifier=classifier, explainer=explainer)
    model.metrics = evaluate(model, train_frame, test_frame)
    return model


def _operating(flags: np.ndarray, y: np.ndarray, amounts: np.ndarray) -> dict[str, Any]:
    fraud, legit = y == 1, y == 0
    missed = amounts[fraud & ~flags].sum() / 100
    false_alarms = int((flags & legit).sum())
    return {
        "recall": round(float(flags[fraud].mean()), 4) if fraud.any() else None,
        "precision": round(float(y[flags].mean()), 4) if flags.any() else None,
        "false_alarm_rate": round(float(flags[legit].mean()), 4),
        "missed_fraud_taka": round(float(missed), 2),
        "false_alarms": false_alarms,
        "banking_risk_cost_taka": round(ALPHA * float(missed) + BETA * false_alarms, 2),
    }


def evaluate(model: FraudSentinel, train_frame: pd.DataFrame, test_frame: pd.DataFrame) -> dict[str, Any]:
    from app.modules.risk.service import score_rules

    X_train, _ = matrix(train_frame)
    X_test, rows = matrix(test_frame)
    y = test_frame["label"].to_numpy()
    amounts = test_frame["amount_paisa"].to_numpy()
    high = np.array([f.amount_paisa > HIGH_VALUE_DAYS * max(f.average_daily_sales_paisa, 1) for f in rows])
    score, percentile, probability = model.score_matrix(X_test, high)
    rules = np.array([score_rules(f).score for f in rows])

    per_type = {}
    for typology, group in test_frame.groupby("typology"):
        mask = (test_frame["typology"] == typology).to_numpy()
        per_type[typology] = {
            "payments": int(mask.sum()),
            "flagged_rate": round(float((score[mask] >= MEDIUM).mean()), 4),
            "blocked_rate": round(float((score[mask] >= HIGH).mean()), 4),
            "rules_flagged_rate": round(float((rules[mask] >= MEDIUM).mean()), 4),
        }
    prob_true, prob_pred = calibration_curve(y, probability, n_bins=10, strategy="uniform")
    return {
        "version": model.version,
        "train_payments": int(len(train_frame)), "test_payments": int(len(test_frame)),
        "train_fraud_rate": round(float(train_frame["label"].mean()), 4),
        "test_fraud_rate": round(float(y.mean()), 4),
        "split": "chronological: last 15 days held out",
        "layer2": {
            "roc_auc": round(float(roc_auc_score(y, probability)), 4),
            "pr_auc": round(float(average_precision_score(y, probability)), 4),
            "brier": round(float(brier_score_loss(y, probability)), 4),
            "calibration": [{"predicted": round(float(p), 3), "observed": round(float(t), 3)}
                            for p, t in zip(prob_pred, prob_true, strict=True)],
        },
        "cascade_at_medium": _operating(score >= MEDIUM, y, amounts),
        "cascade_at_high": _operating(score >= HIGH, y, amounts),
        "rules_baseline_at_medium": _operating(rules >= MEDIUM, y, amounts),
        "layer1_escalated_legit_rate": round(float((percentile[y == 0] >= 0.99).mean()), 4),
        "per_typology": per_type,
        "psi": {name: round(calculate_psi(X_train[:, i], X_test[:, i]), 4) for i, name in enumerate(FEATURES)},
        "cost_function": f"banking risk cost = {ALPHA} x missed fraud taka + {BETA} x false alarms",
    }
