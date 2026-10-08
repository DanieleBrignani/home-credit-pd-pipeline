"""Leakage controls: forbidden / post-outcome fields must not be able to reach the model matrix."""
import inspect
import re
from pathlib import Path

import pandas as pd
import pytest

from creditrisk import features, train
from creditrisk.contracts import FORBIDDEN_FEATURE_COLUMNS
from creditrisk.features import FEATURE_COLUMNS, assert_no_forbidden

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "dbt" / "models" / "staging"


def test_forbidden_columns_not_in_feature_set():
    lowered = {c.lower() for c in FEATURE_COLUMNS}
    assert lowered.isdisjoint({c.lower() for c in FORBIDDEN_FEATURE_COLUMNS})


@pytest.mark.parametrize("bad", ["TARGET", "target", "DAYS_TERMINATION", "days_last_due", "DAYS_FIRST_DRAWING",
                                 "DAYS_ENDDATE_FACT", "CODE_GENDER", "SK_ID_CURR"])
def test_assert_no_forbidden_raises(bad):
    with pytest.raises(ValueError):
        assert_no_forbidden(FEATURE_COLUMNS + [bad])


def test_build_features_ignores_injected_forbidden_columns():
    df = pd.DataFrame([{"amt_income_total": 1., "amt_credit": 1., "days_birth": -1, "target": 1,
                        "days_termination": -5, "days_last_due": 10, "code_gender": "F"}])
    out = features.build_features(df)
    assert_no_forbidden(out.columns)


@pytest.mark.parametrize("col", ["DAYS_FIRST_DRAWING", "DAYS_FIRST_DUE", "DAYS_LAST_DUE_1ST_VERSION", "DAYS_LAST_DUE",
                                 "DAYS_TERMINATION", "DAYS_ENDDATE_FACT", "CODE_GENDER"])
def test_forbidden_source_fields_never_selected_in_staging(col):
    """Static scan: the SQL layer cannot even read these raw columns into staging."""
    for sql in STAGING.glob("*.sql"):
        code = "\n".join(l for l in sql.read_text().splitlines() if not l.strip().startswith("--"))
        assert not re.search(rf'"{col}"', code), f"{col} selected in {sql.name}"


def test_target_only_in_application_staging():
    for sql in STAGING.glob("*.sql"):
        code = "\n".join(l for l in sql.read_text().splitlines() if not l.strip().startswith("--"))
        if sql.name != "stg_application.sql":
            assert '"TARGET"' not in code


def test_every_history_sql_has_temporal_guard():
    guards = {"int_bureau_agg.sql": "days_credit <= 0", "int_previous_agg.sql": "days_decision < 0",
              "int_installments_agg.sql": "days_instalment <= 0", "int_credit_card_agg.sql": "months_balance < 0",
              "int_pos_cash_agg.sql": "months_balance < 0"}
    for f, g in guards.items():
        assert g in (ROOT / "dbt" / "models" / "intermediate" / f).read_text(), f"missing guard in {f}"


def test_selection_code_cannot_receive_test_split():
    """fit_and_select reads only the train/valid keys; passing frames without 'test' must work."""
    src = inspect.getsource(train.fit_and_select)
    assert '"test"' not in src and "'test'" not in src


def test_lineage_doc_covers_every_feature():
    doc = (ROOT / "docs" / "feature_lineage.md").read_text()
    missing = [f.name for f in features.FEATURE_REGISTRY if f"`{f.name}`" not in doc]
    assert not missing, f"features missing from docs/feature_lineage.md: {missing}"


def test_dbt_pipeline_version_matches_config():
    from creditrisk import config
    yml = (ROOT / "dbt" / "dbt_project.yml").read_text()
    assert f'feature_pipeline_version: "{config.FEATURE_PIPELINE_VERSION}"' in yml
