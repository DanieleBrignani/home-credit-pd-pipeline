"""Monitoring primitives: PSI, missingness, and Prometheus instruments.

What this module can and cannot claim (also in README "Monitoring"):
  * Input drift (PSI on features / score distribution), missingness, latency, errors, model version are computable
    from live traffic -> implemented here and exposed at /metrics.
  * Performance / concept drift needs realised outcomes that arrive months after scoring. This dataset is static and
    has no application timestamps, so NO production drift has been observed. We only (a) check train-vs-test PSI as a
    sanity baseline and (b) inject synthetic shifts to prove the alarms fire (scripts/drift_simulation.py).
"""
from __future__ import annotations

import numpy as np
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

# Features monitored through fixed-bucket histograms (reference buckets come from the training split).
MONITORED_FEATURES = ["ext_source_mean", "credit_income_ratio", "annuity_income_ratio", "credit_term_months"]
PD_BUCKETS = (0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0)
FEATURE_BUCKETS = {
    "ext_source_mean": tuple(np.round(np.linspace(0.1, 0.9, 9), 2)),
    "credit_income_ratio": (1, 2, 3, 4, 5, 6, 8, 10, 15, 25),
    "annuity_income_ratio": (0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.6, 1.0),
    "credit_term_months": (6, 12, 18, 24, 36, 48, 60, 90),
}


def psi(expected, actual, bins: int | np.ndarray = 10, eps: float = 1e-4) -> float:
    """Population Stability Index with quantile bins from `expected`. Rule of thumb: <0.1 stable, 0.1-0.25 moderate,
    >0.25 major shift. NaNs are their own bin so missingness shifts register."""
    e, a = np.asarray(expected, float), np.asarray(actual, float)
    e_nan, a_nan = np.isnan(e).mean(), np.isnan(a).mean()
    e, a = e[~np.isnan(e)], a[~np.isnan(a)]
    if isinstance(bins, int):
        edges = np.unique(np.quantile(e, np.linspace(0, 1, bins + 1)))
    else:
        edges = np.asarray(bins, float)
    edges[0], edges[-1] = -np.inf, np.inf
    pe = np.histogram(e, edges)[0] / max(len(e), 1) * (1 - e_nan)
    pa = np.histogram(a, edges)[0] / max(len(a), 1) * (1 - a_nan)
    pe, pa = np.append(pe, e_nan), np.append(pa, a_nan)
    pe, pa = np.clip(pe, eps, None), np.clip(pa, eps, None)
    return float(np.sum((pa - pe) * np.log(pa / pe)))


def psi_from_counts(expected_counts, actual_counts, eps: float = 1e-4) -> float:
    pe = np.asarray(expected_counts, float); pa = np.asarray(actual_counts, float)
    pe, pa = pe / max(pe.sum(), 1), pa / max(pa.sum(), 1)
    pe, pa = np.clip(pe, eps, None), np.clip(pa, eps, None)
    return float(np.sum((pa - pe) * np.log(pa / pe)))


def psi_status(v: float) -> str:
    return "stable" if v < 0.1 else "moderate_shift" if v < 0.25 else "major_shift"


class Metrics:
    """All Prometheus instruments; one instance per app (own registry => no cross-test pollution)."""

    def __init__(self):
        r = self.registry = CollectorRegistry()
        self.requests = Counter("pd_api_requests_total", "Requests by endpoint and status", ["endpoint", "status"], registry=r)
        self.errors = Counter("pd_api_errors_total", "Errors by type", ["type"], registry=r)
        self.latency = Histogram("pd_api_request_seconds", "End-to-end /predict latency",
                                 buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5), registry=r)
        self.pd = Histogram("pd_prediction_probability", "Predicted PD distribution", buckets=PD_BUCKETS, registry=r)
        self.feature_missing = Counter("pd_feature_missing_total", "Missing input values by feature", ["feature"], registry=r)
        self.feature_seen = Counter("pd_feature_observed_total", "Scored rows by feature", ["feature"], registry=r)
        self.feature_hist = {f: Histogram(f"pd_feature_{f}", f"Distribution of {f}", buckets=FEATURE_BUCKETS[f], registry=r)
                             for f in MONITORED_FEATURES}
        self.history_miss = Counter("pd_history_lookup_miss_total", "Applicants with no history-feature row", registry=r)
        self.model_info = Gauge("pd_model_info", "Active model (value 1)", ["version", "family", "feature_schema"], registry=r)
        self.model_loaded_ts = Gauge("pd_model_loaded_timestamp_seconds", "Unix time the active model was loaded", registry=r)

    def observe_features(self, X_row) -> None:
        for col, val in X_row.items():
            self.feature_seen.labels(col).inc()
            if val is None or (isinstance(val, float) and np.isnan(val)):
                self.feature_missing.labels(col).inc()
        for f in MONITORED_FEATURES:
            v = X_row.get(f)
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                self.feature_hist[f].observe(float(v))
