import numpy as np
import pandas as pd
import pytest

from creditrisk import config, features
from creditrisk.features import FEATURE_COLUMNS, FEATURE_REGISTRY, build_features


def _row(**kw):
    base = dict(amt_income_total=100000., amt_credit=300000., amt_annuity=15000., amt_goods_price=250000.,
                days_birth=-12000, days_employed=-2000, cnt_fam_members=2., ext_source_1=0.3, ext_source_2=0.6,
                ext_source_3=np.nan, name_contract_type="Cash loans")
    base.update(kw)
    return pd.DataFrame([base])


def test_registry_names_unique_and_complete():
    names = [f.name for f in FEATURE_REGISTRY]
    assert len(names) == len(set(names))
    assert FEATURE_COLUMNS == features.NUMERIC_FEATURES + features.CATEGORICAL_FEATURES
    for f in FEATURE_REGISTRY:  # every feature documents source/transformation/availability
        assert f.source and f.transformation and f.availability


def test_output_schema_is_exact_and_ordered():
    out = build_features(_row())
    assert list(out.columns) == FEATURE_COLUMNS
    assert len(out) == 1


def test_ratios():
    out = build_features(_row()).iloc[0]
    assert out["credit_income_ratio"] == pytest.approx(3.0)
    assert out["annuity_income_ratio"] == pytest.approx(0.15)
    assert out["credit_term_months"] == pytest.approx(20.0)
    assert out["credit_goods_ratio"] == pytest.approx(1.2)
    assert out["income_per_family_member"] == pytest.approx(50000.)
    assert out["employed_to_age_ratio"] == pytest.approx(2000 / 12000)
    assert out["ext_source_mean"] == pytest.approx(0.45)
    assert out["ext_source_min"] == pytest.approx(0.3)


@pytest.mark.parametrize("col", ["amt_income_total", "amt_annuity", "amt_goods_price"])
def test_zero_denominator_gives_nan_not_inf(col):
    out = build_features(_row(**{col: 0.0}))
    assert not np.isinf(out.select_dtypes("number").to_numpy()).any()


def test_missing_history_means_zero_counts_and_flags_zero():
    out = build_features(_row()).iloc[0]
    for c in features.ZERO_FILL:
        assert out[c] == 0.0
    for k in ("bureau", "prev", "inst", "cc", "pos"):
        assert out[f"has_{k}_history"] == 0.0
    assert np.isnan(out["inst_late_share"])  # a ratio over nothing stays unknown


def test_history_present_sets_flag():
    out = build_features(_row(bureau_n_credits=3, inst_n_records=12, inst_late_share=0.25)).iloc[0]
    assert out["has_bureau_history"] == 1.0 and out["has_inst_history"] == 1.0 and out["has_cc_history"] == 0.0
    assert out["inst_late_share"] == 0.25


def test_target_and_ids_never_leak_into_output():
    out = build_features(_row(target=1, sk_id_curr=5, split="test"))
    assert {"target", "sk_id_curr", "split"}.isdisjoint(out.columns)


def test_deterministic():
    a, b = build_features(_row()), build_features(_row())
    pd.testing.assert_frame_equal(a, b)


def test_schema_version_changes_with_pipeline_version(monkeypatch):
    v1 = features.feature_schema_version()
    monkeypatch.setattr(config, "FEATURE_PIPELINE_VERSION", "fp-9.9.9")
    assert features.feature_schema_version() != v1


def test_feature_counts_consistent():
    c = features.feature_counts()
    assert c["total_model_input_columns"] == len(FEATURE_COLUMNS)
    assert c["raw_application_columns"] + c["history_aggregates_from_dbt"] + c["derived_in_python"] == len(FEATURE_COLUMNS)
