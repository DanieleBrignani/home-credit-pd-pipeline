# Production readiness review

**Verdict: this project is NOT production-ready.**

Reasons, in order of weight:

1. **Results exist now, but only from one machine and one run.** The pipeline has been run end to end on the real Home Credit files (row counts match the published counts), and the README results are generated from `reports/*.json`. They come from a single laptop run, recorded at commit `5980d74` plus uncommitted fixes to dbt tests, compose and scripts (none touch features or the model). No second environment, CI runner or independent reviewer has reproduced them, and see category 28 for the reproduction check.
2. Most categories below are `PARTIAL`: the mechanism exists and is tested, but it has never run against real data, real traffic, a remote CI runner, or a real incident.
3. One category (`Scalability`) is `FAIL`, and several `PARTIAL`s contain known gaps (no TLS, no secret manager, no off-machine backups).

This is a reviewed, tested **engineering reference implementation**, not a deployable lending service. It estimates PD and makes no lending decision.

## Method

Each status needs concrete repository evidence; `PASS` was granted only where a test or a command output (cited) demonstrates the requirement *as stated*, and the limitation field says what is not covered. Fixture-based evidence is labelled as such.
Statuses reflect the repository at review time; see "Evidence snapshot" at the bottom.

## Findings fixed during this review

The first pass found three **critical FAILs**. They were implemented before closing:

| Initial finding | Fix | Evidence |
|---|---|---|
| `/predict` had no authentication | `X-API-Key` (SHA-256 compared in constant time); app refuses to start with no key configured; rejected requests are neither scored nor audited | `tests/test_api.py::test_predict_requires_valid_api_key`, `::test_app_refuses_to_start_without_auth_configuration`; container smoke `unauthenticated_rejected: true` |
| No backup / recovery path for the audit trail and registry | `scripts/backup_restore.py` with a *restore drill* into a scratch DB comparing row counts | drill output `RESTORE DRILL PASSED` (fixture DB) |
| Dependencies only lower-bounded → rebuild could differ | `requirements.lock` (exact versions) and exact pins for the serving image | `requirements.lock`, `infra/requirements-serving.txt` |

Also fixed: concurrent-loader race (Postgres advisory lock in ingest), silent schema additions (now logged), promotion policy flaw (first run could promote the boosted model with nothing to compare against; now the baseline bootstraps and the boosted model must earn promotion, tested), host-port assumptions in compose (configurable).

## Summary

| # | Category | Status | Critical? |
|---|---|---|---|
| 1 | Data source integrity | PARTIAL | yes |
| 2 | Raw data immutability | PARTIAL | yes |
| 3 | Data contracts | PARTIAL | |
| 4 | Schema evolution | PARTIAL | |
| 5 | Idempotency | PASS | yes |
| 6 | Lineage | PARTIAL | yes |
| 7 | Database design | PARTIAL | |
| 8 | Data quality | PARTIAL | |
| 9 | Orchestration | PARTIAL | |
| 10 | Error handling | PARTIAL | |
| 11 | Retry strategy | PARTIAL | |
| 12 | Security | PARTIAL | yes |
| 13 | Secret management | PARTIAL | yes |
| 14 | Privacy | PARTIAL | yes |
| 15 | Testing | PASS | yes |
| 16 | Integration testing | PASS | |
| 17 | End-to-end testing | PASS | |
| 18 | CI/CD | PARTIAL | |
| 19 | Containerization | PARTIAL | |
| 20 | Observability | PARTIAL | |
| 21 | SLIs | PARTIAL | |
| 22 | SLOs | PARTIAL | |
| 23 | Performance | PASS | |
| 24 | Scalability | **FAIL** | |
| 25 | Recovery | PARTIAL | yes |
| 26 | Rollback | PASS | yes |
| 27 | Documentation | PARTIAL | |
| 28 | Reproducibility | PARTIAL | yes |
| 29 | Cost awareness | PARTIAL | |

No *critical* category is `FAIL`; every critical `PARTIAL` is a reason the verdict above stands.

---

## 1. Data source integrity — PARTIAL
- **Requirement:** ingested files are the canonical public files, verifiably unaltered.
- **Evidence:** `ingest.validate_file` checks required columns, duplicate headers, sha256 against `MANIFEST.sha256`; `load_table` checks published row counts (`contracts.py`). `meta.ingest_batches` records sha256/rows/commit.
- **Test:** `tests/test_contracts_metrics.py` (missing file/column, duplicate header, manifest lock, mutation detected); `test_integration.py::test_raw_loaded_verbatim_and_lineage_recorded`.
- **Real-data evidence:** all eight real files passed the contract and their row counts equal the published counts exactly (`meta.ingest_batches`; `reports/metrics.json` records `CANONICAL`).
- **Limitation:** the lock is trust-on-first-use — Kaggle publishes no checksum we compare against, so a corrupted first download would be locked in; row counts are the only external reference.
- **Required improvement:** record known-good checksums from a second independent download; run on the real files.

## 2. Raw data immutability — PARTIAL
- **Requirement:** raw inputs cannot be silently changed.
- **Evidence:** a changed file for a locked name raises `ContractViolation` (exit code 2); `raw.*` is a verbatim TEXT copy; `data/raw/*.csv` is git-ignored but hashed.
- **Test:** `test_manifest_locks_first_seen_hash_and_detects_mutation`.
- **Limitation:** protection is application-level. A new file hash drops and recreates the `raw` table; there is no read-only DB role, and anyone with DB access can alter `raw.*`.
- **Required improvement:** separate roles (loader writes `raw`, dbt/API read-only), append-only raw with batch ids.

## 3. Data contracts — PARTIAL
- **Requirement:** explicit expectations for each source (grain, keys, columns).
- **Evidence:** `contracts.py` (grain, PK, FK, required columns, row counts); grain/cardinality table in `docs/feature_lineage.md`; dbt `unique`/`not_null`/`relationships` tests.
- **Test:** contract tests above; dbt build (fixtures) passes all tests.
- **Limitation:** no dtype or value-range contract at ingest (casting errors surface in dbt); real-data behaviour (e.g. duplicate-key groups in installments) unobserved.
- **Required improvement:** typed expectations (e.g. Great Expectations or dbt contracts) validated on the real files.

## 4. Schema evolution — PARTIAL
- **Requirement:** upstream changes are detected and handled predictably.
- **Evidence:** additive columns are loaded to raw and logged; removed/renamed required columns fail the contract; dbt selects explicit columns so additions are harmless; `feature_schema_version` hash changes with the feature set, and promotion gate 1 blocks incompatible models.
- **Test:** `test_additive_column_is_reported_not_fatal`, `test_missing_required_column_fails`, `test_schema_version_changes_with_pipeline_version`, `test_incompatible_feature_schema_blocks_promotion`.
- **Limitation:** no migration tooling; a breaking feature change needs manual version bump of `FEATURE_PIPELINE_VERSION`; API has no multi-version support.
- **Required improvement:** versioned feature views and a side-by-side API version during migrations.

## 5. Idempotency — PASS
- **Requirement:** re-running any stage gives the same state without duplication.
- **Evidence:** ingest skips an already-loaded sha256 and takes an advisory lock per table; dbt rebuilds tables from raw; split is a hash of the id; content fingerprint of `training_dataset` after two rebuilds is identical.
- **Test:** `test_integration.py::test_ingest_is_idempotent`, `::test_feature_rebuild_is_bit_identical`, `::test_split_is_deterministic_hash_of_id`.
- **Limitation:** verified on fixtures; the advisory lock is not tested under real concurrency; `train` appends new registry versions per run by design.
- **Required improvement:** concurrency test; dedupe training runs by (dataset, code) hash.

## 6. Lineage — PARTIAL
- **Requirement:** every output traceable to inputs and code.
- **Evidence:** `meta.ingest_batches`; generated `docs/feature_lineage.md` with source/transform/availability per feature (CI fails if stale; test asserts every feature is documented); MLflow tags (git commit, dataset version, feature pipeline/schema versions, feature-table fingerprint); audit rows carry model and feature-schema versions.
- **Test:** `test_leakage.py::test_lineage_doc_covers_every_feature`; `test_integration.py::test_train_register_and_serve_from_registry`.
- **Limitation:** the repository has **no commits yet**, so the recorded git commit is `unknown`/dirty until it does; no dbt docs graph generated; no column-level lineage tool.
- **Required improvement:** commit, tag releases, fail training on a dirty tree, publish `dbt docs`.

## 7. Database design — PARTIAL
- **Requirement:** sensible schemas, keys, indexes, privileges.
- **Evidence:** layered schemas (`raw`, `staging`, `intermediate`, `marts`, `meta`, `audit`); `audit.predictions` has a primary key, check constraint on probability range and a time index; marts keys tested unique.
- **Test:** dbt tests; `test_integration` audit assertions.
- **Limitation:** no indexes on `marts.applicant_history_features(sk_id_curr)` beyond what a lookup needs (it is not declared as a PK because dbt builds it with CTAS); a single DB user owns everything, so `revoke update, delete` on the audit table is not enforceable; no partitioning/retention.
- **Required improvement:** role separation, explicit index/PK on the lookup table, partitioned audit with retention.

## 8. Data quality — PARTIAL
- **Requirement:** automated checks on content, failing the pipeline when violated.
- **Evidence:** dbt tests (unique/not-null/accepted values/relationships) plus singular tests (shares in [0,1], non-negative counts, plausible default rate, no orphan history); dbt failures stop the flow (non-retryable).
- **Test:** `dbt build` on fixtures; pipeline run on fixtures.
- **Real-data evidence:** on the real data the dbt tests initially **failed**: one on container shared memory, and one test of mine that wrongly assumed the history tables cover only training applicants. After fixing both, the full dbt build ran on the real data (about 7 minutes) and passed.
- **Limitation:** the plausibility thresholds were written before seeing the data and not tuned; no anomaly/volume monitoring across loads.
- **Required improvement:** add freshness and volume checks; review thresholds with the data owner.

## 9. Orchestration — PARTIAL
- **Requirement:** a scheduled, observable, dependency-ordered pipeline.
- **Evidence:** Prefect flow `creditrisk.flows` (validate → ingest → dbt → train → promote), run end to end on fixtures; stage timings written to `reports/runtime.json`.
- **Test:** executed on the fixture DB; retry policy unit-tested.
- **Limitation:** no deployment, schedule, alerting on failure, or Prefect server in the compose stack.
- **Required improvement:** Prefect deployment/work pool or Airflow DAG with schedule and failure notifications.

## 10. Error handling — PARTIAL
- **Requirement:** failures are contained, classified and safe.
- **Evidence:** API: audit failure → 503 and no score; model not loaded → 503; inference errors → 500 without payload; validation → 422. Pipeline: contract violation exit code 2; dbt failures raise non-retryable errors.
- **Test:** `test_audit_failure_withholds_score`, `test_not_ready_without_model`, `test_forbidden_or_unknown_fields_rejected`.
- **Limitation:** no circuit breaker/timeouts on the DB calls; 422 responses are not counted in metrics; flow error paths other than the retry policy are not unit-tested.
- **Required improvement:** DB timeouts, request-level deadline, metrics for validation failures.

## 11. Retry strategy — PARTIAL
- **Requirement:** transient faults retried with backoff; deterministic faults not.
- **Evidence:** Prefect task retries with backoff for ingest/dbt; `retry_condition_fn` refuses `NonRetryable`.
- **Test:** `test_registry.py::test_flow_retry_policy_does_not_retry_deterministic_failures`.
- **Limitation:** the retry path was never triggered by a real transient failure; API-side retries to DB/MLflow are not implemented.
- **Required improvement:** fault-injection test (stop Postgres mid-run); client retry guidance and idempotency keys for `/predict`.

## 12. Security — PARTIAL
- **Requirement:** authenticated, least-privilege, minimal attack surface.
- **Evidence:** API key on `/predict` and `/model`, token on `/admin/reload`, `extra=forbid` input models with range limits, non-root container, no payload logging.
- **Test:** `test_predict_requires_valid_api_key`, `test_admin_reload_requires_token`; smoke `api_container_non_root: true`.
- **Limitation:** no TLS, no per-key rate limiting or quotas, no key rotation tooling, `/metrics` unauthenticated, no dependency/image vulnerability scanning, static admin token, model files are pickles (load only from a trusted registry).
- **Required improvement:** TLS/reverse proxy and mTLS or OAuth, rate limits, scanner in CI, signed model artifacts.

## 13. Secret management — PARTIAL
- **Requirement:** no secrets in code; managed storage and rotation.
- **Evidence:** secrets only via env; `.env` git-ignored; compose uses `:?` so missing secrets fail startup; API keys stored as hashes.
- **Test:** `test_app_refuses_to_start_without_auth_configuration`.
- **Limitation:** no secret manager or rotation; `.env` lives in a OneDrive-synced folder on this machine; the MLflow DB URI appears in the container command line; `API_KEY_LOCAL` plaintext sits in the local `.env` for the smoke test.
- **Required improvement:** secret manager, short-lived credentials, keep `.env` out of synced folders.

## 14. Privacy — PARTIAL
- **Requirement:** minimise and protect personal data.
- **Evidence:** request bodies never logged; audit stores only the listed fields plus an HMAC of the applicant id; unknown fields rejected; protected `CODE_GENDER` excluded.
- **Test:** `test_logs_never_contain_applicant_payload`, `test_audit_row_has_exact_fields_and_no_applicant_data`.
- **Limitation:** age is used as a feature; a single static HMAC salt; no retention policy, deletion/subject-access process or DPIA; training data held in plain Postgres; no fairness evaluation.
- **Required improvement:** retention and erasure policy, salt rotation, bias/adverse-impact audit, legal review of features.

## 15. Testing — PASS
- **Requirement:** unit tests for feature functions, schema, preprocessing, probability format; leakage tests.
- **Evidence:** `tests/test_features.py`, `test_contracts_metrics.py`, `test_leakage.py`, `test_api.py` (probability format), `test_registry.py`, `test_results_pipeline.py`.
- **Test:** `pytest -q` (counts in the snapshot below).
- **Limitation:** coverage not measured; no property-based or mutation tests; model-quality assertions on real data do not exist.
- **Required improvement:** coverage gate in CI, property-based tests for `build_features`.

## 16. Integration testing — PASS
- **Requirement:** raw → features, features → model, registry → inference.
- **Evidence:** `tests/test_integration.py` against live Postgres: loads fixtures, runs dbt, compares dbt installment aggregates with an independent pandas implementation, trains, registers, serves through the registry alias and checks the audit row.
- **Test:** same file (passes).
- **Limitation:** fixtures only; requires Docker; runs against a local MLflow sqlite store, not the server.
- **Required improvement:** run the same file against the real data subset in a nightly job.

## 17. End-to-end testing — PASS
- **Requirement:** sample applicant → API prediction.
- **Evidence:** in-process E2E in `test_api.py`; container-level E2E `scripts/smoke_stack.py` (compose stack, containerised registry, authenticated prediction, audit row, Prometheus scrape, Grafana dashboard) — output `artifacts/smoke_stack.json`, `SMOKE PASSED`.
- **Test:** the above.
- **Limitation:** the model is trained on fixtures; the smoke test proves wiring, not quality; it is not in CI.
- **Required improvement:** run the smoke test in CI with a pre-built fixture model.

## 18. CI/CD — PARTIAL
- **Requirement:** automated build/test on change; automated, gated delivery.
- **Evidence:** `.github/workflows/ci.yml` (lineage freshness, full pytest against a Postgres service, dbt parse, image builds).
- **Test:** none — the workflow has **never run on GitHub** (no remote, no commit); its steps were exercised locally.
- **Limitation:** no CD, no release tagging, no registry push, no real-data job (licence-gated).
- **Required improvement:** push, observe a green run, add image publishing and a gated deploy.

## 19. Containerization — PARTIAL
- **Requirement:** reproducible, minimal, safe images.
- **Evidence:** API and MLflow images build; compose stack ran healthy; API runs as non-root with a healthcheck; serving library versions pinned.
- **Test:** `scripts/smoke_stack.py`.
- **Limitation:** API image is large (about 1.6 GB: SHAP, MLflow, LightGBM); base images not pinned by digest; no image scanning/SBOM; the training pipeline runs on the host, not in a container.
- **Required improvement:** slim serving image, digest pins, scan in CI, containerised pipeline runner.

## 20. Observability — PARTIAL
- **Requirement:** metrics, dashboards and alerts covering behaviour and health.
- **Evidence:** Prometheus instruments (requests, latency, PD distribution, missingness, feature histograms, errors, model version); provisioned Grafana dashboard; 5 alert rules loaded by Prometheus.
- **Test:** smoke test confirms scrape, dashboard and rules.
- **Limitation:** alerts never fired; no tracing or central log store; no Alertmanager routing; dashboard not reviewed under real traffic.
- **Required improvement:** Alertmanager, structured JSON logs to a store, traces, alert drills.

## 21. SLIs — PARTIAL
- **Requirement:** measurable indicators of service quality.
- **Evidence:** `docs/slo.md` defines SLIs as PromQL/SQL over existing instruments.
- **Test:** the underlying metrics are asserted in `test_metrics_exposed`.
- **Limitation:** never measured over a period; audit-completeness reconciliation job is not implemented.
- **Required improvement:** implement the reconciliation, record a baseline.

## 22. SLOs — PARTIAL
- **Requirement:** agreed targets with error-budget policy.
- **Evidence:** proposed targets in `docs/slo.md`.
- **Test:** none possible without traffic.
- **Limitation:** targets are guesses, not derived from data; no error-budget policy or owner.
- **Required improvement:** set after a baseline period with stakeholders.

## 23. Performance — PARTIAL
- **Requirement:** measured latency/throughput of the real stack.
- **Evidence:** `scripts/benchmark_inference.py` measures p50/p95/p99 through uvicorn with real DB lookup and audit write, using test-split applicants; `make_results.py` publishes them.
- **Real-data evidence:** the benchmark ran against the real champion with real test-split applicants: 2000 sequential requests, no errors; latencies and the feature-pipeline and training times are in the README results block (from `reports/inference_benchmark.json`, `reports/runtime.json`, `reports/metrics.json`).
- **Test:** `python scripts/benchmark_inference.py` (re-runnable).
- **Limitation:** single client, single uvicorn worker, loopback, one laptop; no concurrent-load profile, so it measures latency, not capacity. The synchronous audit insert and the SHAP-free path are included; explanations are not benchmarked.
- **Required improvement:** concurrent-load test and a latency budget breakdown (validation, DB lookup, feature build, model, audit).

## 24. Scalability — FAIL
- **Requirement:** demonstrated handling of larger data and request volumes.
- **Evidence:** none beyond design intent (stateless API, pooled connections, features precomputed).
- **Test:** none; no load test, no large-data run.
- **Limitation:** one uvicorn worker, one Postgres, synchronous audit insert, all aggregates rebuilt in full each run, SHAP computed in-request when asked.
- **Required improvement:** load test, async/batched audit sink, incremental dbt models, horizontal API scaling test. Not a blocker for a reference implementation; a blocker for production.

## 25. Recovery — PARTIAL
- **Requirement:** restore service and data after loss, with known objectives.
- **Evidence:** `scripts/backup_restore.py drill` restores a dump into a scratch DB and compares row counts (`RESTORE DRILL PASSED` on the fixture DB); derived data rebuilds from raw (tested); runbook in `docs/runbook.md`.
- **Test:** the drill.
- **Limitation:** manual; backups stay on the same machine, unencrypted, unscheduled; RTO/RPO unmeasured; MLflow artifact volume not backed up by the script.
- **Required improvement:** scheduled encrypted off-site backups, artifact-store backup, measured RTO/RPO.

## 26. Rollback — PASS
- **Requirement:** champion/challenger, promotion decision, rollback mechanism.
- **Evidence:** MLflow aliases (`champion`, `challenger`, `previous_champion`); promotion rule with explicit gates in `registry.py`; every decision logged to `reports/model_decisions.jsonl`; rollback by alias repoint plus `/admin/reload`; `scripts/lifecycle_demo.py` runs a rollback drill.
- **Test:** `tests/test_registry.py` (promotion gates, bootstrap-then-challenge lifecycle, rollback, rollback-without-target).
- **Real-data evidence:** on the real registered versions the baseline was bootstrapped as champion, the LightGBM challenger passed every gate and was promoted, and the rollback drill moved champion to the previous version and rolled forward again (`reports/lifecycle_demo.json`, `reports/model_decisions.jsonl`).
- **Limitation:** rollback of *data/feature* changes is "rebuild from raw", not instant; time-to-rollback was not measured; the API reload after rollback was exercised only in the fixture smoke test.
- **Required improvement:** measure time-to-rollback including API reload on the real stack.

## 27. Documentation — PARTIAL
- **Requirement:** accurate docs for operators and reviewers.
- **Evidence:** README (all requested sections), `docs/feature_lineage.md`, `docs/monitoring.md`, `docs/runbook.md`, `docs/slo.md`, `docs/cost.md`, `docs/reproducibility.md`, `RECRUITER_SUMMARY.md`.
- **Test:** `test_lineage_doc_covers_every_feature`; `test_repo_readmes_have_no_unregistered_prose_numbers`.
- **Limitation:** README results block and model-level failure analysis are empty until real data is run; no architecture decision records.
- **Required improvement:** run on real data, regenerate, then write the model failure analysis from it.

## 28. Reproducibility — PARTIAL
- **Requirement:** same inputs and code give the same features and metrics.
- **Evidence (real data, this machine):** (a) a second full `dbt build` from the raw tables gave a feature-table fingerprint identical to the one recorded in `reports/metrics.json`; (b) `scripts/reproduce_check.py` re-trained both models from the recorded seed and configuration and matched the dataset version, the feature fingerprint, and every validation and test metric to 1e-6 (`artifacts/reproduce_check.log`, `artifacts/reproduce_metrics.json`). Supporting: hash split, deterministic LightGBM, `requirements.lock`, `scripts/verify_readme_numbers.py` ties every README number to `reports/*.json`.
- **Test:** `test_feature_rebuild_is_bit_identical` (fixtures), plus the two real-data runs above.
- **Limitation:** all reproduction was on one machine with the same library versions; the first reproduction attempt crashed on Windows temp-file cleanup (fixed) before comparing, so it was repeated; the lock file is Windows/Python 3.13 specific; timings are not reproducible bit-for-bit; the raw files were not re-ingested from an independent download; the committed code differs from the code that produced the results only by uncommitted dbt-test, compose and script fixes.
- **Required improvement:** reproduce on a second OS/CI runner from a Linux lock; re-download the data independently and compare checksums; commit a clean tree and re-run so metrics carry a clean commit hash.

## 29. Cost awareness — PARTIAL
- **Requirement:** cost drivers identified and measurable.
- **Evidence:** `docs/cost.md` lists drivers and the exact commands to measure them.
- **Test:** none.
- **Limitation:** no cost figures or cloud sizing exist and none are claimed; the large serving image and TEXT raw storage are known inefficiencies.
- **Required improvement:** measure on real data; slim image; cost model for a managed deployment.

---

## What would be needed to change the verdict

1. Obtain the Kaggle files and run `make all-real`; commit the code first so lineage records a real commit.
2. Pass `scripts/reproduce_check.py` and `scripts/verify_readme_numbers.py`.
3. Close the Security/Secret-management gaps (TLS, secret manager, rate limits, scanning).
4. Execute CI remotely; add off-site backups; run a load test.
5. Complete a model-risk review including fairness and out-of-time validation (impossible with this dataset's lack of dates).

## Evidence snapshot

Recorded at the time of writing:

- `pytest -q`: **96 passed** (unit, leakage, registry, in-process API E2E, results tooling, and DB integration tests on fixtures); log in `artifacts/pytest_final.log`.
- Real data: all eight files passed the contract with row counts equal to the published counts; the full `dbt build` on the real data passed twice (42 passed, 1 warning, 0 errors on the second run; the warning is the relationship check of bureau-balance rows against bureau).
- Real data: training, selection on validation, a single test scoring, registration, promotion and the rollback drill, SHAP, drift simulation, segment analysis and the inference benchmark all ran; outputs are in `reports/`.
- Reproduction: second `dbt build` gave an identical feature fingerprint; `scripts/reproduce_check.py` printed `REPRODUCED within tolerance 1e-06`.
- `scripts/verify_readme_numbers.py`: `OK` (blocks equal regenerated output; no unregistered numbers in prose).
- Fixture-only evidence (wiring, not model quality): `scripts/smoke_stack.py` `SMOKE PASSED`; `scripts/backup_restore.py drill` `RESTORE DRILL PASSED`. These were not repeated on the real database.
