"""Central configuration. Secrets come from environment variables only."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = Path(os.getenv("RAW_DIR", ROOT / "data" / "raw"))
ARTIFACT_DIR = Path(os.getenv("ARTIFACT_DIR", ROOT / "artifacts"))
REPORT_DIR = ROOT / "reports"
MANIFEST_PATH = RAW_DIR / "MANIFEST.sha256"

# Bump FEATURE_PIPELINE_VERSION whenever dbt feature SQL or features.py logic changes.
FEATURE_PIPELINE_VERSION = "fp-1.0.0"
SPLIT_SEED_SALT = "hc-split-v1"  # part of the deterministic hash split
RANDOM_STATE = 42


def pg_dsn() -> str:
    return (
        f"postgresql://{os.getenv('POSTGRES_USER', 'creditrisk')}:"
        f"{os.environ['POSTGRES_PASSWORD']}@{os.getenv('POSTGRES_HOST', 'localhost')}:"
        f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'creditrisk')}"
    )


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"
