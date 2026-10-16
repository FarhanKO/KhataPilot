"""A basic fairness check: approval rates, errors and calibration by shop type and region.

Shop type and region are not model inputs, but other features can stand in for them (a mobile
recharge shop has thin margins, a tea stall has jumpy sales). So we look at outcomes per group, on
out-of-fold predictions (each shop scored by a model that never saw it):

- approval rate, and the ratio of the lowest to the highest group (the "four-fifths" rule of thumb);
- approval rate among shops that would have repaid (equal opportunity: good payers should be
  approved at similar rates whatever their group);
- predicted against observed default rate (calibration within the group), with a 95% interval
  for the observed rate: groups of 25-30 shops are noisy, and a gap inside the interval is not
  evidence of bias.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ATTRIBUTES = ("shop_type", "urbanicity", "division")


def wilson(successes: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return float(centre - half), float(centre + half)


def _group_rows(frame: pd.DataFrame, attribute: str, approve_below: float) -> list[dict[str, Any]]:
    rows = []
    for value, g in frame.groupby(attribute, sort=True):
        approved = g["pd"] < approve_below
        good = g["y"] == 0
        low, high = wilson(float(g["y"].sum()), len(g))
        rows.append({
            "group": str(value),
            "shops": int(len(g)),
            "default_rate": float(g["y"].mean()),
            "default_rate_ci95": [low, high],
            "mean_predicted": float(g["pd"].mean()),
            "calibrated_within_ci": bool(low <= g["pd"].mean() <= high),
            "approval_rate": float(approved.mean()),
            "approval_rate_good_payers": float(approved[good].mean()) if good.any() else None,
            "default_rate_if_approved": float(g.loc[approved, "y"].mean()) if approved.any() else None,
            "roc_auc": float(roc_auc_score(g["y"], g["pd"])) if g["y"].nunique() == 2 and g["y"].sum() >= 5 else None,
        })
    return rows


def report(meta: pd.DataFrame, y: np.ndarray, probability: np.ndarray, approve_below: float) -> dict[str, Any]:
    frame = meta.assign(y=y, pd=probability)
    out: dict[str, Any] = {"approve_below": approve_below}
    for attribute in ATTRIBUTES:
        rows = _group_rows(frame, attribute, approve_below)
        rates = [r["approval_rate"] for r in rows if r["shops"] >= 20]
        good = [r["approval_rate_good_payers"] for r in rows if r["shops"] >= 20 and r["approval_rate_good_payers"]]
        out[attribute] = {
            "groups": rows,
            "approval_ratio_min_max": float(min(rates) / max(rates)) if rates and max(rates) > 0 else None,
            "good_payer_approval_gap": float(max(good) - min(good)) if good else None,
            "max_calibration_gap": float(max(abs(r["mean_predicted"] - r["default_rate"]) for r in rows
                                             if r["shops"] >= 20)) if rows else None,
        }
    return out
