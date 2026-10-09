"""Champion / challenger / promotion / rollback on a throw-away MLflow sqlite registry."""
import mlflow
import pytest
from mlflow.tracking import MlflowClient
from sklearn.dummy import DummyClassifier

from creditrisk import registry
from creditrisk.features import feature_schema_version
from creditrisk.train import REGISTERED_MODEL


@pytest.fixture()
def reg(tmp_path, monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    monkeypatch.setattr(registry, "DECISION_LOG", tmp_path / "decisions.jsonl")
    mlflow.set_tracking_uri(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    mlflow.set_experiment("t")
    created = []

    def add(auc, brier=0.07, ks=0.35, canonical="True", schema=None, slope=1.0):
        with mlflow.start_run() as run:
            mlflow.set_tags({"canonical_kaggle_files": canonical, "feature_schema_version": schema or feature_schema_version()})
            mlflow.log_metrics({"valid_roc_auc": auc, "valid_brier": brier, "valid_ks": ks, "valid_calibration_slope": slope})
            info = mlflow.sklearn.log_model(DummyClassifier().fit([[0], [1]], [0, 1]), "model")
        mv = mlflow.register_model(info.model_uri, REGISTERED_MODEL)
        created.append(int(mv.version))
        return int(mv.version)

    return add


def test_bootstrap_promotes_first_model(reg):
    v = reg(0.74)
    d = registry.promote(v)
    assert d["promote"] and registry.status()["champion"]["version"] == v


def test_bootstrap_rejects_noncanonical_data(reg):
    v = reg(0.74, canonical="False")
    d = registry.promote(v)
    assert not d["promote"] and registry.status()["champion"] is None
    assert any(c["gate"] == "gate0_canonical_data" and not c["passed"] for c in d["checks"])


def test_challenger_needs_min_gain(reg):
    a = reg(0.74); registry.promote(a)
    b = reg(0.7405)
    assert not registry.promote(b)["promote"]
    assert registry.status()["champion"]["version"] == a


def test_challenger_promoted_when_better_and_old_champion_kept_for_rollback(reg):
    a = reg(0.74); registry.promote(a)
    b = reg(0.76)
    d = registry.promote(b)
    assert d["promote"]
    s = registry.status()
    assert s["champion"]["version"] == b and s["previous_champion"]["version"] == a


def test_brier_regression_blocks_promotion(reg):
    a = reg(0.74, brier=0.07); registry.promote(a)
    b = reg(0.78, brier=0.09)
    d = registry.promote(b)
    assert not d["promote"] and any(c["gate"] == "gate3_brier" and not c["passed"] for c in d["checks"])


def test_incompatible_feature_schema_blocks_promotion(reg):
    v = reg(0.80, schema="fs-old")
    assert not registry.promote(v)["promote"]


def test_dry_run_changes_nothing(reg):
    v = reg(0.74)
    assert registry.promote(v, dry_run=True)["promote"]
    assert registry.status()["champion"] is None


def test_rollback_restores_previous_champion_and_is_logged(reg, tmp_path):
    a = reg(0.74); registry.promote(a)
    b = reg(0.76); registry.promote(b)
    out = registry.rollback(reason="PSI alarm")
    assert out == {"from": b, "to": a, "reason": "PSI alarm"}
    assert registry.status()["champion"]["version"] == a
    log = (tmp_path / "decisions.jsonl").read_text()
    assert '"kind": "rollback"' in log and '"kind": "promoted"' in log


def test_rollback_without_target_fails(reg):
    a = reg(0.74); registry.promote(a)
    with pytest.raises(RuntimeError):
        registry.rollback()


def test_flow_retry_policy_does_not_retry_deterministic_failures():
    """Contract violations / failed dbt tests / failed gates are NonRetryable; infra errors are retried."""
    from creditrisk import flows

    class S:
        def __init__(self, exc): self.exc = exc
        def result(self):
            raise self.exc

    assert flows._retry_unless_nonretryable(None, None, S(flows.NonRetryable("bad data"))) is False
    assert flows._retry_unless_nonretryable(None, None, S(RuntimeError("database unreachable"))) is True


def test_lifecycle_bootstraps_baseline_then_challenger_must_earn_promotion(reg):
    lr, gb = reg(0.72), reg(0.76)
    steps = registry.lifecycle_step({"logreg": lr, "lightgbm": gb}, "lightgbm")
    assert [s["step"] for s in steps] == ["bootstrap_baseline", "challenger_vs_champion"]
    s = registry.status()
    assert s["champion"]["version"] == gb and s["previous_champion"]["version"] == lr
    assert registry.lifecycle_step({"logreg": lr, "lightgbm": gb}, "lightgbm") == []   # idempotent


def test_lifecycle_keeps_baseline_when_challenger_not_better_enough(reg):
    lr, gb = reg(0.74), reg(0.7405)
    steps = registry.lifecycle_step({"logreg": lr, "lightgbm": gb}, "lightgbm")
    assert steps[1]["promote"] is False and registry.status()["champion"]["version"] == lr


def test_operator_override_is_recorded_with_failed_gates_and_reason(reg, tmp_path):
    a = reg(0.74); registry.promote(a)
    b = reg(0.7405)                      # not enough gain: the normal rule rejects it
    assert not registry.promote(b)["promote"]
    d = registry.promote(b, force_reason="re-baseline after determinism fix")
    assert d["promote"] and d["override_reason"] and "gate2_auc_gain" in d["overridden_failed_gates"]
    s = registry.status()
    assert s["champion"]["version"] == b and s["previous_champion"]["version"] == a
    assert '"kind": "override_promoted"' in (tmp_path / "decisions.jsonl").read_text()
