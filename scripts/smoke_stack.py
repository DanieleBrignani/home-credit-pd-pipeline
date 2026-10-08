"""Container-level end-to-end smoke test of the full docker-compose stack, using the FIXTURE database (creditrisk_test).

postgres + mlflow server + api + prometheus + grafana come up; a fixture-trained model is registered in the *containerised*
MLflow registry; an operator override sets the champion alias (the promotion gate correctly refuses non-canonical data);
the API container loads it; an authenticated /predict goes through; Prometheus scrapes the API; Grafana provisions the dashboard.
This proves wiring, not model quality. Output: artifacts/smoke_stack.json (never used as a result).

    python scripts/smoke_stack.py [--keep]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_env():
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


def wait(url, ok=lambda r: r.status_code == 200, tries=90, **kw):
    for _ in range(tries):
        try:
            r = httpx.get(url, timeout=3, **kw)
            if ok(r):
                return r
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise SystemExit(f"timeout waiting for {url}")


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--keep", action="store_true"); a = ap.parse_args()
    load_env()
    os.environ["POSTGRES_DB"] = "creditrisk_test"
    os.environ["PROMETHEUS_PORT"] = "19090"; os.environ["GRAFANA_PORT"] = "13000"; os.environ["API_PORT"] = "18000"   # 9090 is often taken on dev machines
    results = {}
    run = lambda *c, **k: subprocess.run(c, cwd=ROOT, check=True, **k)
    run("docker", "compose", "up", "-d", "--build", "postgres", "mlflow", "api", "prometheus", "grafana")
    try:
        wait("http://localhost:5000/health")
        results["mlflow_up"] = True
        # train + register against the containerised registry (fixtures; artifacts proxied through the server)
        env = {**os.environ, "MLFLOW_TRACKING_URI": "http://localhost:5000", "PYTHONUTF8": "1"}
        run(sys.executable, "-m", "creditrisk.train", "--n-iter", "1", "--out", "artifacts/smoke_metrics.json", env=env,
            stdout=subprocess.DEVNULL)
        reg = json.loads((ROOT / "artifacts/smoke_metrics.json").read_text())
        results["fixture_run_flagged_noncanonical"] = reg["status"] == "NON_CANONICAL_DO_NOT_QUOTE"
        ver = reg["registered_versions"][reg["selected_on_validation"]]
        import mlflow
        mlflow.set_tracking_uri("http://localhost:5000")
        mlflow.tracking.MlflowClient().set_registered_model_alias("credit-risk-pd", "champion", str(ver))
        key = os.environ["API_KEY_LOCAL"]
        r = httpx.post("http://localhost:18000/admin/reload", headers={"x-admin-token": os.environ["ADMIN_TOKEN"]}, timeout=60)
        results["api_reload"] = r.status_code
        wait("http://localhost:18000/ready")
        results["api_ready"] = True
        import psycopg2
        from creditrisk import config
        os.environ["POSTGRES_HOST"] = "localhost"
        with psycopg2.connect(config.pg_dsn()) as c:
            cur = c.cursor()
            cur.execute("select sk_id_curr, amt_income_total, amt_credit, days_birth, ext_source_2 from staging.stg_application limit 1")
            sid, inc, cr, db_, e2 = cur.fetchone()
        body = {"sk_id_curr": int(sid), "application": {"amt_income_total": float(inc), "amt_credit": float(cr),
                                                       "days_birth": int(db_), "ext_source_2": float(e2)}}
        anon = httpx.post("http://localhost:18000/predict", json=body, timeout=30)
        results["unauthenticated_rejected"] = anon.status_code == 401
        p = httpx.post("http://localhost:18000/predict", json=body, headers={"x-api-key": key}, timeout=30)
        results["predict_status"] = p.status_code
        j = p.json()
        results["predict_fields_ok"] = all(k in j for k in ("prediction_id", "probability_of_default", "model_version",
                                                           "feature_pipeline_version", "timestamp"))
        with psycopg2.connect(config.pg_dsn()) as c:
            cur = c.cursor(); cur.execute("select count(*) from audit.predictions where prediction_id=%s", (j["prediction_id"],))
            results["audit_row_written"] = cur.fetchone()[0] == 1
        time.sleep(20)  # allow a scrape
        t = wait("http://localhost:19090/api/v1/targets", ok=lambda r: r.status_code == 200).json()
        results["prometheus_target_up"] = any(x["health"] == "up" for x in t["data"]["activeTargets"])
        q = httpx.get("http://localhost:19090/api/v1/query", params={"query": "sum(pd_api_requests_total)"}).json()
        results["prometheus_has_api_metrics"] = bool(q["data"]["result"])
        g = wait("http://localhost:13000/api/search?query=PD", auth=("admin", os.environ["GRAFANA_ADMIN_PASSWORD"]))
        results["grafana_dashboard_provisioned"] = any("PD scoring API" in d["title"] for d in g.json())
        rules = httpx.get("http://localhost:19090/api/v1/rules").json()
        results["prometheus_alert_rules_loaded"] = sum(len(gr["rules"]) for gr in rules["data"]["groups"])
        user = subprocess.run(["docker", "compose", "exec", "-T", "api", "id", "-u"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        results["api_container_non_root"] = user != "0"
    finally:
        (ROOT / "artifacts" / "smoke_stack.json").write_text(json.dumps(results, indent=2))
        print(json.dumps(results, indent=2))
        if not a.keep:
            subprocess.run(["docker", "compose", "stop", "api", "mlflow", "prometheus", "grafana"], cwd=ROOT)
    bad = [k for k, v in results.items() if v in (False, 0) and k != "api_reload"] + ([] if results.get("predict_status") == 200 else ["predict_status"])
    print("SMOKE", "PASSED" if not bad else f"FAILED: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
