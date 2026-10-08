# NOTE (Windows): MLflow prints emoji in run URLs; set PYTHONUTF8=1 in your shell before running training/pipeline commands.
# Windows: run with Git Bash / WSL make, or copy the commands (they are plain shell). See docs/reproducibility.md
PY ?= .venv/Scripts/python.exe
DBT ?= .venv/Scripts/dbt.exe
RAW ?= data/raw

up:            ## start Postgres (and optionally the whole stack: make stack)
	docker compose up -d postgres
stack:
	docker compose up -d --build
validate:      ## source validation only (contracts + sha256 lock)
	$(PY) -m creditrisk.ingest --raw-dir $(RAW) --validate-only
pipeline:      ## validate -> ingest -> dbt build -> train -> register -> promotion decision (Prefect flow)
	$(PY) -m creditrisk.flows --raw-dir $(RAW)
lineage:
	$(PY) scripts/gen_feature_lineage.py
test:          ## unit + e2e (no DB needed) and integration (needs Postgres)
	$(PY) -m pytest -q
lifecycle:
	$(PY) scripts/lifecycle_demo.py
benchmark:
	$(PY) scripts/benchmark_inference.py --n 2000
drift:
	$(PY) scripts/drift_simulation.py
shap:
	$(PY) scripts/explain_global.py
results:
	$(PY) scripts/make_results.py
verify:
	$(PY) scripts/verify_readme_numbers.py
reproduce:
	$(PY) scripts/reproduce_check.py
all-real: up validate pipeline lifecycle shap drift benchmark results verify reproduce
