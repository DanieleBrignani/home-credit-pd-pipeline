"""Train baseline (logistic regression) + LightGBM, select on VALIDATION, evaluate TEST once, log + register in MLflow.

Test-set discipline:
  * `fit_and_select` receives only train/valid frames.
  * `evaluate_test_once` is the only function that touches the test frame and is called after selection.
  * Thresholds are chosen on validation and frozen before test evaluation.
Reported numbers are written to reports/metrics.json together with the dataset provenance. If the raw files are not
the canonical Kaggle files (row counts differ) the file is marked NON_CANONICAL and must not be quoted as a result.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
from mlflow.tracking import MlflowClient
from sklearn.isotonic import IsotonicRegression

from . import config, data, metrics
from .features import FEATURE_COLUMNS, feature_counts, feature_schema_version
from .modeling import IsotonicCalibratedModel, LGBMPipeline, make_logreg

REGISTERED_MODEL = "credit-risk-pd"
EXPERIMENT = "home-credit-pd"


def tracking_uri() -> str:
    import os
    return os.getenv("MLFLOW_TRACKING_URI", f"sqlite:///{(config.ARTIFACT_DIR / 'mlflow.db').as_posix()}")


def _sample_lgbm_params(rng: np.random.Generator, n: int):
    for i in range(n):
        if i == 0:
            yield {}  # defaults first: a reproducible anchor
            continue
        yield {"num_leaves": int(rng.choice([15, 31, 63])), "learning_rate": float(rng.choice([0.02, 0.03, 0.05])),
               "min_child_samples": int(rng.choice([50, 100, 200])),
               "colsample_bytree": float(rng.choice([0.4, 0.6, 0.8])), "reg_lambda": float(rng.choice([1, 5, 20]))}


def fit_and_select(frames, n_iter: int, seed: int):
    """Uses train + valid only."""
    (Xtr, ytr), (Xva, yva) = frames["train"], frames["valid"]
    t0 = time.time()
    lr_results = []
    for C in (0.01, 0.1, 1.0):
        m = make_logreg(C=C, random_state=seed).fit(Xtr, ytr)
        lr_results.append((metrics.core_metrics(yva, m.predict_proba(Xva)[:, 1])["roc_auc"], C, m))
    _, best_C, lr = max(lr_results, key=lambda r: r[0])
    lr_seconds = time.time() - t0

    t0 = time.time()
    rng = np.random.default_rng(seed)
    best = None
    for params in _sample_lgbm_params(rng, n_iter):
        m = LGBMPipeline(params).fit(Xtr, ytr, Xva, yva)
        auc = metrics.core_metrics(yva, m.predict_proba(Xva)[:, 1])["roc_auc"]
        if best is None or auc > best[0]:
            best = (auc, params, m)
    lgbm_seconds = time.time() - t0
    _, lgbm_params, lgbm = best

    # Calibration decision: 2-fold on validation (fit isotonic on one half, score Brier on the other).
    pv = lgbm.predict_proba(Xva)[:, 1]
    idx = np.random.default_rng(seed).permutation(len(yva))
    a, b = idx[: len(idx) // 2], idx[len(idx) // 2:]
    gains = []
    yv = yva.to_numpy()
    for fit_i, ev_i in ((a, b), (b, a)):
        iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-6, y_max=1 - 1e-6).fit(pv[fit_i], yv[fit_i])
        gains.append(metrics.core_metrics(yv[ev_i], pv[ev_i])["brier"] - metrics.core_metrics(yv[ev_i], iso.predict(pv[ev_i]))["brier"])
    calibrate = bool(np.mean(gains) > 0)
    lgbm_final = lgbm
    if calibrate:
        iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-6, y_max=1 - 1e-6).fit(pv, yv)
        lgbm_final = IsotonicCalibratedModel(lgbm, iso)
    return {"logreg": {"model": lr, "params": {"C": best_C}, "seconds": lr_seconds},
            "lightgbm": {"model": lgbm_final, "params": {**lgbm_params, "best_iteration": lgbm.best_iteration_,
                                                         "isotonic_calibration": calibrate,
                                                         "calibration_brier_gain_cv": float(np.mean(gains))},
                         "seconds": lgbm_seconds}}


def evaluate(model, X, y, thresholds=None):
    p = model.predict_proba(X)[:, 1]
    m = metrics.core_metrics(y, p)
    th = thresholds or metrics.pick_thresholds(y, p)
    return p, m, th


def evaluate_test_once(candidates, frames, thresholds_by_model):
    Xte, yte = frames["test"]
    out = {}
    for name, c in candidates.items():
        p, m, th = evaluate(c["model"], Xte, yte, thresholds_by_model[name])
        out[name] = {"metrics": m, "thresholds": metrics.threshold_table(yte, p, th), "deciles": metrics.decile_table(yte, p),
                     "calibration": metrics.calibration_bins(yte, p), "p": p}
    return out


def _plot_calibration(path: Path, cal: dict[str, pd.DataFrame]):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 0.5], [0, 0.5], "k--", label="perfect")
    for n, df in cal.items():
        ax.plot(df.mean_predicted, df.observed_rate, "o-", label=n)
    ax.set_xlabel("mean predicted PD"); ax.set_ylabel("observed default rate"); ax.legend()
    ax.set_title("Calibration (test, quantile bins)")
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-iter", type=int, default=12)
    ap.add_argument("--seed", type=int, default=config.RANDOM_STATE)
    ap.add_argument("--out", type=Path, default=config.REPORT_DIR / "metrics.json")
    ap.add_argument("--no-register", action="store_true")
    args = ap.parse_args(argv)
    config.ARTIFACT_DIR.mkdir(exist_ok=True, parents=True)
    args.out.parent.mkdir(exist_ok=True, parents=True)
    mlflow.set_tracking_uri(tracking_uri())
    mlflow.set_experiment(EXPERIMENT)

    conn = data.connect()
    prov = data.dataset_provenance(conn)
    fingerprint = data.feature_table_fingerprint(conn)
    df = data.load_training_frame(conn)
    frames = data.split_frames(df)
    split_sizes = {k: int(len(v[1])) for k, v in frames.items()}

    t0 = time.time()
    cands = fit_and_select(frames, args.n_iter, args.seed)
    train_seconds = time.time() - t0

    Xva, yva = frames["valid"]
    valid_eval, thresholds = {}, {}
    for n, c in cands.items():
        p, m, th = evaluate(c["model"], Xva, yva)
        valid_eval[n], thresholds[n] = m, th
    selected = max(valid_eval, key=lambda n: valid_eval[n]["roc_auc"])
    test_eval = evaluate_test_once(cands, frames, thresholds)   # <- only place the test split is scored

    cal_png = args.out.parent / "calibration_test.png"
    _plot_calibration(cal_png, {n: r["calibration"] for n, r in test_eval.items()})

    client = MlflowClient()
    versions = {}
    for n, c in cands.items():
        with mlflow.start_run(run_name=n) as run:
            mlflow.set_tags({
                "git_commit": config.git_commit(), "dataset_version": prov["dataset_version"],
                "canonical_kaggle_files": str(prov["canonical_kaggle_files"]),
                "feature_pipeline_version": config.FEATURE_PIPELINE_VERSION,
                "feature_schema_version": feature_schema_version(), "feature_table_fingerprint": fingerprint,
                "model_family": n, "selected_on_validation": str(n == selected)})
            mlflow.log_params({**{f"hp_{k}": v for k, v in c["params"].items()}, "n_features": len(FEATURE_COLUMNS),
                               "seed": args.seed, **{f"n_{k}": v for k, v in split_sizes.items()}})
            mlflow.log_metrics({**{f"valid_{k}": v for k, v in valid_eval[n].items()},
                                **{f"test_{k}": v for k, v in test_eval[n]["metrics"].items()},
                                "fit_seconds": c["seconds"]})
            d = config.ARTIFACT_DIR / f"run_{n}"
            d.mkdir(exist_ok=True)
            test_eval[n]["thresholds"].to_csv(d / "thresholds_test.csv", index=False)
            test_eval[n]["deciles"].to_csv(d / "deciles_test.csv", index=False)
            (d / "thresholds_valid.json").write_text(json.dumps(thresholds[n]))
            (d / "feature_columns.json").write_text(json.dumps(FEATURE_COLUMNS))
            mlflow.log_artifacts(str(d)); mlflow.log_artifact(str(cal_png))
            mlflow.log_artifact(str(config.ROOT / "docs" / "feature_lineage.md")) if (config.ROOT / "docs" / "feature_lineage.md").exists() else None
            info = mlflow.sklearn.log_model(c["model"], artifact_path="model")
            if not args.no_register:
                mv = mlflow.register_model(info.model_uri, REGISTERED_MODEL)
                client.set_model_version_tag(REGISTERED_MODEL, mv.version, "family", n)
                client.set_model_version_tag(REGISTERED_MODEL, mv.version, "feature_schema_version", feature_schema_version())
                client.set_model_version_tag(REGISTERED_MODEL, mv.version, "feature_pipeline_version", config.FEATURE_PIPELINE_VERSION)
                versions[n] = int(mv.version)

    result = {
        "data_provenance": prov, "status": "CANONICAL" if prov["canonical_kaggle_files"] else "NON_CANONICAL_DO_NOT_QUOTE",
        "git_commit": config.git_commit(), "feature_table_fingerprint": fingerprint,
        "run_config": {"n_iter": args.n_iter, "seed": args.seed},
        "feature_schema_version": feature_schema_version(), "feature_counts": feature_counts(),
        "split_sizes": split_sizes, "selected_on_validation": selected, "registered_versions": versions,
        "training_seconds_total": train_seconds,
        "models": {n: {"params": cands[n]["params"], "fit_seconds": cands[n]["seconds"], "valid": valid_eval[n],
                       "test": test_eval[n]["metrics"], "thresholds_from_valid": thresholds[n],
                       "test_threshold_table": test_eval[n]["thresholds"].to_dict("records")} for n in cands}}
    args.out.write_text(json.dumps(result, indent=2, default=float))
    print(json.dumps({k: result[k] for k in ("status", "selected_on_validation", "registered_versions")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
