"""The results generator and README verifier, exercised with FAKE report files in a temp dir (renderer logic only).
Nothing here is, or may be mistaken for, a result: the numbers are arbitrary placeholders, and the temp files never touch reports/."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def _m(status="CANONICAL"):
    def blk(auc):
        return {"roc_auc": auc, "pr_auc": 0.2, "brier": 0.07, "brier_baseline_prevalence": 0.074, "ks": 0.3,
                "calibration_slope": 1.0, "calibration_intercept": 0.0, "prevalence": 0.08, "n": 100}
    rows = [{"rule": "top_10pct", "threshold": 0.2, "flag_rate": 0.1, "precision": 0.3, "recall": 0.4}]
    mod = {n: {"params": {}, "fit_seconds": 1.0, "valid": blk(a), "test": blk(a), "thresholds_from_valid": {}, "test_threshold_table": rows}
           for n, a in (("logreg", 0.7), ("lightgbm", 0.75))}
    return {"status": status, "git_commit": "abc" * 5, "feature_schema_version": "fs-x", "feature_table_fingerprint": "f" * 8,
            "run_config": {"n_iter": 2, "seed": 1}, "split_sizes": {"train": 70, "valid": 15, "test": 15},
            "selected_on_validation": "lightgbm", "training_seconds_total": 5.0, "models": mod,
            "feature_counts": {"engineered_total (aggregates + derived)": 61, "total_model_input_columns": 90},
            "data_provenance": {"dataset_version": "ds-x", "files": {"bureau": {"rows": 10, "sha256": "x"}}}}


def test_refuses_to_publish_noncanonical(tmp_path, monkeypatch):
    mr = _load("make_results")
    (tmp_path / "metrics.json").write_text(json.dumps(_m("NON_CANONICAL_DO_NOT_QUOTE")))
    monkeypatch.setattr(mr, "REP", tmp_path)
    with pytest.raises(SystemExit, match="NON_CANONICAL"):
        mr.render_block()


def test_renders_every_required_result_item(tmp_path, monkeypatch):
    mr = _load("make_results")
    (tmp_path / "metrics.json").write_text(json.dumps(_m()))
    (tmp_path / "runtime.json").write_text(json.dumps({"feature_pipeline_dbt_build": {"seconds": 12.0}, "ingest": {"seconds": 3.0}}))
    (tmp_path / "inference_benchmark.json").write_text(json.dumps({
        "client_latency_ms": {"p50": 5.0, "p95": 9.0, "p99": 12.0}, "n_requests": 10, "errors": 0, "model_version": "2",
        "model_family": "lightgbm", "conditions": {"cpu_count": 8}}))
    monkeypatch.setattr(mr, "REP", tmp_path)
    block, summary = mr.render_block()
    for needle in ("Logistic regression (baseline)", "LightGBM", "PR-AUC", "Brier", "KS", "calib. slope", "precision", "recall",
                   "training wall-clock", "engineered features", "feature pipeline", "p50 / p95"):
        assert needle in block, needle
    assert "accuracy" not in block.lower()
    assert "ROC-AUC" in summary


def test_splice_and_check_roundtrip(tmp_path):
    mr = _load("make_results")
    p = tmp_path / "R.md"
    p.write_text(f"intro\n{mr.START}\nold\n{mr.END}\noutro\n")
    mr.splice(p, mr.START, mr.END, "NEW BODY")
    assert "NEW BODY" in p.read_text() and "old" not in p.read_text() and p.read_text().startswith("intro")


def test_verifier_flags_unregistered_numbers_in_prose():
    v = _load("verify_readme_numbers")
    prose = v.strip_code(v.strip_generated("A model reached 0.83 AUC. Run `make x 42`.\n1. ordinal ok\nThe `422` code.\n"
                                           "<!-- RESULTS:START (g) -->\n0.99\n<!-- RESULTS:END -->"))
    found = [m.group(0) for m in v.NUM.finditer(prose)]
    assert found == ["0.83"]


def test_repo_readmes_have_no_unregistered_prose_numbers():
    v = _load("verify_readme_numbers")
    for name in ("README.md", "RECRUITER_SUMMARY.md"):
        prose = v.strip_code(v.strip_generated((ROOT / name).read_text(encoding="utf-8")))
        claims = json.loads((ROOT / "docs" / "claims.json").read_text())
        bad = [m.group(0) for m in v.NUM.finditer(prose) if m.group(0) not in claims]
        assert not bad, f"{name}: {bad}"
