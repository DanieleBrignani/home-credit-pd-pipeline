"""Inference API.  POST /predict estimates probability of default; it does NOT make a lending decision.

Privacy: request bodies are never logged. Application logs carry prediction_id, model_version and latency only.
Audit: every successful prediction is written to audit.predictions BEFORE the response is returned (fail-closed:
if the audit write fails the request returns 503 and no score is released).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field

from . import config, explain
from .audit import AuditRecord, MemoryAudit, PostgresAudit
from .features import FEATURE_COLUMNS, HISTORY_COLUMNS, build_features, feature_schema_version
from .monitoring import Metrics

log = logging.getLogger("creditrisk.api")


class Application(BaseModel):
    """Application-time fields only. extra='forbid': unknown / post-outcome / protected fields are rejected (422)."""
    model_config = ConfigDict(extra="forbid")
    amt_income_total: float = Field(gt=0)
    amt_credit: float = Field(gt=0)
    amt_annuity: Optional[float] = Field(default=None, gt=0)
    amt_goods_price: Optional[float] = Field(default=None, ge=0)
    days_birth: int = Field(lt=0, ge=-40000)
    days_employed: Optional[int] = Field(default=None, le=0)
    days_registration: Optional[float] = Field(default=None, le=0)
    days_id_publish: Optional[int] = Field(default=None, le=0)
    own_car_age: Optional[float] = Field(default=None, ge=0)
    cnt_children: Optional[int] = Field(default=None, ge=0)
    cnt_fam_members: Optional[float] = Field(default=None, ge=1)
    region_population_relative: Optional[float] = Field(default=None, ge=0, le=1)
    region_rating_client_w_city: Optional[int] = Field(default=None, ge=1, le=3)
    ext_source_1: Optional[float] = Field(default=None, ge=0, le=1)
    ext_source_2: Optional[float] = Field(default=None, ge=0, le=1)
    ext_source_3: Optional[float] = Field(default=None, ge=0, le=1)
    obs_30_cnt_social_circle: Optional[float] = Field(default=None, ge=0)
    def_30_cnt_social_circle: Optional[float] = Field(default=None, ge=0)
    days_last_phone_change: Optional[float] = Field(default=None, le=0)
    amt_req_credit_bureau_year: Optional[float] = Field(default=None, ge=0)
    name_contract_type: Optional[str] = Field(default=None, max_length=40)
    flag_own_car: Optional[Literal["Y", "N"]] = None
    flag_own_realty: Optional[Literal["Y", "N"]] = None
    name_income_type: Optional[str] = Field(default=None, max_length=40)
    name_education_type: Optional[str] = Field(default=None, max_length=60)
    name_family_status: Optional[str] = Field(default=None, max_length=40)
    name_housing_type: Optional[str] = Field(default=None, max_length=40)
    occupation_type: Optional[str] = Field(default=None, max_length=40)


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sk_id_curr: int = Field(gt=0, description="Applicant key used to look up precomputed bureau/previous-loan history")
    application: Application


class PredictResponse(BaseModel):
    prediction_id: str
    probability_of_default: float
    model_version: str
    feature_pipeline_version: str
    timestamp: datetime
    feature_schema_version: str
    history_features_found: bool
    explanation: Optional[dict] = None


# ---------------------------------------------------------------- dependencies (real + test doubles)
class MlflowModelStore:
    """Resolves the registry alias (default: champion) to a concrete version and loads it."""

    def __init__(self, name: str, alias: str):
        self.name, self.alias = name, alias

    def load(self):
        import mlflow
        from mlflow.tracking import MlflowClient
        from .train import tracking_uri
        mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", tracking_uri()))
        mv = MlflowClient().get_model_version_by_alias(self.name, self.alias)
        model = mlflow.sklearn.load_model(f"models:/{self.name}/{mv.version}")
        return model, {"name": self.name, "version": str(mv.version), "family": mv.tags.get("family", "unknown"),
                       "feature_schema_version": mv.tags.get("feature_schema_version", "unknown")}


class PostgresFeatureStore:
    def __init__(self, dsn: str):
        from psycopg2.pool import ThreadedConnectionPool
        self.pool = ThreadedConnectionPool(1, 5, dsn)

    def get(self, sk_id_curr: int) -> Optional[dict]:
        conn = self.pool.getconn()
        try:
            cur = conn.cursor()
            cols = ", ".join(HISTORY_COLUMNS)
            cur.execute(f"select {cols} from marts.applicant_history_features where sk_id_curr = %s", (sk_id_curr,))
            row = cur.fetchone()
            return None if row is None else dict(zip(HISTORY_COLUMNS, row))
        finally:
            self.pool.putconn(conn)


class MemoryFeatureStore:
    def __init__(self, rows: dict[int, dict] | None = None):
        self.rows = rows or {}

    def get(self, sk_id_curr: int):
        return self.rows.get(sk_id_curr)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_app(model_store=None, feature_store=None, audit=None, admin_token: str | None = None,
               api_key_hashes: list[str] | None = None, allow_unauthenticated: bool = False) -> FastAPI:
    app = FastAPI(title="Home Credit PD scoring API", version="0.1.0",
                  description="Estimates probability of default. Not an autonomous lending decision system.")
    metrics = Metrics()
    state = {"model": None, "info": None, "ready": False}
    admin_token = admin_token if admin_token is not None else os.getenv("ADMIN_TOKEN")
    if api_key_hashes is None:
        api_key_hashes = [h.strip() for h in os.getenv("API_KEY_SHA256S", "").split(",") if h.strip()]
    allow_unauthenticated = allow_unauthenticated or os.getenv("ALLOW_UNAUTHENTICATED") == "1"
    if not api_key_hashes and not allow_unauthenticated:
        # fail closed at startup rather than silently serving scores to anyone
        raise RuntimeError("API_KEY_SHA256S is not configured (set ALLOW_UNAUTHENTICATED=1 only for local experiments)")

    def require_key(x_api_key: str = Header(default="")):
        if allow_unauthenticated and not api_key_hashes:
            return
        digest = hash_api_key(x_api_key)
        if not any(hmac.compare_digest(digest, h) for h in api_key_hashes):
            metrics.requests.labels("auth", "401").inc()
            raise HTTPException(401, "missing or invalid API key")

    def _load():
        model_store_ = model_store or MlflowModelStore("credit-risk-pd", os.getenv("MODEL_ALIAS", "champion"))
        model, info = model_store_.load()
        state.update(model=model, info=info, ready=True)
        metrics.model_info.clear()
        metrics.model_info.labels(info["version"], info["family"], info["feature_schema_version"]).set(1)
        metrics.model_loaded_ts.set(time.time())
        log.info("model loaded version=%s family=%s", info["version"], info["family"])

    @app.on_event("startup")
    def _startup():
        nonlocal feature_store, audit
        if feature_store is None:
            feature_store = PostgresFeatureStore(config.pg_dsn())
        if audit is None:
            audit = PostgresAudit(config.pg_dsn())
        try:
            _load()
        except Exception:
            log.exception("model load failed; /ready will report not ready")
            metrics.errors.labels("model_load").inc()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        if not state["ready"]:
            raise HTTPException(503, "model not loaded")
        return {"status": "ready", "model_version": state["info"]["version"]}

    @app.get("/model", dependencies=[Depends(require_key)])
    def model_info():
        if not state["ready"]:
            raise HTTPException(503, "model not loaded")
        return {**state["info"], "feature_pipeline_version": config.FEATURE_PIPELINE_VERSION,
                "feature_schema_version": feature_schema_version()}

    @app.post("/admin/reload")
    def reload(x_admin_token: str = Header(default="")):
        if not admin_token or x_admin_token != admin_token:
            raise HTTPException(403, "forbidden")
        _load()
        return {"status": "reloaded", "model_version": state["info"]["version"]}

    @app.get("/metrics")
    def prom():
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @app.post("/predict", response_model=PredictResponse, response_model_exclude_none=True,
              dependencies=[Depends(require_key)])
    def predict(req: PredictRequest, explain_top_k: int = Query(0, ge=0, le=10)):
        t0 = time.perf_counter()
        if not state["ready"]:
            metrics.requests.labels("/predict", "503").inc()
            raise HTTPException(503, "model not loaded")
        try:
            hist = feature_store.get(req.sk_id_curr)
            row = {**req.application.model_dump(), **(hist or {})}
            X = build_features(pd.DataFrame([row]))
            assert list(X.columns) == FEATURE_COLUMNS
            p = float(state["model"].predict_proba(X)[:, 1][0])
            if not 0.0 <= p <= 1.0:
                raise ValueError("probability out of range")
            latency_ms = (time.perf_counter() - t0) * 1000
            pid = str(uuid.uuid4())
            ts = datetime.now(timezone.utc)
            try:
                audit.write(AuditRecord(pid, req.sk_id_curr, state["info"]["name"], state["info"]["version"],
                                        feature_schema_version(), config.FEATURE_PIPELINE_VERSION, ts, p, latency_ms,
                                        hist is not None))
            except Exception:
                metrics.errors.labels("audit_write").inc()
                metrics.requests.labels("/predict", "503").inc()
                log.error("audit write failed; score withheld")   # no payload in logs
                raise HTTPException(503, "audit unavailable; prediction withheld")
            expl = None
            if explain_top_k:
                expl = {"top_contributions": explain.top_k(state["model"], X, explain_top_k), "caveat": explain.CAVEAT}
            metrics.observe_features(X.iloc[0].to_dict())
            metrics.pd.observe(p)
            if hist is None:
                metrics.history_miss.inc()
            metrics.latency.observe(time.perf_counter() - t0)
            metrics.requests.labels("/predict", "200").inc()
            log.info("prediction_id=%s model_version=%s latency_ms=%.1f", pid, state["info"]["version"], latency_ms)
            return PredictResponse(prediction_id=pid, probability_of_default=p, model_version=state["info"]["version"],
                                   feature_pipeline_version=config.FEATURE_PIPELINE_VERSION, timestamp=ts,
                                   feature_schema_version=feature_schema_version(),
                                   history_features_found=hist is not None, explanation=expl)
        except HTTPException:
            raise
        except Exception:
            metrics.errors.labels("inference").inc()
            metrics.requests.labels("/predict", "500").inc()
            log.exception("inference error")  # traceback, but never the request body
            raise HTTPException(500, "inference error")

    return app


def app_factory() -> FastAPI:  # uvicorn --factory creditrisk.api:app_factory
    return create_app()
