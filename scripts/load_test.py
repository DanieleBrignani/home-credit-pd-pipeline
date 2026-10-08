"""Concurrent load test of the real serving stack (uvicorn + champion + Postgres lookup + synchronous audit insert).

Runs the same fixed workload against 1 worker and N workers so scaling is *measured*, not assumed. Real test-split applicants.
Single machine, loopback, client and server share the same CPUs and the same Postgres container => this is a capacity probe for
this laptop, not a production sizing. Writes reports/load_test.json.

    python scripts/load_test.py --requests 3000 --concurrency 16 --workers 1 4
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import secrets
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from creditrisk import config  # noqa: E402
from creditrisk.api import hash_api_key  # noqa: E402

spec = importlib.util.spec_from_file_location("bench", ROOT / "scripts" / "benchmark_inference.py")
bench = importlib.util.module_from_spec(spec); spec.loader.exec_module(bench)


def run_once(workers: int, bodies: list[dict], concurrency: int, port: int) -> dict:
    key = secrets.token_urlsafe(16)
    env = {**os.environ, "API_KEY_SHA256S": hash_api_key(key), "RATE_LIMIT_PER_MINUTE": "0"}   # limiter off: we measure capacity
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "creditrisk.api:app_factory", "--factory", "--port", str(port),
                             "--workers", str(workers), "--log-level", "warning"], env=env)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(180):
            try:
                if httpx.get(f"{base}/ready", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise SystemExit("API did not become ready")
        hdr = {"x-api-key": key}
        with httpx.Client(base_url=base, headers=hdr, timeout=60) as c:       # warm-up
            for b in bodies[:60]:
                c.post("/predict", json=b)
        work = bodies[60:]
        lat, codes = [], []

        def one(chunk):
            out = []
            with httpx.Client(base_url=base, headers=hdr, timeout=60) as c:
                for b in chunk:
                    s = time.perf_counter()
                    r = c.post("/predict", json=b)
                    out.append(((time.perf_counter() - s) * 1000, r.status_code))
            return out

        chunks = [work[i::concurrency] for i in range(concurrency)]
        t0 = time.perf_counter()
        with ThreadPoolExecutor(concurrency) as ex:
            for part in ex.map(one, chunks):
                for ms, code in part:
                    lat.append(ms); codes.append(code)
        wall = time.perf_counter() - t0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
    lat = np.array(lat)
    errors = int(sum(c != 200 for c in codes))
    return {"workers": workers, "concurrency": concurrency, "requests": len(lat), "errors": errors,
            "throughput_rps": round(len(lat) / wall, 1),
            "latency_ms": {"p50": round(float(np.percentile(lat, 50)), 1), "p95": round(float(np.percentile(lat, 95)), 1),
                           "p99": round(float(np.percentile(lat, 99)), 1), "max": round(float(lat.max()), 1)},
            "status_codes": {str(k): int(v) for k, v in zip(*np.unique(codes, return_counts=True))}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=3000)
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 4])
    ap.add_argument("--port", type=int, default=8078)
    a = ap.parse_args()
    bodies = bench.sample_bodies(a.requests + 60)
    runs = [run_once(w, bodies, a.concurrency, a.port) for w in a.workers]
    out = {"runs": runs, "conditions": {"host": platform.platform(), "python": platform.python_version(), "cpu_count": os.cpu_count(),
                                        "note": "client, API workers and Postgres share one machine; rate limiter disabled for the test; "
                                                "synchronous audit insert included; no TLS proxy in this path"}}
    (config.REPORT_DIR / "load_test.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
