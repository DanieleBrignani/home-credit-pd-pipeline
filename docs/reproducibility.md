# Reproducibility: every reported number and the command that produces it

Prerequisites: Docker, Python 3.13, the Kaggle Home Credit CSVs in `data/raw/`, `.env` created from `.env.example`.

| Artifact | Command | Writes |
|---|---|---|
| Postgres | `docker compose up -d postgres` | volume `pgdata` |
| Source validation + raw lock | `python -m creditrisk.ingest --validate-only` | `data/raw/MANIFEST.sha256` |
| Ingest, dbt build, train, register, promotion decision | `python -m creditrisk.flows` | `reports/runtime.json`, `reports/metrics.json`, MLflow |
| Feature lineage doc | `python scripts/gen_feature_lineage.py` | `docs/feature_lineage.md` |
| Champion/challenger/rollback drill | `python scripts/lifecycle_demo.py` | `reports/lifecycle_demo.json`, `reports/model_decisions.jsonl` |
| Global SHAP | `python scripts/explain_global.py` | `reports/shap_global_importance.*` |
| Drift simulation | `python scripts/drift_simulation.py` | `reports/drift_simulation.json` |
| Inference p50/p95 | `python scripts/benchmark_inference.py --n 2000` | `reports/inference_benchmark.json` |
| README / summary numbers | `python scripts/make_results.py` | README + RECRUITER_SUMMARY blocks, `reports/RESULTS.md` |
| Check that every README number is traceable | `python scripts/verify_readme_numbers.py` | exit code |
| Re-train and compare metrics | `python scripts/reproduce_check.py` | exit code |
| Tests | `pytest -q` | — |

`make all-real` runs the whole chain in order.

## What "identical" means here

- **Features:** `marts.training_dataset` content is fingerprinted (md5 over all rows ordered by id); `reproduce_check.py` and `test_feature_rebuild_is_bit_identical` compare fingerprints across rebuilds. The split is a hash of `SK_ID_CURR`.
- **Metrics:** LightGBM is run deterministic with a fixed seed, so `reproduce_check.py` compares ROC-AUC/PR-AUC/Brier/KS to `1e-6`. This has been verified only on the machine/library versions in `requirements.txt` (and the pins in `infra/requirements-serving.txt`); other platforms may differ in the last digits.
- **Timings** (training, feature pipeline, inference latency) are *measured*, not reproducible bit-for-bit; expect machine-dependent variation.
