"""Prediction audit trail. Stores exactly: prediction_id, model_version, request timestamp, feature schema version,
prediction, latency (+ keyed hash of the applicant id for reconciliation). No applicant attributes are stored."""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime

DDL = """
create schema if not exists audit;
create table if not exists audit.predictions (
    prediction_id           uuid primary key,
    applicant_hash          text        not null,
    model_name              text        not null,
    model_version           text        not null,
    feature_schema_version  text        not null,
    feature_pipeline_version text       not null,
    request_ts              timestamptz not null,
    probability_of_default  double precision not null check (probability_of_default between 0 and 1),
    latency_ms              double precision not null,
    history_found           boolean     not null
);
create index if not exists predictions_ts_idx on audit.predictions (request_ts);
revoke update, delete on audit.predictions from public;
"""


def make_pool(dsn: str, maxconn: int = 20):
    """Bounded Postgres pool with a connect timeout and a per-statement timeout so a slow database fails fast
    (the API then answers 5xx / withholds the score) instead of piling up requests."""
    from psycopg2.pool import ThreadedConnectionPool
    return ThreadedConnectionPool(1, maxconn, dsn, connect_timeout=3, options="-c statement_timeout=3000")


@dataclass
class AuditRecord:
    prediction_id: str
    applicant_id: int
    model_name: str
    model_version: str
    feature_schema_version: str
    feature_pipeline_version: str
    request_ts: datetime
    probability: float
    latency_ms: float
    history_found: bool


def applicant_hash(applicant_id: int, salt: str | None = None) -> str:
    """HMAC-SHA256 so the id cannot be recovered/enumerated from the audit table without the salt."""
    salt = salt if salt is not None else os.environ["AUDIT_HASH_SALT"]
    return hmac.new(salt.encode(), str(applicant_id).encode(), hashlib.sha256).hexdigest()[:32]


class PostgresAudit:
    def __init__(self, dsn: str):
        self.pool = make_pool(dsn)
        conn = self.pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute("select pg_advisory_xact_lock(hashtext('audit-ddl'))")   # several API workers start at once; DDL must not race
            cur.execute(DDL); conn.commit()
        finally:
            self.pool.putconn(conn)

    def write(self, r: AuditRecord) -> None:
        conn = self.pool.getconn()
        try:
            conn.cursor().execute(
                "insert into audit.predictions values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (r.prediction_id, applicant_hash(r.applicant_id), r.model_name, r.model_version, r.feature_schema_version,
                 r.feature_pipeline_version, r.request_ts, r.probability, r.latency_ms, r.history_found))
            conn.commit()
        except Exception:
            conn.rollback(); raise
        finally:
            self.pool.putconn(conn)

    def ping(self) -> bool:
        conn = self.pool.getconn()
        try:
            conn.cursor().execute("select 1"); return True
        finally:
            self.pool.putconn(conn)


class MemoryAudit:
    """Test double with the same interface."""
    def __init__(self):
        self.rows: list[AuditRecord] = []

    def write(self, r: AuditRecord) -> None:
        self.rows.append(r)

    def ping(self) -> bool:
        return True
