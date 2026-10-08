"""Segment-level error analysis of the champion on the held-out test split (descriptive; nothing here is used to select,
tune or promote a model). Writes reports/failure_analysis.json.

For each segment: size, observed default rate, mean predicted PD, calibration ratio (mean predicted / observed),
ROC-AUC (only when the segment has at least MIN_N rows and both classes). Segments are chosen up-front (contract type, history
presence, missing external scores, age band), not mined for the worst-looking result.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from creditrisk import config, data  # noqa: E402
from creditrisk.features import build_features  # noqa: E402
from creditrisk.train import REGISTERED_MODEL, tracking_uri  # noqa: E402

MIN_N = 500


def main() -> int:
    mlflow.set_tracking_uri(tracking_uri())
    mv = mlflow.tracking.MlflowClient().get_model_version_by_alias(REGISTERED_MODEL, "champion")
    model = mlflow.sklearn.load_model(f"models:/{REGISTERED_MODEL}/{mv.version}")
    with data.connect() as c:
        raw = data.load_training_frame(c)
    te = raw[raw.split == "test"].reset_index(drop=True)
    X = build_features(te.drop(columns=["target", "split", "sk_id_curr", "bucket"], errors="ignore"))
    y = te["target"].astype(int).to_numpy()
    p = model.predict_proba(X)[:, 1]
    seg = pd.DataFrame({
        "contract_type": te["name_contract_type"],
        "bureau_history": np.where(X["has_bureau_history"] == 1, "has bureau history", "no bureau history"),
        "previous_app_history": np.where(X["has_prev_history"] == 1, "has previous applications", "no previous applications"),
        "ext_scores_missing": X[["ext_source_1", "ext_source_2", "ext_source_3"]].isna().sum(axis=1).astype(str) + " of 3 missing",
        "age_band": pd.cut(-te["days_birth"] / 365.25, [0, 30, 40, 50, 60, 100], labels=["<30", "30-39", "40-49", "50-59", "60+"]).astype(str),
    })
    out = {"model_version": mv.version, "split": "test (descriptive only)", "min_n_for_auc": MIN_N,
           "overall": {"n": int(len(y)), "default_rate": float(y.mean()), "mean_pd": float(p.mean()),
                       "roc_auc": float(roc_auc_score(y, p))}, "segments": {}}
    for col in seg.columns:
        rows = []
        for val, idx in seg.groupby(col).groups.items():
            idx = np.asarray(list(idx))
            yy, pp = y[idx], p[idx]
            r = {"segment": str(val), "n": int(len(idx)), "default_rate": float(yy.mean()), "mean_pd": float(pp.mean()),
                 "calibration_ratio": float(pp.mean() / yy.mean()) if yy.mean() > 0 else None}
            r["roc_auc"] = float(roc_auc_score(yy, pp)) if len(idx) >= MIN_N and 0 < yy.sum() < len(yy) else None
            rows.append(r)
        out["segments"][col] = sorted(rows, key=lambda r: r["segment"])
    (config.REPORT_DIR / "failure_analysis.json").write_text(json.dumps(out, indent=2))
    for col, rows in out["segments"].items():
        print(col)
        for r in rows:
            print(f"  {r['segment']:<28} n={r['n']:>6} obs={r['default_rate']:.3f} pd={r['mean_pd']:.3f} "
                  f"ratio={r['calibration_ratio'] if r['calibration_ratio'] is None else round(r['calibration_ratio'],2)} auc={r['roc_auc'] if r['roc_auc'] is None else round(r['roc_auc'],3)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
