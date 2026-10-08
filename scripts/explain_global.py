"""Global SHAP summary for the champion on a validation sample (not test). Attribution, not causality."""
from __future__ import annotations

import sys
from pathlib import Path

import mlflow

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from creditrisk import config, data, explain  # noqa: E402
from creditrisk.train import REGISTERED_MODEL, tracking_uri  # noqa: E402


def main() -> int:
    mlflow.set_tracking_uri(tracking_uri())
    mv = mlflow.tracking.MlflowClient().get_model_version_by_alias(REGISTERED_MODEL, "champion")
    model = mlflow.sklearn.load_model(f"models:/{REGISTERED_MODEL}/{mv.version}")
    with data.connect() as c:
        frames = data.split_frames(data.load_training_frame(c))
    imp = explain.global_importance(model, frames["valid"][0], n=3000)
    imp.to_csv(config.REPORT_DIR / "shap_global_importance.csv", index=False)
    (config.REPORT_DIR / "shap_global_importance.md").write_text(
        f"# Global SHAP importance - champion v{mv.version} (validation sample)\n\n> {explain.CAVEAT}\n\n"
        + imp.head(20).to_markdown(index=False, floatfmt=".4f") + "\n")
    print(imp.head(15).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
