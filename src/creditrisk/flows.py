"""Prefect orchestration: validate -> ingest -> dbt build -> train/evaluate/register -> promotion decision.

Error handling / retry strategy
  * Transient infrastructure errors (DB connection, container restart) are retried with backoff.
  * Deterministic failures are NOT retried: a data-contract violation (exit code 2), a failed dbt test, or a
    failed promotion gate stops the run with a non-zero status. Retrying cannot fix bad data.
  * Every stage's wall-clock time is written to reports/runtime.json (source of the runtime numbers in the README).
Idempotency: ingest skips files whose sha256 is already loaded; dbt rebuilds tables from raw; the split is a hash.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from prefect import flow, get_run_logger, task

from . import config

DBT_DIR = config.ROOT / "dbt"


class NonRetryable(RuntimeError):
    """Deterministic failure: bad data / failed gate."""


def _retry_unless_nonretryable(task, task_run, state) -> bool:
    try:
        state.result()
    except NonRetryable:
        return False
    except Exception:
        return True
    return True


def _timed(stage: str, fn, *a, **k):
    t0 = time.time()
    out = fn(*a, **k)
    path = Path(os.getenv("RUNTIME_JSON", config.REPORT_DIR / "runtime.json"))
    cur = json.loads(path.read_text()) if path.exists() else {}
    cur[stage] = {"seconds": round(time.time() - t0, 2), "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(cur, indent=2))
    return out


@task(retries=0)
def validate_sources(raw_dir: str) -> None:
    from . import ingest
    if ingest.main(["--raw-dir", raw_dir, "--validate-only"]) != 0:
        raise NonRetryable("source validation failed (see log)")


@task(retries=3, retry_delay_seconds=[5, 30, 120], retry_condition_fn=_retry_unless_nonretryable)
def ingest_raw(raw_dir: str, allow_mismatch: bool) -> None:
    from . import ingest
    args = ["--raw-dir", raw_dir] + (["--allow-rowcount-mismatch"] if allow_mismatch else [])
    rc = _timed("ingest", ingest.main, args)
    if rc == 2:
        raise NonRetryable("data contract violation")
    if rc != 0:
        raise RuntimeError(f"ingest failed rc={rc}")


@task(retries=2, retry_delay_seconds=[10, 60], retry_condition_fn=_retry_unless_nonretryable)
def dbt_build() -> None:
    def run():
        return subprocess.run([str(Path(sys.executable).with_name("dbt")), "build", "--profiles-dir", "."],
                              cwd=DBT_DIR, capture_output=True, text=True)
    r = _timed("feature_pipeline_dbt_build", run)
    if r.returncode != 0:
        tail = (r.stdout or "")[-2000:]
        if "Database Error" in tail and "could not connect" in tail:
            raise RuntimeError("database unreachable")        # transient -> retry
        raise NonRetryable("dbt build failed (model error or failed data test):\n" + tail)


@task(retries=0)
def train_and_register(n_iter: int, out: str) -> dict:
    from . import train
    rc = _timed("training_total", train.main, ["--n-iter", str(n_iter), "--out", out])
    if rc != 0:
        raise NonRetryable("training failed")
    return json.loads(Path(out).read_text())


@task(retries=0)
def decide_promotion(result: dict, metrics_out: str) -> list:
    from . import registry
    if result["status"] != "CANONICAL":   # never write fixture decisions into the reports/ folder that feeds the README
        registry.DECISION_LOG = Path(metrics_out).parent / "model_decisions.jsonl"
    return registry.lifecycle_step(result["registered_versions"], result["selected_on_validation"])


@flow(name="credit-risk-pipeline", log_prints=True)
def pipeline(raw_dir: str = str(config.RAW_DIR), n_iter: int = 12, allow_noncanonical: bool = False,
             metrics_out: str = str(config.REPORT_DIR / "metrics.json")) -> dict:
    log = get_run_logger()
    validate_sources(raw_dir)
    ingest_raw(raw_dir, allow_noncanonical)
    dbt_build()
    result = train_and_register(n_iter, metrics_out)
    decision = decide_promotion(result, metrics_out)
    log.info("status=%s selected=%s decisions=%s", result["status"], result["selected_on_validation"],
             [(d["step"], d["promote"]) for d in decision])
    return {"status": result["status"], "decisions": decision}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default=str(config.RAW_DIR))
    ap.add_argument("--n-iter", type=int, default=12)
    ap.add_argument("--allow-noncanonical", action="store_true", help="fixtures only; results are flagged NON_CANONICAL")
    ap.add_argument("--metrics-out", default=str(config.REPORT_DIR / "metrics.json"))
    a = ap.parse_args()
    print(json.dumps(pipeline(a.raw_dir, a.n_iter, a.allow_noncanonical, a.metrics_out), indent=2, default=str))
