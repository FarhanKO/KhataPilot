"""Calibration: turning model scores into probabilities you can take at face value, and measuring it."""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression


def fit_platt(raw: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Platt scaling on out-of-fold raw scores: a two-parameter fit, stable on a few hundred shops
    where isotonic regression would overfit."""
    fit = LogisticRegression(C=1e6).fit(raw.reshape(-1, 1), y)
    return float(fit.coef_[0][0]), float(fit.intercept_[0])


def apply_platt(raw: np.ndarray, a: float, b: float) -> np.ndarray:
    return 1 / (1 + np.exp(-(a * np.asarray(raw, dtype=float) + b)))


def brier(probability: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((probability - y) ** 2))


def reliability(probability: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    """Equal-count bins: the average predicted probability against the share that actually defaulted."""
    order = np.argsort(probability)
    rows = []
    for chunk in np.array_split(order, bins):
        if len(chunk):
            rows.append({"predicted": float(probability[chunk].mean()), "observed": float(y[chunk].mean()),
                         "shops": int(len(chunk))})
    return rows


def expected_calibration_error(probability: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    curve = reliability(probability, y, bins)
    total = sum(r["shops"] for r in curve)
    return float(sum(r["shops"] * abs(r["predicted"] - r["observed"]) for r in curve) / total)
