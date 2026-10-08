"""E2E (in-process): sample applicant -> POST /predict -> response contract, audit row, logs, metrics, failure modes."""
import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from creditrisk import api, config
from creditrisk.audit import MemoryAudit
from creditrisk.features import feature_schema_version

KEY = "test-key-not-a-secret"
HDR = {"x-api-key": KEY}

APPLICANT = {
    "sk_id_curr": 100001,
    "application": {"amt_income_total": 187654.0, "amt_credit": 500000.0, "amt_annuity": 24000.0, "amt_goods_price": 450000.0,
                    "days_birth": -13000, "days_employed": -2400, "ext_source_1": 0.41, "ext_source_2": 0.62,
                    "ext_source_3": 0.55, "name_contract_type": "Cash loans", "flag_own_car": "N"},
}


class FakeStore:
    def __init__(self, model, version="7", family="logreg"):
        self.model, self.version, self.family = model, version, family

    def load(self):
        return self.model, {"name": "credit-risk-pd", "version": self.version, "family": self.family,
                            "feature_schema_version": feature_schema_version()}


@pytest.fixture()
def client(tiny_models):
    models, _ = tiny_models
    audit = MemoryAudit()
    app = api.create_app(FakeStore(models["lightgbm"], "7", "lightgbm"),
                         api.MemoryFeatureStore({100001: {"bureau_n_credits": 3, "inst_n_records": 20,
                                                          "inst_late_share": 0.1}}), audit, admin_token="tok",
                         api_key_hashes=[api.hash_api_key(KEY)])
    with TestClient(app, headers=HDR) as c:
        c.audit = audit
        yield c


def test_predict_contract(client):
    r = client.post("/predict", json=APPLICANT)
    assert r.status_code == 200, r.text
    b = r.json()
    for k in ("prediction_id", "probability_of_default", "model_version", "feature_pipeline_version", "timestamp"):
        assert k in b
    uuid.UUID(b["prediction_id"])
    assert 0.0 <= b["probability_of_default"] <= 1.0
    assert b["model_version"] == "7" and b["feature_pipeline_version"] == config.FEATURE_PIPELINE_VERSION
    assert b["history_features_found"] is True


def test_unknown_applicant_still_scores_and_flags_missing_history(client):
    body = {**APPLICANT, "sk_id_curr": 999}
    r = client.post("/predict", json=body)
    assert r.status_code == 200 and r.json()["history_features_found"] is False


def test_audit_row_has_exact_fields_and_no_applicant_data(client):
    pid = client.post("/predict", json=APPLICANT).json()["prediction_id"]
    row = client.audit.rows[-1]
    assert row.prediction_id == pid and row.model_version == "7"
    assert row.feature_schema_version == feature_schema_version()
    assert 0 <= row.probability <= 1 and row.latency_ms > 0
    assert not hasattr(row, "amt_income_total")


@pytest.mark.parametrize("extra", [{"target": 1}, {"code_gender": "F"}, {"days_termination": -5}])
def test_forbidden_or_unknown_fields_rejected(client, extra):
    body = {**APPLICANT, "application": {**APPLICANT["application"], **extra}}
    assert client.post("/predict", json=body).status_code == 422


@pytest.mark.parametrize("patch", [{"amt_income_total": -1}, {"ext_source_2": 1.5}, {"days_birth": 100}])
def test_invalid_values_rejected(client, patch):
    body = {**APPLICANT, "application": {**APPLICANT["application"], **patch}}
    assert client.post("/predict", json=body).status_code == 422


def test_logs_never_contain_applicant_payload(client, caplog):
    with caplog.at_level(logging.DEBUG):
        client.post("/predict", json=APPLICANT)
    text = " ".join(r.getMessage() for r in caplog.records)
    for secret in ("187654", "500000", "0.62", "100001"):
        assert secret not in text
    assert "prediction_id=" in text


def test_audit_failure_withholds_score(tiny_models):
    models, _ = tiny_models

    class Broken(MemoryAudit):
        def write(self, r):
            raise RuntimeError("db down")

    app = api.create_app(FakeStore(models["logreg"]), api.MemoryFeatureStore(), Broken(), api_key_hashes=[], allow_unauthenticated=True)
    with TestClient(app) as c:
        r = c.post("/predict", json=APPLICANT)
        assert r.status_code == 503 and "probability" not in r.text


def test_not_ready_without_model():
    class NoModel:
        def load(self):
            raise RuntimeError("no champion")

    app = api.create_app(NoModel(), api.MemoryFeatureStore(), MemoryAudit(), api_key_hashes=[], allow_unauthenticated=True)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/ready").status_code == 503
        assert c.post("/predict", json=APPLICANT).status_code == 503


def test_metrics_exposed(client):
    client.post("/predict", json=APPLICANT)
    text = client.get("/metrics").text
    for name in ("pd_api_requests_total", "pd_api_request_seconds_bucket", "pd_prediction_probability_bucket",
                 "pd_feature_missing_total", "pd_model_info", "pd_feature_ext_source_mean_bucket"):
        assert name in text
    assert 'version="7"' in text


def test_explain_includes_causal_caveat(client):
    b = client.post("/predict?explain_top_k=3", json=APPLICANT).json()
    assert len(b["explanation"]["top_contributions"]) == 3
    assert "not describe causal" in b["explanation"]["caveat"]


def test_admin_reload_requires_token(client):
    assert client.post("/admin/reload").status_code == 403
    assert client.post("/admin/reload", headers={"x-admin-token": "tok"}).status_code == 200


def test_probability_format_for_both_model_families(tiny_models):
    models, X = tiny_models
    for m in models.values():
        p = m.predict_proba(X.head(50))
        assert p.shape == (50, 2) and ((p >= 0) & (p <= 1)).all() and abs(p.sum(1) - 1).max() < 1e-9


def test_predict_requires_valid_api_key(client):
    anon = TestClient(client.app)
    assert anon.post("/predict", json=APPLICANT).status_code == 401
    assert anon.post("/predict", json=APPLICANT, headers={"x-api-key": "wrong"}).status_code == 401
    assert anon.get("/model").status_code == 401
    assert client.post("/predict", json=APPLICANT).status_code == 200
    assert client.audit.rows, "authenticated call audited"
    n = len(client.audit.rows)
    anon.post("/predict", json=APPLICANT)
    assert len(client.audit.rows) == n, "rejected request must not be scored or audited"


def test_app_refuses_to_start_without_auth_configuration(monkeypatch):
    monkeypatch.delenv("API_KEY_SHA256S", raising=False)
    monkeypatch.delenv("ALLOW_UNAUTHENTICATED", raising=False)
    with pytest.raises(RuntimeError, match="API_KEY_SHA256S"):
        api.create_app(FakeStore(None), api.MemoryFeatureStore(), MemoryAudit())


def test_health_and_metrics_stay_open_for_probes(client):
    anon = TestClient(client.app)
    assert anon.get("/health").status_code == 200 and anon.get("/metrics").status_code == 200


def test_rate_limit_per_key_returns_429_with_retry_after(tiny_models):
    models, _ = tiny_models
    app = api.create_app(FakeStore(models["logreg"]), api.MemoryFeatureStore(), MemoryAudit(),
                         api_key_hashes=[api.hash_api_key(KEY), api.hash_api_key("other-key")], rate_limit_per_minute=3)
    with TestClient(app) as c:
        codes = [c.post("/predict", json=APPLICANT, headers=HDR).status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
        r = c.post("/predict", json=APPLICANT, headers=HDR)
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
        # a different key has its own bucket
        assert c.post("/predict", json=APPLICANT, headers={"x-api-key": "other-key"}).status_code == 200
        assert 'status="429"' in c.get("/metrics").text


def test_validation_failures_are_counted_in_metrics(client):
    bad = {**APPLICANT, "application": {**APPLICANT["application"], "amt_income_total": -5}}
    assert client.post("/predict", json=bad).status_code == 422
    assert 'status="422"' in client.get("/metrics").text
