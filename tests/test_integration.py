"""Integration tests: raw -> features, features -> trained model, registry -> inference.
Need Postgres (docker compose up -d postgres); they use a separate database (creditrisk_test) filled with TEST-ONLY fixtures."""
import json
import os

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.db


def _q(sql, params=None):
    from creditrisk import data
    with data.connect() as c:
        return pd.read_sql(sql, c, params=params)


# ---------------------------------------------------------------- raw -> features
def test_raw_loaded_verbatim_and_lineage_recorded(built_db, fixture_tables):
    tables, _ = fixture_tables
    n = _q("select count(*) n from raw.application_train").n[0]
    assert n == len(tables["application_train"])
    b = _q("select * from meta.ingest_batches where table_name='bureau' order by loaded_at desc limit 1").iloc[0]
    assert len(b.sha256) == 64 and b.row_count == len(tables["bureau"]) and b.git_commit


def test_ingest_is_idempotent(built_db, fixture_tables):
    from creditrisk import ingest
    _, raw_dir = fixture_tables
    before = _q("select count(*) n from meta.ingest_batches").n[0]
    assert ingest.main(["--raw-dir", str(raw_dir), "--allow-rowcount-mismatch"]) == 0
    assert _q("select count(*) n from meta.ingest_batches").n[0] == before


def test_training_grain_is_one_row_per_applicant(built_db, fixture_tables):
    tables, _ = fixture_tables
    df = _q("select sk_id_curr, split from marts.training_dataset")
    assert df.sk_id_curr.is_unique and len(df) == len(tables["application_train"])  # no join fan-out
    assert set(df.split) == {"train", "valid", "test"}
    frac = df.split.value_counts(normalize=True)
    assert abs(frac["train"] - .70) < .04 and abs(frac["valid"] - .15) < .04


def test_split_is_deterministic_hash_of_id(built_db):
    a = _q("select sk_id_curr, split from marts.training_dataset order by 1")
    b = _q("select sk_id_curr, case when (('x'||substr(md5(sk_id_curr::text||'hc-split-v1'),1,8))::bit(32)::bigint % 100) < 70 then 'train' "
           "when (('x'||substr(md5(sk_id_curr::text||'hc-split-v1'),1,8))::bit(32)::bigint % 100) < 85 then 'valid' else 'test' end split "
           "from marts.training_dataset order by 1")
    pd.testing.assert_frame_equal(a, b)


def test_training_table_has_no_forbidden_columns(built_db):
    from creditrisk.contracts import FORBIDDEN_FEATURE_COLUMNS
    cols = set(_q("select * from marts.training_dataset limit 1").columns)
    allowed_meta = {"target", "sk_id_curr", "split", "bucket"}
    leaked = {c for c in cols - allowed_meta if c.upper() in FORBIDDEN_FEATURE_COLUMNS}
    assert not leaked


def test_dbt_installments_match_independent_pandas_implementation(built_db, fixture_tables):
    """Second implementation of the temporal guard + aggregation, compared with the dbt SQL output."""
    tables, _ = fixture_tables
    i = tables["installments_payments"]
    i = i[(i.DAYS_INSTALMENT <= 0) & (i.DAYS_ENTRY_PAYMENT.isna() | (i.DAYS_ENTRY_PAYMENT <= 0))].copy()
    i["late"] = i.DAYS_ENTRY_PAYMENT - i.DAYS_INSTALMENT
    exp = i.groupby("SK_ID_CURR").agg(inst_n_records=("late", "size"), inst_late_share=("late", lambda s: (s.dropna() > 0).mean()),
                                      inst_max_days_late=("late", "max")).reset_index()
    got = _q("select sk_id_curr, inst_n_records, inst_late_share, inst_max_days_late from intermediate.int_installments_agg")
    m = exp.merge(got, left_on="SK_ID_CURR", right_on="sk_id_curr", suffixes=("_pd", "_sql"))
    assert len(m) == len(exp) == len(got)
    assert (m.inst_n_records_pd == m.inst_n_records_sql).all()
    np.testing.assert_allclose(m.inst_late_share_pd, m.inst_late_share_sql.astype(float), atol=1e-9)
    np.testing.assert_allclose(m.inst_max_days_late_pd, m.inst_max_days_late_sql.astype(float))


def test_future_dated_trap_rows_are_excluded(built_db, fixture_tables):
    tables, _ = fixture_tables
    prev = tables["previous_application"]
    trap_cid = prev[prev.DAYS_DECISION > 0].SK_ID_CURR.iloc[0]
    exp = int(((prev.SK_ID_CURR == trap_cid) & (prev.DAYS_DECISION < 0)).sum())
    got = _q("select prev_n_applications from intermediate.int_previous_agg where sk_id_curr=%s", (int(trap_cid),))
    assert (got.prev_n_applications[0] if len(got) else 0) == exp
    bb = _q("select max(months_balance) m from staging.stg_bureau_balance").m[0]
    assert bb > 0  # the trap exists in staging ...
    cc_rows = _q("select count(*) n from intermediate.int_credit_card_agg").n[0]
    assert cc_rows > 0  # ... and the guard (months_balance < 0) is applied in the intermediate layer (test_leakage)


def test_employed_sentinel_becomes_null_and_flagged(built_db):
    d = _q("select count(*) filter (where flag_employed_sentinel=1) f, count(*) filter (where flag_employed_sentinel=1 and days_employed is not null) bad "
           "from staging.stg_application")
    assert d.f[0] > 0 and d.bad[0] == 0


def test_feature_rebuild_is_bit_identical(built_db):
    """Same raw -> same features: two full dbt rebuilds give the same content fingerprint."""
    import subprocess, sys
    from pathlib import Path
    from creditrisk import data
    with data.connect() as c:
        f1 = data.feature_table_fingerprint(c)
    r = subprocess.run([str(Path(sys.executable).with_name("dbt")), "run", "--profiles-dir", ".", "--quiet"],
                       cwd=Path(__file__).resolve().parents[1] / "dbt", capture_output=True, text=True, env=os.environ.copy())
    assert r.returncode == 0, r.stdout[-2000:]
    with data.connect() as c:
        assert data.feature_table_fingerprint(c) == f1


def test_python_feature_matrix_from_db_matches_registry(built_db):
    from creditrisk import data
    from creditrisk.features import FEATURE_COLUMNS
    with data.connect() as c:
        frames = data.split_frames(data.load_training_frame(c))
    for X, y in frames.values():
        assert list(X.columns) == FEATURE_COLUMNS and len(X) == len(y)


# ---------------------------------------------------------------- features -> trained model -> registry -> inference
def test_train_register_and_serve_from_registry(built_db, tmp_path, monkeypatch):
    import mlflow
    from fastapi.testclient import TestClient
    from creditrisk import api, registry, train
    from creditrisk.audit import PostgresAudit
    from creditrisk import config
    uri = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)
    monkeypatch.setattr(registry, "DECISION_LOG", tmp_path / "d.jsonl")
    out = tmp_path / "metrics.json"
    assert train.main(["--n-iter", "2", "--out", str(out)]) == 0
    res = json.loads(out.read_text())

    # fixtures are never canonical -> results must be flagged, and the promotion gate must refuse them
    assert res["status"] == "NON_CANONICAL_DO_NOT_QUOTE"
    sel = res["registered_versions"][res["selected_on_validation"]]
    assert not registry.promote(sel)["promote"]
    for m in res["models"].values():
        assert set(m["valid"]) >= {"roc_auc", "pr_auc", "brier", "ks"} and "accuracy" not in m["valid"]
        assert {r["rule"] for r in m["test_threshold_table"]} >= {"top_10pct", "f2_optimal"}

    # registry -> inference (alias set directly, i.e. an operator override, to test serving only)
    client = mlflow.tracking.MlflowClient(); mlflow.set_tracking_uri(uri)
    client.set_registered_model_alias(train.REGISTERED_MODEL, "champion", str(sel))
    app = api.create_app(audit=PostgresAudit(config.pg_dsn()), feature_store=api.PostgresFeatureStore(config.pg_dsn()),
                         api_key_hashes=[api.hash_api_key('int-test-key')])
    sample = _q("select * from staging.stg_application limit 1").iloc[0]
    body = {"sk_id_curr": int(sample.sk_id_curr), "application": {
        "amt_income_total": float(sample.amt_income_total), "amt_credit": float(sample.amt_credit),
        "days_birth": int(sample.days_birth), "ext_source_2": float(sample.ext_source_2)}}
    with TestClient(app, headers={"x-api-key": "int-test-key"}) as c:
        r = c.post("/predict", json=body)
        assert r.status_code == 200, r.text
        pid = r.json()["prediction_id"]
        assert r.json()["history_features_found"] is True and r.json()["model_version"] == str(sel)
    row = _q("select * from audit.predictions where prediction_id=%s", (pid,)).iloc[0]
    assert row.model_version == str(sel) and row.latency_ms > 0 and len(row.applicant_hash) == 32
    assert str(int(sample.sk_id_curr)) != row.applicant_hash


def test_test_split_untouched_by_selection(built_db, monkeypatch):
    """fit_and_select works with frames that have NO test entry."""
    from creditrisk import data, train
    with data.connect() as c:
        frames = data.split_frames(data.load_training_frame(c))
    only_tv = {k: frames[k] for k in ("train", "valid")}
    out = train.fit_and_select(only_tv, n_iter=1, seed=1)
    assert set(out) == {"logreg", "lightgbm"}


def test_training_frame_is_ordered_independent_of_physical_row_order(built_db):
    """load_training_frame sorts by id, so model fitting does not depend on how Postgres happens to store the rows."""
    from creditrisk import data
    with data.connect() as c:
        df = data.load_training_frame(c)
    assert df.sk_id_curr.is_monotonic_increasing
