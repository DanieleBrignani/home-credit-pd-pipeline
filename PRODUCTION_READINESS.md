# Production readiness review

**Verdict: this project is NOT production-ready.**

Reasons, in order of weight:

1. **Results exist now, but only from one machine and one run.** The pipeline has been run end to end on the real Home Credit files (row counts match the published counts), and the README results are generated from `reports/*.json`. They come from a single laptop run, recorded at commit `1277446` plus uncommitted fixes to dbt tests, compose and scripts (none touch features or the model). No second environment, CI runner or independent reviewer has reproduced them, and see category 28 for the reproduction check.
2. Most categories below are `PARTIAL`: the mechanism exists and is tested, but it has run on one machine against real data and in one CI run on a remote Linux runner, but never under real traffic, in a second environment, or through a real incident.
3. No category is `FAIL` any more, but several `PARTIAL`s contain known gaps: no managed secret store, TLS only from a local certificate authority, no off-machine backups, and a CI workflow that has never run on a remote.

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

Found later, on real data and under load (all fixed, each with a test or a measurement):

| Finding | Fix | Evidence |
|---|---|---|
| Models depended on the physical row order of the feature table (test AUC moved in the third decimal after a rebuild) | training reads rows `ORDER BY sk_id_curr` | reproduction verified after a fresh `dbt build`; `test_training_frame_is_ordered_independent_of_physical_row_order` |
| API failed to start with several workers (audit DDL race) | advisory lock around the DDL | `test_audit_ddl_is_safe_when_many_workers_start_at_once`; load test with four workers ran without errors |
| Request path dominated by pandas feature building (about 99 ms per single-row request) | NumPy implementation, bit-identical to the previous one | exact equality over all real rows and in `test_features.py`; about 6.5 ms per request afterwards |
| No index on the online lookup table | primary key on `marts.applicant_history_features` | profile: lookup about 4 ms |
| No TLS, rate limiting, query timeouts; validation errors invisible in metrics | Caddy TLS proxy, per-key token bucket, connect/statement timeouts, 422 counted | `test_rate_limit_per_key_returns_429_with_retry_after`, `test_validation_failures_are_counted_in_metrics`, real-stack smoke test |
| Backup copied multi-gigabyte rebuildable schemas | default backup covers only irreproducible data | drill on the real database |

MLflow 2.22.5 to 3.17.0 (done on a copy of the registry first, then on the real one after a verified backup). Differences that mattered, none of which the unit tests would have predicted:

| Difference found | Handling | Evidence |
|---|---|---|
| The 3.x server rejects any Host header other than localhost (DNS-rebinding protection), so the API could not reach it by service name | `--allowed-hosts mlflow:5000,localhost:*,127.0.0.1:*` | API loaded the registered model through the 3.x server |
| A bare path as artifact destination silently listed no files | destination passed as a URI (`file:///mlartifacts`) and explicit `--default-artifact-root` | artifact listing and model load |
| `log_model` now defaults to the `skops` format and rejects the model's own types | `serialization_format` set to cloudpickle explicitly (the previous default); same trust note as before | training/registration integration test |
| A fresh environment resolved pandas 3 (the requirements file only had a lower bound), which broke tests and would have broken pickled-model loading in the image | `pandas>=2.2,<3` | tests green on the 2.x pandas that the serving image pins |
| Registry schema migration is one-way | migrated a copy first; backup drill before the real migration | 6 versions, 6 runs and 3 aliases preserved; 300 real applicants scored identically by the old and new stacks (max absolute difference about 6e-17) |
| Smoke test could call the API before it finished loading the model | wait for `/health` before `/admin/reload` | real-stack smoke test passes |

Also fixed: concurrent-loader race (Postgres advisory lock in ingest), silent schema additions (now logged), promotion policy flaw (first run could promote the boosted model with nothing to compare against; now the baseline bootstraps and the boosted model must earn promotion, tested), host-port assumptions in compose (configurable).

## Summary

| # | Category | Status | Critical? |
|---|---|---|---|
| 1 | Data source integrity | PARTIAL | yes |
| 2 | Raw data immutability | PARTIAL | yes |
| 3 | Data contracts | PARTIAL | |
| 4 | Schema evolution | PARTIAL | |
| 5 | Idempotency | PASS | yes |
| 6 | Lineage | PASS | yes |
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
| 24 | Scalability | PARTIAL | |
| 25 | Recovery | PARTIAL | yes |
| 26 | Rollback | PASS | yes |
| 27 | Documentation | PARTIAL | |
| 28 | Reproducibility | PARTIAL | yes |
| 29 | Cost awareness | PARTIAL | |

Counts: 7 PASS, 22 PARTIAL, 0 FAIL. No *critical* category is `FAIL`; every critical `PARTIAL` is a reason the verdict above stands.

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

## 6. Lineage — PASS
- **Requirement:** every output traceable to inputs and code.
- **Evidence:** `meta.ingest_batches` (sha256, rows, load time); generated `docs/feature_lineage.md` with source, transformation and availability for every feature (CI fails if stale; a test asserts every feature is documented); MLflow tags (git commit, dataset version, feature pipeline and schema versions, feature-table fingerprint); audit rows carry model and feature-schema versions. The reported results carry a clean commit hash (`reports/metrics.json`).
- **Test:** `test_lineage_doc_covers_every_feature`; `test_train_register_and_serve_from_registry`.
- **Limitation:** later commits changed serving and feature-building code after the reported training run; the feature change is proven bit-identical (all real rows) but the commit in `metrics.json` is not the repository head. No dbt docs graph and no column-level lineage tool. The local history was rewritten once before any push; hashes stored in MLflow run tags and in files outside the repository carry the earlier values, mapped in `docs/commit_hash_map.json`.
- **Required improvement:** publish `dbt docs`; fail training on a dirty tree; re-run reporting from a tagged release.

## 7. Database design — PARTIAL
- **Requirement:** sensible schemas, keys, indexes, privileges.
- **Evidence:** layered schemas (`raw`, `staging`, `intermediate`, `marts`, `meta`, `audit`); `audit.predictions` has a primary key, a probability range check and a time index; the online lookup table now has a primary key (lookup measured at about 4 ms); marts keys tested unique.
- **Test:** dbt tests; `test_integration` audit assertions; profile of the request path.
- **Limitation:** a single database user owns everything, so `revoke update, delete` on the audit table is not enforceable; no partitioning or retention; audit growth is unbounded.
- **Required improvement:** role separation, partitioned audit with retention.

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
- **Evidence:** API: audit failure gives 503 and no score; model not loaded gives 503; inference errors give 500 without payload; validation gives 422 and is now counted; database calls have connect and statement timeouts. Pipeline: contract violation exits with code 2; dbt failures are non-retryable.
- **Test:** `test_audit_failure_withholds_score`, `test_not_ready_without_model`, `test_forbidden_or_unknown_fields_rejected`, `test_validation_failures_are_counted_in_metrics`.
- **Limitation:** no circuit breaker; timeouts were not fault-injected; flow error paths other than the retry policy are not unit-tested.
- **Required improvement:** fault-injection tests (database stopped mid-request), request deadline.

## 11. Retry strategy — PARTIAL
- **Requirement:** transient faults retried with backoff; deterministic faults not.
- **Evidence:** Prefect task retries with backoff for ingest/dbt; `retry_condition_fn` refuses `NonRetryable`.
- **Test:** `test_registry.py::test_flow_retry_policy_does_not_retry_deterministic_failures`.
- **Limitation:** the retry path was never triggered by a real transient failure; API-side retries to DB/MLflow are not implemented.
- **Required improvement:** fault-injection test (stop Postgres mid-run); client retry guidance and idempotency keys for `/predict`.

## 12. Security — PARTIAL
- **Requirement:** authenticated, least-privilege, minimal attack surface.
- **Evidence:** API key on `/predict` and `/model` (hash compared in constant time), per-key token-bucket rate limit, token on `/admin/reload`, input models with `extra=forbid` and range limits, non-root container, no payload logging, TLS proxy that does not expose `/admin` or `/metrics`; the API, Postgres, MLflow, Prometheus and Grafana ports are bound to the host loopback. The real-stack smoke test checked unauthenticated rejection, blocked endpoints and non-root.
- **Test:** `test_predict_requires_valid_api_key`, `test_rate_limit_per_key_returns_429_with_retry_after`, `test_admin_reload_requires_token`; `scripts/smoke_stack.py --real`.
- **Limitation:** the TLS certificate comes from a local certificate authority (not publicly trusted), no mutual TLS or OAuth, the rate limit is per API process (not shared across workers), no key-rotation tooling, a static admin token, the vulnerability scan runs in CI in report-only mode and does not gate the build; after the upgrade to MLflow 3.17 a local scan of the API and MLflow server images reports no HIGH or CRITICAL findings that have a fix available (this says nothing about unfixed or newly disclosed issues, and the scan only covers those two images), model files are pickles (load only from a trusted registry).
- **Required improvement:** publicly trusted certificate or load-balancer TLS, shared rate limiting, make the scan gating now that it is clean for fixable HIGH/CRITICAL findings, pin the scanner image, and scan the pipeline image too, signed model artifacts.

## 13. Secret management — PARTIAL
- **Requirement:** no secrets in code; managed storage and rotation.
- **Evidence:** secrets only via env; `.env` git-ignored; compose uses `:?` so missing secrets fail startup; API keys stored as hashes.
- **Test:** `test_app_refuses_to_start_without_auth_configuration`.
- **Limitation:** no secret manager or rotation; `.env` was synced to OneDrive while the project lived in a synced folder (the project has since been moved to a non-synced directory, but the file may remain in OneDrive's cloud recycle bin until emptied, and the local-development values in it were not rotated); the MLflow DB URI appears in the container command line; `API_KEY_LOCAL` plaintext sits in the local `.env` for the smoke test.
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
- **Requirement:** sample applicant to API prediction.
- **Evidence:** in-process end-to-end tests in `test_api.py`; container-level `scripts/smoke_stack.py --real` on the real database and the real champion: TLS proxy, unauthenticated rejection, authenticated prediction with a real test-split applicant, audit row, blocked operational endpoints, Prometheus scrape and rules, Grafana dashboard, non-root container (`reports/smoke_stack_real.json`, `SMOKE PASSED`). A fixture-mode variant exists for CI-style use.
- **Test:** the above.
- **Limitation:** one applicant per run; the smoke test is not in CI.
- **Required improvement:** run the smoke test in CI with a pre-built fixture model.

## 18. CI/CD — PARTIAL
- **Requirement:** automated build/test on change; automated, gated delivery.
- **Evidence:** `.github/workflows/ci.yml` (lineage freshness, full pytest against a Postgres service with coverage, dbt parse, builds of the API, MLflow and pipeline images, a report-only image scan). It has now run on GitHub Actions: run 1 passed `test` (about 3 min) and failed `docker-build` because I had written a non-existent tag for a third-party scan action; run 2 on commit `e2cbfb6`, after replacing that action with the scanner's container image, passed both jobs (`test` 2 min 35 s, `docker-build` 3 min 3 s, 3 min 7 s in total). Run 4 on `5ed98c5`, with the hardened API image, also passed both jobs (3 min 39 s in total). Runs 5 and 6 passed too; run 6 on `5c0c812` (MLflow 3.17, regenerated lock files) took 4 min 41 s. Of six runs on `main`, the only failure is run 1, caused by the wrong action tag.
- **Test:** the two GitHub Actions runs above (checked from the Actions page).
- **Limitation:** the CI uses fixtures only (the licence-gated Kaggle files are not in the repository); no CD, no release tagging, no image publishing, no deploy gate; the image scan is report-only (findings reviewed locally, see Security); the runner label is scheduled to move to a newer Ubuntu release and two actions are flagged for a Node.js deprecation (warnings only); no branch protection or required checks.
- **Required improvement:** protect `main` with required checks, tag releases, publish images, make the scan gating after reviewing the first report, pin action versions by commit.

## 19. Containerization — PARTIAL
- **Requirement:** reproducible, minimal, safe images.
- **Evidence:** API, MLflow and pipeline images build; the compose stack ran healthy on the real data; the API runs as non-root with a healthcheck; serving library versions are pinned; a Linux lock file taken from the pipeline image has the same versions of the key libraries as the Windows lock; images build on a clean Linux CI runner; the runtime API image has no package installer (removing pip, which bundles its own copies of urllib3 and msgpack, and upgrading one OS library and the upgrade to MLflow 3.17 took the local scan of the API image from 23 findings with a fix available to none; the MLflow server image has no pip and no fixable findings either), and a real-stack smoke test passed on those images.
- **Test:** `scripts/smoke_stack.py --real`.
- **Limitation:** the pipeline image ran up to data loading but **training inside Docker with the default memory limit crashed the Docker engine**, so the reported results come from a host run; the API image is large (about 1.6 GB); base images are not pinned by digest; the image scan runs in CI in report-only mode and does not gate the build.
- **Required improvement:** raise the container memory limit (about 12 GB) and re-run the pipeline in the container; slim serving image; digest pins; scanner run.

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

## 23. Performance — PASS
- **Requirement:** measured latency/throughput of the real stack.
- **Evidence:** `scripts/benchmark_inference.py` on the real champion with real test-split applicants (2000 sequential requests, no errors): p50 about 58 ms and p95 about 67 ms (`reports/inference_benchmark.json`; the README results block is generated from it). A profile of the request path found that feature building dominated and removed most of it (about 99 ms to about 6.5 ms per request, bit-identical output); database lookup is about 4 ms; model prediction about 14 ms for the 1785-tree champion. Feature-pipeline and training times are in `reports/runtime.json` and `reports/metrics.json`.
- **Test:** `python scripts/benchmark_inference.py` (re-runnable).
- **Limitation:** single client on one laptop, loopback, no TLS proxy in this path; the in-process components do not account for the whole median (HTTP handling, validation, audit commit and metric updates are not broken down); explanations (SHAP) are not benchmarked.
- **Required improvement:** a per-stage latency breakdown and a latency budget; benchmark through the proxy.

## 24. Scalability — PARTIAL
- **Requirement:** demonstrated handling of larger data and request volumes.
- **Evidence:** data volume: about 55 million source rows loaded, aggregated and trained on a single node (timings in the README results block). Request volume: `scripts/load_test.py` ran 3000 requests at concurrency 16 against one worker and four workers with no errors (`reports/load_test.json`): about 22 requests/s with one worker and about 34 with four, with p95 latency of roughly one second and 0.7 seconds respectively under that concurrency.
- **Test:** `python scripts/load_test.py`; `test_audit_ddl_is_safe_when_many_workers_start_at_once`.
- **Limitation:** scaling is clearly sub-linear (about 1.6x for four workers) because client, workers and Postgres share one machine, each request does a synchronous audit insert, and the rate limit is per process; this is a capacity probe of a laptop, not a sizing; no autoscaling, no multi-node test, dbt models are rebuilt in full every run.
- **Required improvement:** separate load generator and database hosts, asynchronous or batched audit sink, incremental dbt models, a capacity target derived from real traffic.

## 25. Recovery — PARTIAL
- **Requirement:** restore service and data after loss, with known objectives.
- **Evidence:** `scripts/backup_restore.py drill` on the **real database**: backup of the irreproducible data (audit trail, MLflow registry and runs, lineage, marts) restored into a scratch database with every table's row count equal (`reports/backup_drill.json`: about 80 MB, backup about 13 s, restore and verification about 28 s on this machine). Derived data rebuilds from the locked raw files (rebuild verified identical). Runbook in `docs/runbook.md`. A Docker engine crash during this work also showed the data volume surviving a full engine restart.
- **Test:** the drill.
- **Limitation:** manual and unscheduled; backups stay on the same machine, unencrypted; measured durations are not an RTO or RPO; the MLflow artifact volume (model files) is not covered by the database dump.
- **Required improvement:** scheduled encrypted off-site backups including the artifact volume, and agreed RTO/RPO.

## 26. Rollback — PASS
- **Requirement:** champion/challenger, promotion decision, rollback mechanism.
- **Evidence:** MLflow aliases (`champion`, `challenger`, `previous_champion`); a promotion rule with explicit gates; every decision logged with the gate values, including two rejections for insufficient gain and one operator override that records its reason and the failed gate; rollback by alias repoint plus `/admin/reload`. On the real stack the measured time from alias flip through API reload to the new version being served was 7.2 s, 21.4 s and 8.8 s in three measurements on the same laptop (the spread is not explained; the 21 s one was on a freshly recreated stack; the last was on MLflow 3.17), and in both a prediction was confirmed to come from the rolled-back model; roll-forward took about the same as each rollback (`reports/smoke_stack_real.json`). `scripts/lifecycle_demo.py` also runs the drill.
- **Test:** `tests/test_registry.py` (promotion gates, lifecycle, override, rollback); `scripts/smoke_stack.py --real`.
- **Limitation:** rollback of data or feature changes is "rebuild from raw", not instant; timing is one measurement on a laptop; the override path is trusted to operators with registry access.
- **Required improvement:** approval workflow for overrides; repeat the timing under load.

## 27. Documentation — PARTIAL
- **Requirement:** accurate docs for operators and reviewers.
- **Evidence:** README (all requested sections), `docs/feature_lineage.md`, `docs/monitoring.md`, `docs/runbook.md`, `docs/slo.md`, `docs/cost.md`, `docs/reproducibility.md`, `RECRUITER_SUMMARY.md`.
- **Test:** `test_lineage_doc_covers_every_feature`; `test_repo_readmes_have_no_unregistered_prose_numbers`.
- **Limitation:** README results block and model-level failure analysis are empty until real data is run; no architecture decision records.
- **Required improvement:** run on real data, regenerate, then write the model failure analysis from it.

## 28. Reproducibility — PARTIAL
- **Requirement:** same inputs and code give the same features and metrics.
- **Evidence (real data):** after a second full `dbt build` the feature-table fingerprint was identical to the recorded one; `scripts/reproduce_check.py` re-trained both models and matched the dataset version, the feature fingerprint and every validation and test metric to 1e-6, **after** training was made independent of physical row order (an earlier run showed that without this, a rebuilt table in another row order gave slightly different metrics). Also: hash split, deterministic LightGBM, `requirements.lock` and a Linux lock from the pipeline image with matching key-library versions, `scripts/verify_readme_numbers.py` tying README numbers to `reports/*.json`.
- **Test:** `test_feature_rebuild_is_bit_identical`, `test_training_frame_is_ordered_independent_of_physical_row_order`, and the real-data runs above.
- **Limitation:** all reproduction on one machine; the Linux lock was generated but not used to reproduce the results; the raw files were not re-downloaded independently; run-to-run noise from row order is about one thousandth of AUC, so smaller model differences are not meaningful; timings are not reproducible bit-for-bit.
- **Required improvement:** reproduce on a second OS or CI runner from the Linux lock; independent re-download and checksum comparison; tag a release and re-run reporting from it.

## 29. Cost awareness — PARTIAL
- **Requirement:** cost drivers identified and measurable.
- **Evidence:** `docs/cost.md` lists drivers and the exact commands to measure them.
- **Test:** none.
- **Limitation:** no cost figures or cloud sizing exist and none are claimed; the large serving image and TEXT raw storage are known inefficiencies.
- **Required improvement:** measure on real data; slim image; cost model for a managed deployment.

---

## What would be needed to change the verdict

1. (Done once, on one machine) obtain the Kaggle files and run the full chain; repeat from a tagged release on a second environment.
2. Pass `scripts/reproduce_check.py` and `scripts/verify_readme_numbers.py`.
3. Close the remaining Security and secret-management gaps (publicly trusted TLS, a secret manager, shared rate limiting, a vulnerability scan that actually runs).
4. Execute CI remotely; add off-site backups; run a load test.
5. Complete a model-risk review including fairness and out-of-time validation (impossible with this dataset's lack of dates).

## Evidence snapshot

Recorded at the time of writing:

- `pytest -q --cov=creditrisk`: **105 passed**; statement coverage of the package **87%**; log in `artifacts/pytest_cov_final.log`. Tests use fixtures; coverage is a statement count, not a measure of test quality.
- Real data: eight files passed the contract with row counts equal to the published counts; full `dbt build` passed (42 passed, 1 warning, 0 errors; the warning is the relationship check of bureau-balance rows against bureau, whose many orphan rows are documented in `docs/feature_lineage.md`).
- Real data: training, selection on validation, a single test scoring, registration, promotion decisions (including one operator override), rollback drill, SHAP, drift simulation, segment analysis, inference benchmark, concurrent load test, backup/restore drill and the container smoke test all ran; outputs are in `reports/`.
- Reproduction: fingerprint identical after a rebuild; metrics reproduced to 1e-6 (`artifacts/reproduce_check2.log`).
- `scripts/verify_readme_numbers.py`: `OK`.
- Image scan (local, Trivy, HIGH/CRITICAL with fixes available): API image 23 findings before hardening, 18 after hardening, 0 after the MLflow 3.17 upgrade; MLflow server image 0 after hardening. Repository visibility was set to private after it had briefly been public.
- CI: GitHub Actions run 2 on `e2cbfb6` and run 4 on `5ed98c5` (after the image hardening) both passed `test` and `docker-build` (run 4: 2 min 33 s and 3 min 35 s); run 6 on `5c0c812` (MLflow 3.17) also passed (4 min 41 s); run 1 had failed on a wrong action tag, since fixed. CI uses fixtures only.
- Not done: any run on a second machine, off-site backup, container training with enough memory, a scan of the pipeline image, independent data re-download, any use of the CI for real-data results.
