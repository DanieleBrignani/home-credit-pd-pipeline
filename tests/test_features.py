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


# ---------------- vectorised build_features must be bit-identical to the reference implementation
def _random_frame(n=400, seed=0):
    import numpy as np
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        "amt_income_total": rng.lognormal(12, .6, n), "amt_credit": rng.lognormal(13, .6, n), "amt_annuity": rng.lognormal(9.5, .5, n),
        "amt_goods_price": rng.lognormal(12.8, .6, n), "days_birth": -rng.integers(7000, 25000, n),
        "days_employed": -rng.integers(0, 15000, n).astype(float), "cnt_fam_members": rng.integers(0, 6, n).astype(float),
        "ext_source_1": rng.random(n), "ext_source_2": rng.random(n), "ext_source_3": rng.random(n),
        "name_contract_type": rng.choice(["Cash loans", "Revolving loans", None], n),
        "occupation_type": rng.choice(["Laborers", None], n),
        "bureau_n_credits": rng.integers(0, 9, n).astype(float), "bureau_sum_debt": rng.lognormal(10, 1, n),
        "inst_n_records": rng.integers(0, 90, n).astype(float), "inst_late_share": rng.random(n),
    })
    for col in ("ext_source_1", "ext_source_3", "amt_annuity", "bureau_n_credits", "inst_late_share", "days_employed"):
        df.loc[rng.random(n) < .25, col] = np.nan
    df.loc[0, "amt_income_total"] = 0.0           # zero denominator
    df.loc[1, "amt_annuity"] = -5.0               # negative denominator
    df.loc[2, "days_birth"] = 0                   # zero age denominator
    df.loc[3, "amt_credit"] = 1e308; df.loc[3, "amt_income_total"] = 1e-300   # overflow to inf
    df.loc[4, ["ext_source_1", "ext_source_2", "ext_source_3"]] = np.nan      # all external scores missing
    df["inst_n_records"] = df["inst_n_records"].astype(object); df.loc[5, "inst_n_records"] = "12"   # numeric string
    df["amt_goods_price"] = df["amt_goods_price"].astype(object)
    df.loc[6, "amt_goods_price"] = "bad"           # unparsable -> NaN
    return df


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_vectorised_build_features_is_bit_identical_to_reference(seed):
    from tests.reference_features import build_features_reference
    df = _random_frame(seed=seed)
    pd.testing.assert_frame_equal(build_features(df), build_features_reference(df), check_exact=True)


def test_vectorised_build_features_identical_on_minimal_and_single_row_inputs():
    from tests.reference_features import build_features_reference
    for df in (pd.DataFrame({"amt_income_total": [1.0], "amt_credit": [2.0], "days_birth": [-9000]}), _row(), _random_frame(1)):
        pd.testing.assert_frame_equal(build_features(df), build_features_reference(df), check_exact=True)
