"""Historical drift SIMULATION. Read docs/monitoring.md first.

What this does (all on the static Home Credit data):
  1. BASELINE SANITY: PSI of monitored features and of the model score, train vs valid and train vs test.
     Both are random hash splits of the same snapshot, so PSI should be ~0. This validates the PSI code and gives the
     noise floor; it is NOT evidence about production behaviour.
  2. SEGMENT SHIFT: PSI of the score between applicant segments (e.g. contract type). Real heterogeneity in the data,
     but still a snapshot, not a time series.
  3. INJECTED SHIFT: the test split is deliberately perturbed (clearly labelled `injected`) to prove the monitor would
     alarm and that the alarm names the right feature. This is a unit test of the monitor, not an observation.

What it CANNOT do: show concept drift, performance decay, or real population drift. The dataset has no application dates and
no delayed outcomes arriving after deployment. Those require production traffic plus realised defaults (months later).

    python scripts/drift_simulation.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import mlflow
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from creditrisk import config, data  # noqa: E402
from creditrisk.monitoring import MONITORED_FEATURES, psi, psi_status  # noqa: E402
from creditrisk.train import REGISTERED_MODEL, tracking_uri  # noqa: E402


def main() -> int:
    mlflow.set_tracking_uri(tracking_uri())
    mv = mlflow.tracking.MlflowClient().get_model_version_by_alias(REGISTERED_MODEL, "champion")
    model = mlflow.sklearn.load_model(f"models:/{REGISTERED_MODEL}/{mv.version}")
    with data.connect() as c:
        raw = data.load_training_frame(c)
    frames = data.split_frames(raw)
    (Xtr, _), (Xva, _), (Xte, _) = frames["train"], frames["valid"], frames["test"]
    score = {k: model.predict_proba(v[0])[:, 1] for k, v in frames.items()}

    def block(ref_X, cur_X, ref_s, cur_s):
        rows = {f: psi(ref_X[f], cur_X[f]) for f in MONITORED_FEATURES}
        rows["score"] = psi(ref_s, cur_s)
        rows["missing_ext_source_1_share_delta"] = float(cur_X["ext_source_1"].isna().mean() - ref_X["ext_source_1"].isna().mean())
        return {k: (v if k.startswith("missing") else {"psi": v, "status": psi_status(v)}) for k, v in rows.items()}

    out = {"model_version": mv.version, "note": "static snapshot; see module docstring for what this does and does not show",
           "baseline_train_vs_valid": block(Xtr, Xva, score["train"], score["valid"]),
           "baseline_train_vs_test": block(Xtr, Xte, score["train"], score["test"])}

    seg = raw[raw.split == "test"].reset_index(drop=True)
    Xt = Xte.reset_index(drop=True)
    cash = (seg.name_contract_type == "Cash loans").to_numpy()
    out["segment_shift_test_cash_vs_revolving"] = {
        "score_psi": psi(score["test"][cash], score["test"][~cash]),
        "n_cash": int(cash.sum()), "n_revolving": int((~cash).sum()),
        "interpretation": "population heterogeneity inside one snapshot, not drift over time"}

    # injected shifts: label = injected
    rng = np.random.default_rng(0)
    inj = {}
    cases = {
        "ext_source_2 shifted down 0.15": ("ext_source_2", lambda s: s - 0.15),
        "credit_income_ratio inflated x1.5": ("credit_income_ratio", lambda s: s * 1.5),
        "ext_source_1 missingness +30pp": ("ext_source_1", lambda s: s.mask(rng.random(len(s)) < 0.3)),
    }
    for name, (col, fn) in cases.items():
        Xp = Xte.copy()
        Xp[col] = fn(Xp[col])
        sp = model.predict_proba(Xp)[:, 1]
        inj[name] = {"kind": "INJECTED (synthetic perturbation applied directly to one model-input column of real test rows)",
                     "feature_psi": {f: round(psi(Xtr[f], Xp[f]), 4) for f in MONITORED_FEATURES + [col] if f in Xp},
                     "score_psi": psi(score["train"], sp), "score_status": psi_status(psi(score["train"], sp)),
                     "mean_score_change": float(sp.mean() - score["test"].mean())}
    out["injected_shift_detection"] = inj
    p = config.REPORT_DIR / "drift_simulation.json"
    p.parent.mkdir(exist_ok=True, parents=True)
    p.write_text(json.dumps(out, indent=2, default=float))
    print(json.dumps({k: out[k] for k in ("baseline_train_vs_test",)}, indent=2, default=float)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
