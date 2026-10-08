"""Model objects. Kept in an importable module so pickled MLflow models load in the API container."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .features import CATEGORICAL_FEATURES, NUMERIC_FEATURES


def _ohe() -> OneHotEncoder:
    return OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=30, sparse_output=False)


def make_preprocessor(scale: bool) -> ColumnTransformer:
    """scale=True for the linear baseline (median impute + missing indicators + standardise)."""
    num_steps = [("impute", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True))]
    if scale:
        num_steps.append(("scale", StandardScaler()))
    cat_steps = [("impute", SimpleImputer(strategy="constant", fill_value="__missing__")), ("ohe", _ohe())]
    return ColumnTransformer([("num", Pipeline(num_steps), NUMERIC_FEATURES),
                              ("cat", Pipeline(cat_steps), CATEGORICAL_FEATURES)])


def make_logreg(C: float = 0.1, random_state: int = 42) -> Pipeline:
    clf = LogisticRegression(C=C, max_iter=500, solver="lbfgs", random_state=random_state)
    return Pipeline([("pre", make_preprocessor(scale=True)), ("clf", clf)])


class IsotonicCalibratedModel(BaseEstimator, ClassifierMixin):
    """Wraps a fitted model; maps its scores through isotonic regression fitted on held-out VALIDATION data."""

    def __init__(self, base, iso: IsotonicRegression):
        self.base, self.iso = base, iso
        self.classes_ = np.array([0, 1])

    def predict_proba(self, X):
        p = self.iso.predict(self.base.predict_proba(X)[:, 1])
        p = np.clip(p, 1e-6, 1 - 1e-6)
        return np.column_stack([1 - p, p])

    def __getattr__(self, item):  # expose base attrs (e.g. named_steps) for SHAP
        if item in ("base", "iso") or item.startswith("__"):
            raise AttributeError(item)
        return getattr(self.base, item)


class LGBMPipeline(BaseEstimator, ClassifierMixin):
    """preprocessor + LightGBM. Early stopping uses a validation fold passed to fit()."""

    def __init__(self, params: dict | None = None):
        self.params = params or {}
        self.classes_ = np.array([0, 1])

    def fit(self, X, y, X_valid=None, y_valid=None):
        import lightgbm as lgb
        self.pre_ = make_preprocessor(scale=False).fit(X)
        Xt = self.pre_.transform(X)
        params = dict(n_estimators=2000, learning_rate=0.03, num_leaves=31, min_child_samples=100,
                      subsample=0.8, subsample_freq=1, colsample_bytree=0.6, reg_lambda=5.0,
                      random_state=42, n_jobs=4, verbose=-1, deterministic=True, force_row_wise=True)
        params.update(self.params)
        self.clf_ = lgb.LGBMClassifier(**params)
        if X_valid is not None:
            self.clf_.fit(Xt, y, eval_set=[(self.pre_.transform(X_valid), y_valid)], eval_metric="auc",
                          callbacks=[lgb.early_stopping(100, verbose=False)])
        else:
            self.clf_.fit(Xt, y)
        self.best_iteration_ = int(self.clf_.best_iteration_ or self.clf_.n_estimators)
        return self

    def predict_proba(self, X):
        return self.clf_.predict_proba(self.pre_.transform(X), num_iteration=self.best_iteration_)

    def feature_names_out(self):
        return list(self.pre_.get_feature_names_out())


def as_frame(X) -> pd.DataFrame:
    return X if isinstance(X, pd.DataFrame) else pd.DataFrame(X)
