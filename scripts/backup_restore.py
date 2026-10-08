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
                      "where table_schema in ('audit','meta','marts') and table_type='BASE TABLE' order by 1").splitlines()
    return {t: int(psql(db, f'select count(*) from {t}')) for t in tables}


def backup(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    name = f"{DB}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.dump"
    sh("docker", "exec", CONTAINER, "pg_dump", "-U", USER, "-Fc", "-f", f"/tmp/{name}", DB)
    sh("docker", "cp", f"{CONTAINER}:/tmp/{name}", str(out / name))
    sh("docker", "exec", CONTAINER, "rm", f"/tmp/{name}")
    print("backup written:", out / name)
    return out / name


def drill(out: Path) -> int:
    dump = backup(out)
    scratch = f"restore_drill_{int(time.time())}"
    psql("postgres", f"create database {scratch}")
    try:
        sh("docker", "cp", str(dump), f"{CONTAINER}:/tmp/{dump.name}")
        sh("docker", "exec", CONTAINER, "pg_restore", "-U", USER, "-d", scratch, "--no-owner", f"/tmp/{dump.name}")
        a, b = counts(DB), counts(scratch)
        ok = a == b and bool(a)
        print("source :", a, "\nrestore:", b, "\nRESTORE DRILL", "PASSED" if ok else "FAILED")
        return 0 if ok else 1
    finally:
        psql("postgres", f"drop database if exists {scratch}")
        sh("docker", "exec", CONTAINER, "rm", "-f", f"/tmp/{dump.name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["backup", "drill"])
    ap.add_argument("--out", type=Path, default=ROOT / "artifacts" / "backups")
    a = ap.parse_args()
    sys.exit(drill(a.out) if a.cmd == "drill" else (backup(a.out) and 0))
