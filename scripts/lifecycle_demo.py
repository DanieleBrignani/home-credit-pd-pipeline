"""Champion / challenger / promotion / rollback demonstration on the registry that `train` populated.

  1. (if the pipeline has not already done so) bootstrap: baseline logistic regression becomes champion if it passes the sanity gates,
     then the selected model is evaluated as challenger with the promotion rule.
  2. the full decision history (promoted / rejected, with every gate and its numbers) is read from reports/model_decisions.jsonl
  3. ROLLBACK drill: champion repointed to the previous champion, status checked, then rolled forward again.
Requires canonical data (gate 0). Writes reports/lifecycle_demo.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from creditrisk import config, registry  # noqa: E402


def main() -> int:
    m = json.loads((config.REPORT_DIR / "metrics.json").read_text())
    registry.lifecycle_step(m["registered_versions"], m["selected_on_validation"])   # no-op if the pipeline already did it
    out = {"decision_history": registry.decision_history(), "status_before_drill": registry.status()}
    if out["status_before_drill"]["previous_champion"] is not None:
        before = registry.status()["champion"]["version"]
        out["rollback_drill"] = registry.rollback(reason="scheduled rollback drill")
        out["status_after_rollback"] = registry.status()
        out["roll_forward"] = registry.rollback(to_version=before, reason="drill complete, roll forward")
    else:
        out["rollback_drill"] = "SKIPPED: no previous champion (the selected model was rejected or is the baseline)"
    out["status_final"] = registry.status()
    (config.REPORT_DIR / "lifecycle_demo.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
