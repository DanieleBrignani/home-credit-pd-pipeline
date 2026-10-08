"""Evaluation metrics. Accuracy is intentionally NOT computed: with ~8% positives a constant 'no default'
classifier scores ~92% accuracy, so it carries no information (see README, Imbalance)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import average_precision_score, brier_score_loss, precision_score, recall_score, roc_auc_score
from sklearn.metrics import roc_curve


def ks_statistic(y, p) -> float:
    fpr, tpr, _ = roc_curve(y, p)
    return float(np.max(tpr - fpr))


def calibration_slope_intercept(y, p) -> tuple[float, float]:
    """Logistic regression of y on logit(p): ideal slope 1, intercept 0."""
    from sklearn.linear_model import LogisticRegression
    z = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6))).reshape(-1, 1)
    lr = LogisticRegression(C=1e6, max_iter=200).fit(z, y)
    return float(lr.coef_[0][0]), float(lr.intercept_[0])


def core_metrics(y, p) -> dict:
    y, p = np.asarray(y), np.asarray(p)
    slope, intercept = calibration_slope_intercept(y, p)
    return {
        "roc_auc": float(roc_auc_score(y, p)),
        "pr_auc": float(average_precision_score(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "brier_baseline_prevalence": float(brier_score_loss(y, np.full_like(p, y.mean(), dtype=float))),
        "ks": ks_statistic(y, p),
        "calibration_slope": slope, "calibration_intercept": intercept,
        "prevalence": float(y.mean()), "n": int(len(y)),
    }


def threshold_metrics(y, p, threshold: float) -> dict:
    y, p = np.asarray(y), np.asarray(p)
    pred = (p >= threshold).astype(int)
    return {"threshold": float(threshold), "flag_rate": float(pred.mean()),
            "precision": float(precision_score(y, pred, zero_division=0)),
            "recall": float(recall_score(y, pred, zero_division=0))}


def pick_thresholds(y_valid, p_valid) -> dict[str, float]:
    """Thresholds are chosen on VALIDATION only and then frozen for test evaluation."""
    y, p = np.asarray(y_valid), np.asarray(p_valid)
    out = {f"top_{int(q * 100)}pct": float(np.quantile(p, 1 - q)) for q in (0.05, 0.10, 0.20)}
    best, best_t = -1.0, 0.5
    for t in np.unique(np.quantile(p, np.linspace(0.5, 0.995, 200))):
        pred = p >= t
        tp = (pred & (y == 1)).sum()
        prec = tp / max(pred.sum(), 1)
        rec = tp / max((y == 1).sum(), 1)
        f2 = 5 * prec * rec / max(4 * prec + rec, 1e-12)  # recall-weighted: missing a default costs more
        if f2 > best:
            best, best_t = f2, float(t)
    out["f2_optimal"] = best_t
    return out


def threshold_table(y, p, thresholds: dict[str, float]) -> pd.DataFrame:
    rows = [{"rule": k, **threshold_metrics(y, p, t)} for k, t in thresholds.items()]
    return pd.DataFrame(rows)


def decile_table(y, p, n=10) -> pd.DataFrame:
    df = pd.DataFrame({"y": np.asarray(y), "p": np.asarray(p)})
    df["decile"] = pd.qcut(df["p"].rank(method="first"), n, labels=False) + 1
    g = df.groupby("decile").agg(n=("y", "size"), mean_pred=("p", "mean"), observed_rate=("y", "mean")).reset_index()
    g["decile"] = n + 1 - g["decile"]  # decile 1 = highest risk
    return g.sort_values("decile").reset_index(drop=True)


def calibration_bins(y, p, n_bins=10) -> pd.DataFrame:
    frac, mean_p = calibration_curve(y, p, n_bins=n_bins, strategy="quantile")
    return pd.DataFrame({"mean_predicted": mean_p, "observed_rate": frac})
