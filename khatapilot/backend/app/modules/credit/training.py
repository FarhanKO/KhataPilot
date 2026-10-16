"""Training and evaluation of the credit model on the synthetic shops (scripts/train_credit.py).

Every shop has a hidden "true" probability of default and a drawn default/no-default label
(data/generate_shops.py::credit_label), set from behaviour only, never from shop type or region.
With a few hundred shops, everything is measured out-of-fold: each shop is scored by a model
trained without it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.modules.credit import calibration, fairness
from app.modules.credit.features import COLUMNS, FEATURES, NAMES, SCORED, Feature, compute
from app.modules.credit.model import APPROVE_BELOW, BANDS, MODEL_VERSION, CreditModel

ROUNDS = (60, 100, 150, 200)
FOLDS = 5
ABLATION_SEEDS = range(42, 50)


def params(seed: int, features: Sequence[Feature] = SCORED) -> dict[str, Any]:
    return {
        "objective": "binary", "learning_rate": 0.03, "num_leaves": 4, "min_data_in_leaf": 20,
        "feature_fraction": 0.9, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 2.0,
        "monotone_constraints": [f.monotone for f in features], "monotone_constraints_method": "advanced",
        "verbose": -1, "seed": seed, "deterministic": True, "force_row_wise": True, "num_threads": 1,
    }


def build_dataset(shops: pd.DataFrame, daily: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, pd.DataFrame]:
    """All features per labelled shop, its label, and its group attributes (for the fairness check only)."""
    labelled = shops[shops["defaulted"].notna()].set_index("shop_id")
    rows, ids = [], []
    for shop_id, days in daily.groupby("shop_id", sort=True):
        if shop_id not in labelled.index:
            continue
        days = days.sort_values("date")
        rows.append(compute({c: days[c].to_numpy() for c in COLUMNS}, int(labelled.at[shop_id, "history_days"])))
        ids.append(shop_id)
    x = pd.DataFrame(rows, index=ids)[NAMES]
    meta = labelled.loc[ids, ["shop_type", "urbanicity", "division", "true_pd"]]
    return x, labelled.loc[ids, "defaulted"].astype(int).to_numpy(), meta


def _out_of_fold(x: np.ndarray, y: np.ndarray, rounds: int, seed: int,
                 features: Sequence[Feature] = SCORED) -> np.ndarray:
    raw = np.zeros(len(y))
    for train, test in StratifiedKFold(FOLDS, shuffle=True, random_state=seed).split(x, y):
        booster = lgb.train(params(seed, features), lgb.Dataset(x[train], y[train]), num_boost_round=rounds)
        raw[test] = booster.predict(x[test], raw_score=True)
    return raw


def _baseline_oof(x: np.ndarray, y: np.ndarray, seed: int) -> np.ndarray:
    out = np.zeros(len(y))
    for train, test in StratifiedKFold(FOLDS, shuffle=True, random_state=seed).split(x, y):
        lr = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=2000)).fit(x[train], y[train])
        out[test] = lr.predict_proba(x[test])[:, 1]
    return out


def _summary(probability: np.ndarray, y: np.ndarray) -> dict[str, float]:
    return {"roc_auc": float(roc_auc_score(y, probability)),
            "pr_auc": float(average_precision_score(y, probability)),
            "brier": calibration.brier(probability, y),
            "ece": calibration.expected_calibration_error(probability, y)}


def _ablation(frame: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame, rounds: int) -> list[dict[str, Any]]:
    """Accuracy and group gaps with every feature scored versus the behavioural features only,
    averaged over several cross-validation splits (one split is too noisy for groups of 25-150)."""
    variants = [("All 12 features scored", FEATURES), ("Behaviour only (the model)", SCORED)]
    out = []
    for name, features in variants:
        x = frame[[f.name for f in features]].to_numpy(dtype=float)
        rows = []
        for seed in ABLATION_SEEDS:
            raw = _out_of_fold(x, y, rounds, seed, features)
            probability = calibration.apply_platt(raw, *calibration.fit_platt(raw, y))
            report = fairness.report(meta, y, probability, APPROVE_BELOW)
            rows.append([roc_auc_score(y, raw), calibration.brier(probability, y)]
                        + [report[a][k] for a in ("shop_type", "urbanicity")
                           for k in ("approval_ratio_min_max", "good_payer_approval_gap")])
        mean = np.array(rows, dtype=float).mean(axis=0)
        out.append({"variant": name, "features": len(features), "roc_auc": mean[0], "brier": mean[1],
                    "shop_type_approval_ratio": mean[2], "shop_type_good_payer_gap": mean[3],
                    "urbanicity_approval_ratio": mean[4], "urbanicity_good_payer_gap": mean[5]})
    return out


def train(shops: pd.DataFrame, daily: pd.DataFrame, *, seed: int = 42,
          ablation: bool = True) -> tuple[CreditModel, dict[str, Any]]:
    frame, y, meta = build_dataset(shops, daily)
    scored = [f.name for f in SCORED]
    x = frame[scored].to_numpy(dtype=float)

    # Pick the number of boosting rounds by out-of-fold log loss.
    by_rounds = {r: log_loss(y, 1 / (1 + np.exp(-_out_of_fold(x, y, r, seed)))) for r in ROUNDS}
    rounds = min(by_rounds, key=by_rounds.get)

    # Out-of-fold scores: the honest estimate of performance, and what calibration is fitted on.
    raw_oof = _out_of_fold(x, y, rounds, seed)
    platt = calibration.fit_platt(raw_oof, y)
    calibrated = calibration.apply_platt(raw_oof, *platt)
    uncalibrated = 1 / (1 + np.exp(-raw_oof))
    repeats = [roc_auc_score(y, _out_of_fold(x, y, rounds, s)) for s in ABLATION_SEEDS]
    baseline = _baseline_oof(x, y, seed)

    booster = lgb.train(params(seed), lgb.Dataset(x, y), num_boost_round=rounds)
    peers = {name: {"p25": float(frame[name].quantile(0.25)), "median": float(frame[name].median()),
                    "p75": float(frame[name].quantile(0.75))} for name in NAMES}
    model = CreditModel(booster=booster, platt=platt, features=scored, peers=peers, trained_on={
        "shops": int(len(y)), "default_rate": float(y.mean()), "rounds": rounds, "trained": date.today().isoformat()})

    # SHAP check: LightGBM's TreeSHAP against the reference implementation.
    import shap  # noqa: PLC0415  (slow import; only needed here)

    reference = shap.TreeExplainer(booster).shap_values(x)
    reference = np.asarray(reference[1] if isinstance(reference, list) else reference)
    own = booster.predict(x, pred_contrib=True)[:, :-1]
    importance = np.abs(own).mean(axis=0)

    approved = calibrated < APPROVE_BELOW
    lowers = [0.0] + [upper for _, upper, _ in BANDS[:-1]]
    bands = {letter: int(((calibrated >= lower) & (calibrated < upper)).sum())
             for (letter, upper, _), lower in zip(BANDS, lowers, strict=True)}
    metrics = {
        "model": MODEL_VERSION,
        "shops": int(len(y)),
        "default_rate": float(y.mean()),
        "features_scored": scored,
        "features_shown_not_scored": [f.name for f in FEATURES if not f.scored],
        "rounds": rounds,
        "log_loss_by_rounds": {str(k): float(v) for k, v in by_rounds.items()},
        "oof": _summary(calibrated, y),
        "oof_uncalibrated": _summary(uncalibrated, y),
        "oof_roc_auc_repeats": [float(v) for v in repeats],
        "baseline_logistic": _summary(baseline, y),
        "true_pd": {"mae": float(np.mean(np.abs(calibrated - meta["true_pd"].to_numpy()))),
                    "correlation": float(np.corrcoef(calibrated, meta["true_pd"].to_numpy())[0, 1]),
                    "oracle_roc_auc": float(roc_auc_score(y, meta["true_pd"].to_numpy()))},
        "reliability": calibration.reliability(calibrated, y),
        "platt": list(platt),
        "bands": bands,
        "approval": {"approve_below": APPROVE_BELOW, "approval_rate": float(approved.mean()),
                     "default_rate_approved": float(y[approved].mean()) if approved.any() else None,
                     "default_rate_declined": float(y[~approved].mean()) if (~approved).any() else None},
        "fairness": fairness.report(meta, y, calibrated, APPROVE_BELOW),
        "ablation": _ablation(frame, y, meta, rounds) if ablation else [],
        "feature_importance": {name: float(v) for name, v in sorted(zip(scored, importance, strict=True),
                                                                    key=lambda p: -p[1])},
        "shap_check_max_abs_diff": float(np.abs(reference.reshape(own.shape) - own).max()),
        "peers": peers,
    }
    return model, metrics
