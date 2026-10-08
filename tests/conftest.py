from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def _load_dotenv():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()
os.environ.setdefault("AUDIT_HASH_SALT", "test-salt-not-secret")


@pytest.fixture(scope="session")
def fixture_tables(tmp_path_factory):
    from tests.fixtures.make_fixtures import make
    out = tmp_path_factory.mktemp("raw")
    return make(out=out), out


@pytest.fixture(scope="session")
def tiny_models(fixture_tables):
    """Models trained on fixture applications only (no history) -> exercises serving code, not model quality."""
    from creditrisk.features import build_features
    from creditrisk.modeling import LGBMPipeline, make_logreg
    tables, _ = fixture_tables
    app = tables["application_train"].copy()
    app.columns = [c.lower() for c in app.columns]
    X = build_features(app)
    y = app["target"]
    lr = make_logreg(C=0.1).fit(X, y)
    gb = LGBMPipeline({"n_estimators": 40, "num_leaves": 7, "min_child_samples": 20}).fit(X, y)
    return {"logreg": lr, "lightgbm": gb}, X


@pytest.fixture(scope="session")
def test_db_env():
    """Dedicated database so fixtures never touch the database used for real data."""
    import psycopg2
    os.environ["POSTGRES_DB"] = os.getenv("TEST_POSTGRES_DB", "creditrisk_test")
    if "POSTGRES_PASSWORD" not in os.environ:
        pytest.skip("POSTGRES_PASSWORD not set")
    from creditrisk import config
    try:
        psycopg2.connect(config.pg_dsn(), connect_timeout=3).close()
    except Exception as e:  # database does not exist yet or server is down
        try:
            admin = os.environ.copy(); admin["POSTGRES_DB"] = "postgres"
            os.environ["POSTGRES_DB"] = "postgres"
            c = psycopg2.connect(config.pg_dsn(), connect_timeout=3); c.autocommit = True
            c.cursor().execute(f"create database {os.getenv('TEST_POSTGRES_DB', 'creditrisk_test')}")
            c.close()
            os.environ["POSTGRES_DB"] = os.getenv("TEST_POSTGRES_DB", "creditrisk_test")
        except Exception:
            pytest.skip(f"Postgres not reachable: {e}")
    return os.environ["POSTGRES_DB"]


@pytest.fixture(scope="session")
def built_db(test_db_env, fixture_tables):
    """raw (fixtures) -> ingest -> dbt build. Session scoped; idempotent."""
    from creditrisk import ingest
    _, raw_dir = fixture_tables
    rc = ingest.main(["--raw-dir", str(raw_dir), "--allow-rowcount-mismatch"])
    assert rc == 0
    res = subprocess.run([str(Path(sys.executable).with_name("dbt")), "build", "--profiles-dir", ".", "--quiet"],
                         cwd=ROOT / "dbt", capture_output=True, text=True, env=os.environ.copy())
    assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-1000:]
    return test_db_env
