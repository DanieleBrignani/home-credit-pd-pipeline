"""Backup + verified restore drill for the pipeline database (audit trail, MLflow registry, meta lineage, marts).

    python scripts/backup_restore.py backup  [--out artifacts/backups]
    python scripts/backup_restore.py drill   [--out artifacts/backups]   # backup -> restore into scratch DB -> compare counts

Uses pg_dump/pg_restore inside the Postgres container (no host client needed). A backup that has never been restored is
not a backup, so `drill` restores into a throw-away database and compares row counts of every table in the audit, meta and
marts schemas. Derived data (staging, intermediate, marts) can also be rebuilt from raw; the audit trail cannot.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
CONTAINER = os.getenv("PG_CONTAINER", "creditrisk-postgres-1")
USER = os.getenv("POSTGRES_USER", "creditrisk")
DB = os.getenv("POSTGRES_DB", "creditrisk")


def sh(*args, **k):
    return subprocess.run(args, check=True, capture_output=True, text=True, **k)


def psql(db: str, sql: str) -> str:
    return sh("docker", "exec", "-i", CONTAINER, "psql", "-U", USER, "-d", db, "-At", "-c", sql).stdout.strip()


def counts(db: str) -> dict[str, int]:
    tables = psql(db, "select table_schema||'.'||table_name from information_schema.tables "
                      "where table_schema in ('audit','meta','marts','public') and table_type='BASE TABLE' order by 1").splitlines()
    return {t: int(psql(db, f'select count(*) from {t}')) for t in tables}


REBUILDABLE = ("raw", "staging", "intermediate")   # recreated from the locked raw files by ingest + dbt build


def backup(out: Path, full: bool = False) -> Path:
    """Default: everything that cannot be rebuilt (audit trail, MLflow registry/runs in public, meta lineage, marts).
    `--full` also dumps the multi-gigabyte raw/staging/intermediate schemas."""
    out.mkdir(parents=True, exist_ok=True)
    name = f"{DB}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}{'_full' if full else ''}.dump"
    excl = [] if full else [a for s in REBUILDABLE for a in ("-N", s)]
    sh("docker", "exec", CONTAINER, "pg_dump", "-U", USER, "-Fc", *excl, "-f", f"/tmp/{name}", DB)
    sh("docker", "cp", f"{CONTAINER}:/tmp/{name}", str(out / name))
    sh("docker", "exec", CONTAINER, "rm", f"/tmp/{name}")
    print("backup written:", out / name)
    return out / name


def drill(out: Path, full: bool = False) -> int:
    t0 = time.time()
    dump = backup(out, full)
    t_backup = time.time() - t0
    scratch = f"restore_drill_{int(time.time())}"
    psql("postgres", f"create database {scratch}")
    try:
        t1 = time.time()   # restore + verification timing starts before the copy into the container
        sh("docker", "cp", str(dump), f"{CONTAINER}:/tmp/{dump.name}")
        sh("docker", "exec", CONTAINER, "pg_restore", "-U", USER, "-d", scratch, "--no-owner", f"/tmp/{dump.name}")
        a, b = counts(DB), counts(scratch)
        ok = a == b and bool(a)
        t_restore = time.time() - t1
        print("source :", a, "\nrestore:", b, "\nRESTORE DRILL", "PASSED" if ok else "FAILED")
        print(f"backup {t_backup:.1f}s, restore+verify {t_restore:.1f}s, dump {dump.stat().st_size/1e6:.1f} MB "
              f"(measured on this machine; not an RTO/RPO)")
        import json
        dest = ROOT / ("reports" if DB == "creditrisk" else "artifacts") / "backup_drill.json"   # fixture runs never write to reports/
        dest.write_text(json.dumps({"passed": ok, "database": DB, "full_dump": full, "dump_mb": dump.stat().st_size / 1e6,
                                    "backup_seconds": t_backup, "restore_verify_seconds": t_restore, "row_counts": a}, indent=2))
        return 0 if ok else 1
    finally:
        psql("postgres", f"drop database if exists {scratch}")
        sh("docker", "exec", CONTAINER, "rm", "-f", f"/tmp/{dump.name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["backup", "drill"])
    ap.add_argument("--out", type=Path, default=ROOT / "artifacts" / "backups")
    ap.add_argument("--full", action="store_true", help="also dump raw/staging/intermediate (large)")
    a = ap.parse_args()
    sys.exit(drill(a.out, a.full) if a.cmd == "drill" else (backup(a.out, a.full) and 0))
