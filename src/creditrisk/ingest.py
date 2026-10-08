"""Source validation + idempotent raw load.

Flow per table:
  1. file exists, header satisfies the contract (required columns present)
  2. sha256 verified against data/raw/MANIFEST.sha256 (written on first sight = raw immutability lock)
  3. row count compared with the published count
  4. loaded into raw.<table> as TEXT columns via COPY (no casting, no cleaning => raw stays faithful)
  5. lineage row written to meta.ingest_batches; a table whose sha256 is already loaded is skipped
     (idempotent re-runs); a changed file for a locked name is a hard failure.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import sys
import time
import uuid
from pathlib import Path

from . import config
from .contracts import CONTRACTS, TableContract

log = logging.getLogger("ingest")


class ContractViolation(RuntimeError):
    pass


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def read_header(path: Path) -> list[str]:
    with open(path, newline="", encoding="utf-8") as f:
        return next(csv.reader(f))


def read_manifest(path: Path = config.MANIFEST_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            digest, name = line.split("  ", 1)
            out[name] = digest
    return out


def validate_file(c: TableContract, raw_dir: Path, lock: bool = True) -> dict:
    """Source validation. Returns {'sha256', 'header', 'added_columns'} or raises ContractViolation."""
    path = raw_dir / c.file
    if not path.exists():
        raise ContractViolation(f"{c.name}: missing file {path}")
    header = read_header(path)
    missing = [col for col in c.required if col not in header]
    if missing:
        raise ContractViolation(f"{c.name}: required columns missing from header: {missing}")
    if len(set(header)) != len(header):
        raise ContractViolation(f"{c.name}: duplicate column names in header")
    digest = sha256_file(path)
    manifest = read_manifest(raw_dir / "MANIFEST.sha256")
    if c.file in manifest and manifest[c.file] != digest:
        raise ContractViolation(
            f"{c.name}: sha256 changed since first ingest (raw files are immutable). "
            f"locked={manifest[c.file][:12]} now={digest[:12]}")
    if c.file not in manifest and lock:
        with open(raw_dir / "MANIFEST.sha256", "a") as f:
            f.write(f"{digest}  {c.file}\n")
    return {"sha256": digest, "header": header, "added_columns": [h for h in header if h not in c.required]}


DDL = """
create schema if not exists raw;
create schema if not exists meta;
create table if not exists meta.ingest_batches (
    batch_id uuid primary key, table_name text not null, source_file text not null,
    sha256 text not null, row_count bigint not null, expected_rows bigint,
    contract_columns_missing text, loaded_at timestamptz not null default now(), load_seconds double precision,
    git_commit text);
"""


def load_table(conn, c: TableContract, raw_dir: Path, allow_rowcount_mismatch: bool = False,
               expected_rows_override: int | None = None) -> str:
    info = validate_file(c, raw_dir)
    if info["added_columns"]:
        log.warning("%s: schema evolution - %d column(s) not in the contract were loaded to raw as-is: %s",
                    c.name, len(info["added_columns"]), info["added_columns"][:10])
    cur = conn.cursor()
    cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"ingest:{c.name}",))  # one loader per table at a time
    cur.execute("select 1 from meta.ingest_batches where table_name=%s and sha256=%s", (c.name, info["sha256"]))
    if cur.fetchone():
        log.info("%s: sha256 already loaded -> skip (idempotent)", c.name)
        return "skipped"
    t0 = time.time()
    cols = ", ".join(f'"{h}" text' for h in info["header"])
    cur.execute(f'drop table if exists raw."{c.name}"')
    cur.execute(f'create table raw."{c.name}" ({cols})')
    with open(raw_dir / c.file, "r", encoding="utf-8") as f:
        cur.copy_expert(f"copy raw.\"{c.name}\" from stdin with (format csv, header true)", f)
    cur.execute(f'select count(*) from raw."{c.name}"')
    n = cur.fetchone()[0]
    expected = expected_rows_override if expected_rows_override is not None else c.expected_rows
    if expected is not None and n != expected and not allow_rowcount_mismatch:
        conn.rollback()
        raise ContractViolation(f"{c.name}: row count {n} != published {expected}")
    pk = ", ".join(f'"{k}"' for k in c.pk)
    cur.execute(f'select count(*) from (select 1 from raw."{c.name}" group by {pk} having count(*)>1) d')
    dups = cur.fetchone()[0]
    if dups:  # grain violation is reported, not silently dropped
        log.warning("%s: %d duplicate primary-key groups at grain %s (kept; staging decides)", c.name, dups, c.pk)
    cur.execute(
        "insert into meta.ingest_batches(batch_id, table_name, source_file, sha256, row_count, expected_rows,"
        " load_seconds, git_commit) values (%s,%s,%s,%s,%s,%s,%s,%s)",
        (str(uuid.uuid4()), c.name, c.file, info["sha256"], n, expected, time.time() - t0, config.git_commit()))
    conn.commit()
    log.info("%s: loaded %d rows in %.1fs", c.name, n, time.time() - t0)
    return "loaded"


def main(argv=None) -> int:
    import psycopg2
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-dir", type=Path, default=config.RAW_DIR)
    ap.add_argument("--tables", nargs="*", default=list(CONTRACTS))
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--allow-rowcount-mismatch", action="store_true",
                    help="for fixtures/tests only; recorded in meta.ingest_batches")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        if args.validate_only:
            for t in args.tables:
                validate_file(CONTRACTS[t], args.raw_dir)
                log.info("%s: contract OK", t)
            return 0
        conn = psycopg2.connect(config.pg_dsn())
        conn.cursor().execute(DDL)
        conn.commit()
        for t in args.tables:
            load_table(conn, CONTRACTS[t], args.raw_dir, args.allow_rowcount_mismatch)
        return 0
    except ContractViolation as e:
        log.error("CONTRACT VIOLATION: %s", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
