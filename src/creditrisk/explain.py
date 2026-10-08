"""SHAP attributions.

IMPORTANT: SHAP values describe how the MODEL arrives at a score (attribution of the model's output to its inputs,
on the log-odds scale of the uncalibrated model). They are NOT causal effects: a large attribution for
`ext_source_mean` does not mean changing that input would change the applicant's real default risk.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .modeling import IsotonicCalibratedModel, LGBMPipeline

CAVEAT = ("SHAP explains model attribution on the log-odds scale of the underlying model; "
          "it does not describe causal effects.")


def _base(model):
    return model.base if isinstance(model, IsotonicCalibratedModel) else model


def contributions(model, X: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    """Returns (n_rows x n_transformed_features contributions, transformed feature names)."""
    m = _base(model)
    if isinstance(m, LGBMPipeline):
        import shap
        Xt = m.pre_.transform(X)
        sv = shap.TreeExplainer(m.clf_).shap_values(Xt)
        sv = sv[1] if isinstance(sv, list) else sv
        return np.asarray(sv), m.feature_names_out()
    # logistic regression baseline: contribution = coef_j * standardized x_j (features centred by StandardScaler)
    pre, clf = m.named_steps["pre"], m.named_steps["clf"]
    Xt = pre.transform(X)
    return Xt * clf.coef_[0], list(pre.get_feature_names_out())


def top_k(model, X_row: pd.DataFrame, k: int = 5) -> list[dict]:
    sv, names = contributions(model, X_row)
    order = np.argsort(-np.abs(sv[0]))[:k]
    return [{"feature": names[i], "contribution_logodds": float(sv[0][i])} for i in order]


def global_importance(model, X: pd.DataFrame, n: int = 2000, seed: int = 42) -> pd.DataFrame:
    sample = X.sample(min(n, len(X)), random_state=seed)
    sv, names = contributions(model, sample)
    return (pd.DataFrame({"feature": names, "mean_abs_shap": np.abs(sv).mean(0)})
            .sort_values("mean_abs_shap", ascending=False).reset_index(drop=True))
