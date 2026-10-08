"""Measure /predict latency of the REAL serving stack (uvicorn + champion model from the registry + Postgres feature
lookup + synchronous audit write) with real applicants from the TEST split. Sequential single client => this is latency,
not capacity. Writes reports/inference_benchmark.json. Nothing here is production traffic.

    python scripts/benchmark_inference.py --n 2000
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from creditrisk import config, data  # noqa: E402
from creditrisk.api import Application  # noqa: E402

API_FIELDS = list(Application.model_fields)


def sample_bodies(n: int, seed: int = 0) -> list[dict]:
    with data.connect() as c:
        df = pd.read_sql("select * from marts.training_dataset where split='test'", c)
    df = df.sample(min(n, len(df)), random_state=seed)
    bodies = []
    for r in df.to_dict("records"):
        app = {}
        for k in API_FIELDS:
            v = r.get(k)
            if v is None or (isinstance(v, float) and math.isnan(v)) or (hasattr(v, "__float__") and pd.isna(v)):
                continue
            app[k] = float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, (int, str)) else v
        for k in ("days_birth", "days_employed", "days_id_publish", "cnt_children", "region_rating_client_w_city"):
            if k in app:
                app[k] = int(app[k])
        bodies.append({"sk_id_curr": int(r["sk_id_curr"]), "application": app})
    return bodies


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--warmup", type=int, default=100)
    ap.add_argument("--port", type=int, default=8077)
    ap.add_argument("--out", type=Path, default=config.REPORT_DIR / "inference_benchmark.json")
    a = ap.parse_args()
    bodies = sample_bodies(a.n + a.warmup)
    import secrets
    from creditrisk.api import hash_api_key
    key = secrets.token_urlsafe(16)   # throw-away key for this benchmark process only
    env = {**os.environ, "MODEL_ALIAS": os.getenv("MODEL_ALIAS", "champion"), "API_KEY_SHA256S": hash_api_key(key)}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "creditrisk.api:app_factory", "--factory", "--port", str(a.port),
                             "--log-level", "warning"], env=env)
    base = f"http://127.0.0.1:{a.port}"
    try:
        with httpx.Client(base_url=base, timeout=30, headers={"x-api-key": key}) as c:
            for _ in range(120):
                try:
                    if c.get("/ready").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise SystemExit("API did not become ready (is there a champion model? see `python -m creditrisk.registry status`)")
            model = c.get("/model").json()
            for b in bodies[: a.warmup]:
                c.post("/predict", json=b)
            lat, server_ms, errors = [], [], 0
            t0 = time.perf_counter()
            for b in bodies[a.warmup:]:
                s = time.perf_counter()
                r = c.post("/predict", json=b)
                lat.append((time.perf_counter() - s) * 1000)
                errors += r.status_code != 200
            wall = time.perf_counter() - t0
    finally:
        proc.terminate(); proc.wait(timeout=20)
    lat = np.array(lat)
    res = {"n_requests": len(lat), "errors": int(errors), "model_version": model["version"], "model_family": model["family"],
           "client_latency_ms": {"p50": float(np.percentile(lat, 50)), "p95": float(np.percentile(lat, 95)),
                                 "p99": float(np.percentile(lat, 99)), "mean": float(lat.mean()), "max": float(lat.max())},
           "sequential_throughput_rps": len(lat) / wall,
           "conditions": {"client": "single sequential httpx client over loopback", "includes": "pydantic validation, Postgres feature lookup, "
                          "feature build, model inference, synchronous audit insert, JSON response",
                          "host": platform.platform(), "python": platform.python_version(), "cpu_count": os.cpu_count(),
                          "uvicorn_workers": 1, "payloads": "random real applicants from the held-out test split"}}
    a.out.parent.mkdir(exist_ok=True, parents=True)
    a.out.write_text(json.dumps(res, indent=2))
    print(json.dumps(res["client_latency_ms"], indent=2), "errors:", errors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
