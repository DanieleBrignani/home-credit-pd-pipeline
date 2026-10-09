"""Training-data access, dataset provenance and feature-table fingerprints."""
from __future__ import annotations

import hashlib

import pandas as pd

from . import config
from .contracts import CONTRACTS
from .features import FEATURE_COLUMNS, assert_no_forbidden, build_features

TRAINING_TABLE = "marts.training_dataset"
HISTORY_TABLE = "marts.applicant_history_features"


def connect():
    import psycopg2
    return psycopg2.connect(config.pg_dsn())


def load_training_frame(conn) -> pd.DataFrame:
    # ORDER BY: results must not depend on the physical row order of the table (LightGBM row-subsampling is index-based, so a
    # rebuilt table in a different physical order gave a slightly different model before this was added).
    return pd.read_sql(f"select * from {TRAINING_TABLE} order by sk_id_curr", conn)


def split_frames(df: pd.DataFrame) -> dict[str, tuple[pd.DataFrame, pd.Series]]:
    """Returns {split: (X, y)}. build_features never receives target/split/id columns."""
    out = {}
    for name in ("train", "valid", "test"):
        part = df[df["split"] == name]
        X = build_features(part.drop(columns=["target", "split", "sk_id_curr", "bucket"], errors="ignore"))
        assert_no_forbidden(X.columns)
        assert list(X.columns) == FEATURE_COLUMNS
        out[name] = (X, part["target"].astype(int))
    return out


def dataset_provenance(conn) -> dict:
    """Which raw files fed the pipeline, and whether they are the canonical Kaggle files."""
    b = pd.read_sql(
        "select distinct on (table_name) table_name, sha256, row_count, expected_rows, loaded_at "
        "from meta.ingest_batches order by table_name, loaded_at desc", conn)
    canonical = all(
        (CONTRACTS[r.table_name].expected_rows == r.row_count) for r in b.itertuples()) and len(b) == len(CONTRACTS)
    version = hashlib.sha256("|".join(f"{r.table_name}:{r.sha256}" for r in b.sort_values("table_name").itertuples())
                             .encode()).hexdigest()[:12]
    return {"dataset_version": "ds-" + version, "canonical_kaggle_files": bool(canonical),
            "files": {r.table_name: {"sha256": r.sha256, "rows": int(r.row_count)} for r in b.itertuples()}}


def feature_table_fingerprint(conn) -> str:
    """Order-independent content hash of the training dataset table (used to prove rebuilds are identical)."""
    cur = conn.cursor()
    cur.execute(f"select md5(string_agg(md5(t::text), '' order by t.sk_id_curr)) from {TRAINING_TABLE} t")
    return cur.fetchone()[0]
