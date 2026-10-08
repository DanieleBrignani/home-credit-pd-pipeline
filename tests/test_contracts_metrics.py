import numpy as np
import pandas as pd
import pytest

from creditrisk import ingest, metrics, monitoring
from creditrisk.contracts import CONTRACTS


# ---------------- source contracts (no database needed)
def _write_min(tmp_path, name="bureau_balance", cols=None):
    c = CONTRACTS[name]
    cols = cols or list(c.required)
    pd.DataFrame([[1] * len(cols)], columns=cols).to_csv(tmp_path / c.file, index=False)
    return c


def test_missing_file_fails(tmp_path):
    with pytest.raises(ingest.ContractViolation, match="missing file"):
        ingest.validate_file(CONTRACTS["bureau"], tmp_path)


def test_missing_required_column_fails(tmp_path):
    c = _write_min(tmp_path, cols=["SK_ID_BUREAU", "STATUS"])
    with pytest.raises(ingest.ContractViolation, match="MONTHS_BALANCE"):
        ingest.validate_file(c, tmp_path)


def test_additive_column_is_reported_not_fatal(tmp_path):
    c = _write_min(tmp_path, cols=list(CONTRACTS["bureau_balance"].required) + ["NEW_COL"])
    info = ingest.validate_file(c, tmp_path)
    assert info["added_columns"] == ["NEW_COL"]


def test_duplicate_header_fails(tmp_path):
    c = CONTRACTS["bureau_balance"]
    (tmp_path / c.file).write_text("SK_ID_BUREAU,MONTHS_BALANCE,STATUS,STATUS\n1,0,C,C\n")
    with pytest.raises(ingest.ContractViolation, match="duplicate"):
        ingest.validate_file(c, tmp_path)


def test_manifest_locks_first_seen_hash_and_detects_mutation(tmp_path):
    c = _write_min(tmp_path)
    first = ingest.validate_file(c, tmp_path)
    assert ingest.read_manifest(tmp_path / "MANIFEST.sha256")[c.file] == first["sha256"]
    ingest.validate_file(c, tmp_path)                       # unchanged file passes again (idempotent)
    (tmp_path / c.file).write_text((tmp_path / c.file).read_text() + "9,9,9\n")
    with pytest.raises(ingest.ContractViolation, match="immutable"):
        ingest.validate_file(c, tmp_path)


def test_every_contract_has_grain_and_pk():
    for c in CONTRACTS.values():
        assert c.grain and c.pk


# ---------------- metrics
def test_core_metrics_perfect_and_random():
    y = np.array([0] * 90 + [1] * 10)
    perfect = np.where(y == 1, 0.9, 0.1)
    m = metrics.core_metrics(y, perfect)
    assert m["roc_auc"] == 1.0 and m["ks"] == 1.0 and m["pr_auc"] == 1.0
    assert "accuracy" not in m
    rng = np.random.default_rng(0)
    r = metrics.core_metrics(rng.binomial(1, .08, 20000), rng.random(20000))
    assert 0.47 < r["roc_auc"] < 0.53 and r["pr_auc"] < 0.12


def test_brier_baseline_and_slope_for_calibrated_scores():
    rng = np.random.default_rng(1)
    p = rng.beta(1, 11, 50000)
    y = rng.binomial(1, p)
    m = metrics.core_metrics(y, p)
    assert abs(m["calibration_slope"] - 1) < 0.1 and abs(m["calibration_intercept"]) < 0.1
    assert m["brier"] < m["brier_baseline_prevalence"]


def test_threshold_table_precision_recall_consistent():
    y = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
    p = np.array([.9, .4, .8, .3, .2, .2, .1, .1, .05, .01])
    t = metrics.threshold_metrics(y, p, 0.5)
    assert t["precision"] == 0.5 and t["recall"] == 0.5 and t["flag_rate"] == 0.2


def test_thresholds_chosen_on_validation_only_are_deterministic():
    rng = np.random.default_rng(2)
    p = rng.random(5000); y = rng.binomial(1, p * .3)
    assert metrics.pick_thresholds(y, p) == metrics.pick_thresholds(y, p)


def test_decile_table_decile1_is_highest_risk():
    rng = np.random.default_rng(3)
    p = rng.random(10000); y = rng.binomial(1, p)
    d = metrics.decile_table(y, p)
    assert d.loc[d.decile == 1, "observed_rate"].iloc[0] > d.loc[d.decile == 10, "observed_rate"].iloc[0]


# ---------------- PSI
def test_psi_identical_is_zero_and_shift_detected():
    rng = np.random.default_rng(4)
    a = rng.normal(0, 1, 20000)
    assert monitoring.psi(a, a) == pytest.approx(0, abs=1e-6)
    assert monitoring.psi(a, rng.normal(0, 1, 20000)) < 0.1
    assert monitoring.psi(a, rng.normal(1.0, 1, 20000)) > 0.25


def test_psi_sees_missingness_shift():
    rng = np.random.default_rng(5)
    a = rng.normal(0, 1, 10000); b = a.copy(); b[:3000] = np.nan
    assert monitoring.psi(a, b) > 0.1


def test_psi_status_thresholds():
    assert monitoring.psi_status(0.05) == "stable"
    assert monitoring.psi_status(0.15) == "moderate_shift"
    assert monitoring.psi_status(0.3) == "major_shift"
