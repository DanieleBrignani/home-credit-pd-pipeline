"""Container-level end-to-end smoke test of the docker-compose stack.

  python scripts/smoke_stack.py            FIXTURE mode: fixture database (creditrisk_test), trains a throw-away model into the
                                           containerised registry; proves wiring only. Output artifacts/smoke_stack.json.
  python scripts/smoke_stack.py --real     REAL mode: the real database and the real champion already in the registry (run the
                                           pipeline first). Scores a real test-split applicant through the TLS proxy, checks that
                                           operational endpoints are not exposed publicly, and MEASURES rollback time end to end
                                           (registry alias flip + API reload + new version observed). Output reports/smoke_stack_real.json.

Checks: services healthy, TLS proxy in front of the API (local CA => certificate verification disabled in this test only),
unauthenticated request rejected, authenticated prediction + audit row, /metrics and /admin blocked at the proxy,
Prometheus scrape + alert rules, Grafana dashboard provisioned, API container non-root.
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
API = "http://localhost:18000"          # loopback-only binding (admin / health)
TLS = "https://localhost:18443"          # public entry through the proxy
PROM = "http://localhost:19090"
GRAF = "http://localhost:13000"


def load_env():
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


def wait(url, ok=lambda r: r.status_code == 200, tries=90, verify=True, **kw):
    for _ in range(tries):
        try:
            r = httpx.get(url, timeout=3, verify=verify, **kw)
            if ok(r):
                return r
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise SystemExit(f"timeout waiting for {url}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    load_env()
    os.environ.update(PROMETHEUS_PORT="19090", GRAFANA_PORT="13000", API_PORT="18000", TLS_PORT="18443", PYTHONUTF8="1")
    if not a.real:
        os.environ["POSTGRES_DB"] = "creditrisk_test"
    results: dict = {"mode": "real" if a.real else "fixtures"}
    run = lambda *c, **k: subprocess.run(c, cwd=ROOT, check=True, **k)
    run("docker", "compose", "up", "-d", "--build", "postgres", "mlflow", "api", "proxy", "prometheus", "grafana")
    key = os.environ["API_KEY_LOCAL"]
    adm = {"x-admin-token": os.environ["ADMIN_TOKEN"]}
    try:
        wait("http://localhost:5000/health")
        import mlflow
        from creditrisk import config
        os.environ["MLFLOW_TRACKING_URI"] = "http://localhost:5000"
        mlflow.set_tracking_uri("http://localhost:5000")
        client = mlflow.tracking.MlflowClient()
        if not a.real:
            run(sys.executable, "-m", "creditrisk.train", "--n-iter", "1", "--out", "artifacts/smoke_metrics.json",
                env={**os.environ}, stdout=subprocess.DEVNULL)
            reg = json.loads((ROOT / "artifacts/smoke_metrics.json").read_text())
            results["fixture_run_flagged_noncanonical"] = reg["status"] == "NON_CANONICAL_DO_NOT_QUOTE"
            client.set_registered_model_alias("credit-risk-pd", "champion", str(reg["registered_versions"][reg["selected_on_validation"]]))
        champ = client.get_model_version_by_alias("credit-risk-pd", "champion")
        results["champion_version"] = int(champ.version)
        wait(f"{API}/health")   # the API loads the model at start-up; do not call it before it is listening
        results["api_reload_status"] = httpx.post(f"{API}/admin/reload", headers=adm, timeout=120).status_code
        wait(f"{API}/ready")
        wait(f"{TLS}/health", verify=False)
        results["tls_proxy_serves_api"] = True

        import psycopg2
        os.environ["POSTGRES_HOST"] = "localhost"
        with psycopg2.connect(config.pg_dsn()) as c:
            cur = c.cursor()
            cur.execute("select sk_id_curr, amt_income_total, amt_credit, days_birth, ext_source_2 from staging.stg_application "
                        + ("where sk_id_curr in (select sk_id_curr from marts.training_dataset where split='test') " if a.real else "")
                        + "order by sk_id_curr limit 1")
            sid, inc, cr, db_, e2 = cur.fetchone()
        body = {"sk_id_curr": int(sid), "application": {"amt_income_total": float(inc), "amt_credit": float(cr),
                                                       "days_birth": int(db_), "ext_source_2": float(e2)}}
        results["unauthenticated_rejected"] = httpx.post(f"{TLS}/predict", json=body, verify=False).status_code == 401
        p = httpx.post(f"{TLS}/predict", json=body, headers={"x-api-key": key}, verify=False, timeout=30)
        results["predict_status_via_tls"] = p.status_code
        j = p.json()
        results["predict_fields_ok"] = all(k in j for k in ("prediction_id", "probability_of_default", "model_version",
                                                           "feature_pipeline_version", "timestamp"))
        results["history_features_found"] = j.get("history_features_found")
        with psycopg2.connect(config.pg_dsn()) as c:
            cur = c.cursor(); cur.execute("select count(*) from audit.predictions where prediction_id=%s", (j["prediction_id"],))
            results["audit_row_written"] = cur.fetchone()[0] == 1
        results["metrics_blocked_at_proxy"] = httpx.get(f"{TLS}/metrics", verify=False).status_code == 404
        results["admin_blocked_at_proxy"] = httpx.post(f"{TLS}/admin/reload", headers=adm, verify=False).status_code == 404

        time.sleep(20)  # allow a scrape
        t = wait(f"{PROM}/api/v1/targets").json()
        results["prometheus_target_up"] = any(x["health"] == "up" for x in t["data"]["activeTargets"])
        q = httpx.get(f"{PROM}/api/v1/query", params={"query": "sum(pd_api_requests_total)"}).json()
        results["prometheus_has_api_metrics"] = bool(q["data"]["result"])
        g = wait(f"{GRAF}/api/search?query=PD", auth=("admin", os.environ["GRAFANA_ADMIN_PASSWORD"]))
        results["grafana_dashboard_provisioned"] = any("PD scoring API" in d["title"] for d in g.json())
        rules = httpx.get(f"{PROM}/api/v1/rules").json()
        results["prometheus_alert_rules_loaded"] = sum(len(gr["rules"]) for gr in rules["data"]["groups"])
        user = subprocess.run(["docker", "compose", "exec", "-T", "api", "id", "-u"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        results["api_container_non_root"] = user != "0"

        if a.real:   # measured rollback: alias flip + reload + observed new version, then roll forward again
            from creditrisk import registry
            before = int(client.get_model_version_by_alias("credit-risk-pd", "champion").version)

            def switch(fn):
                t0 = time.perf_counter()
                out = fn()
                httpx.post(f"{API}/admin/reload", headers=adm, timeout=120).raise_for_status()
                while httpx.get(f"{API}/model", headers={"x-api-key": key}).json()["version"] != str(out["to"]):
                    time.sleep(0.1)
                return out, time.perf_counter() - t0

            rb, t_rb = switch(lambda: registry.rollback(reason="smoke test: measured rollback"))
            served = httpx.post(f"{TLS}/predict", json=body, headers={"x-api-key": key}, verify=False).json()["model_version"]
            _, t_fw = switch(lambda: registry.rollback(to_version=before, reason="smoke test: roll forward"))
            results["rollback_seconds_flip_reload_confirmed"] = round(t_rb, 2)
            results["roll_forward_seconds"] = round(t_fw, 2)
            results["prediction_served_by_rolled_back_model"] = served == str(rb["to"])
            results["rollback_from"], results["rollback_to"] = rb["from"], rb["to"]
    finally:
        out = ROOT / ("reports/smoke_stack_real.json" if a.real else "artifacts/smoke_stack.json")
        out.write_text(json.dumps(results, indent=2))
        print(json.dumps(results, indent=2))
        if not a.keep:
            subprocess.run(["docker", "compose", "stop", "api", "proxy", "prometheus", "grafana"], cwd=ROOT)
    bad = [k for k, v in results.items() if v is False]
    if results.get("predict_status_via_tls") != 200:
        bad.append("predict_status_via_tls")
    print("SMOKE", "PASSED" if not bad else f"FAILED: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
