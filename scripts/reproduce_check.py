"""Re-train from the current database with the recorded seed/config and compare with reports/metrics.json.

Checks (a) the feature table fingerprint is unchanged (same raw -> same features) and (b) validation/test metrics match to
tolerance. Registers nothing and writes to a throw-away MLflow store.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TOL = 1e-6


def main() -> int:
    from creditrisk import train

    ref = json.loads((ROOT / "reports" / "metrics.json").read_text())
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{Path(tmp, 'm.db').as_posix()}"
        out = Path(tmp) / "metrics.json"
        rc = train.main(["--n-iter", str(ref["run_config"]["n_iter"]), "--seed", str(ref["run_config"]["seed"]),
                         "--out", str(out), "--no-register"])
        new = json.loads(out.read_text())
    bad = []
    if new["feature_table_fingerprint"] != ref["feature_table_fingerprint"]:
        bad.append("feature_table_fingerprint differs")
    if new["data_provenance"]["dataset_version"] != ref["data_provenance"]["dataset_version"]:
        bad.append("dataset_version differs")
    for m in ref["models"]:
        for split in ("valid", "test"):
            for k in ("roc_auc", "pr_auc", "brier", "ks"):
                d = abs(new["models"][m][split][k] - ref["models"][m][split][k])
                if d > TOL:
                    bad.append(f"{m}.{split}.{k} differs by {d:.2e}")
    print("REPRODUCED within tolerance %.0e" % TOL if not bad and rc == 0 else "NOT REPRODUCED:\n  " + "\n  ".join(bad))
    return 0 if not bad and rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
