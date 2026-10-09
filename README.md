# Home Credit — probability-of-default scoring pipeline

A reproducible data + ML pipeline that turns the relational Home Credit Default Risk files into a probability-of-default (PD)
estimate, with a registry-backed champion/challenger lifecycle, an audited inference API, and monitoring.

> **Scope.** The system estimates probability of default. It does **not** make lending decisions, set prices or limits, or
> replace underwriting policy, and it is not a credit-approval platform.
>
> **Status of results.** The pipeline has been run end to end on the **real Home Credit files** (row counts verified against the published
> counts; sha256 locked). Every number in the results block comes from `reports/*.json` written by repository commands, and
> `scripts/verify_readme_numbers.py` checks that. The Kaggle files themselves are licence-gated and are not in the repository; fixtures in
> `tests/` are test-only. See [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md): working results do **not** make this a production system.

## Business Question

Can historical applicant and credit-behaviour data be transformed into a reproducible risk-scoring pipeline that estimates
probability of default and can be safely deployed and monitored?

## Problem

Default is rare, the signal is spread over seven relational tables at different grains (applications, bureau credits, monthly bureau
status, previous applications, installment payments, card months, POS/cash months), and many columns describe the future relative
to the application (realised repayment dates). A naive join-everything-and-fit approach leaks outcome information and fans out
rows. The hard part is the *data engineering*: correct grain, correct time boundaries, reproducibility and auditability.

## Objective

1. Ingest the real relational files immutably with lineage.
2. Build a leakage-audited feature table with explicit grain and cardinality.
3. Train a logistic-regression baseline and a gradient-boosted model; select on measured validation evidence; keep the test split untouched.
4. Register models, promote by an explicit rule, roll back by repointing an alias.
5. Serve PD with an audit trail and expose drift/latency/error monitoring — while stating exactly what a static dataset can and cannot show.

## Data Architecture

```
data/raw/*.csv  (original Kaggle files, never modified; sha256 locked in data/raw/MANIFEST.sha256)
      │  creditrisk.ingest: contract check (columns, duplicates, sha256 immutability, published row counts)
      ▼
Postgres raw.*            verbatim TEXT copy; lineage in meta.ingest_batches (sha256, rows, git commit)
      │  dbt staging (typing, sentinel handling; forbidden columns are never selected)
      ▼
staging.stg_*             one model per source, source grain preserved
      │  dbt intermediate (temporal guard + aggregation to one row per SK_ID_CURR)
      ▼
intermediate.int_*_agg    five history aggregates (bureau+balance, previous, installments, card, POS)
      │  dbt marts
      ▼
marts.applicant_history_features   online lookup table used by the API
marts.training_dataset             application columns + history + target + deterministic hash split
      │  creditrisk.features.build_features (ratios; same function in training and serving)
      ▼
train → evaluate → MLflow registry (aliases: champion / challenger / previous_champion)
      ▼
FastAPI  POST /predict ──► audit.predictions (Postgres)     /metrics ──► Prometheus ──► Grafana
```

Orchestration is a Prefect flow (`creditrisk.flows`): validate → ingest → dbt build → train/register → promotion decision.
Contract violations and failed dbt tests are *not* retried (retrying cannot fix bad data); infrastructure errors are.

Source grain, keys, cardinalities and aggregation per table are tabulated in [docs/feature_lineage.md](docs/feature_lineage.md#2-source-grain-keys-cardinality-aggregation).

## Dataset

Home Credit Default Risk (Kaggle competition, public). Tables used: `application_train`, `bureau`, `bureau_balance`,
`previous_application`, `installments_payments`, `credit_card_balance`, `POS_CASH_balance`. `application_test` is ingested for lineage
completeness but has no labels and is never used for metrics. Download requires accepting the competition rules on Kaggle; place the
CSVs in `data/raw/`. The ingest step checks the published row counts and locks each file's sha256 on first sight; a changed file is a hard
failure. No synthetic applicants are used in any reported result — fixtures in `tests/fixtures` are test-only and are loaded into a
separate database.

## Feature Pipeline

Features are grouped into families, each with a rationale and a per-feature record of **source, transformation and availability at
prediction time** in [docs/feature_lineage.md](docs/feature_lineage.md) (generated from `features.py`; CI fails if it is stale):

| family | why it exists |
|---|---|
| A. application raw | core underwriting inputs known at submission, plus the external scores |
| B. affordability ratios | credit/income, annuity/income, implied term: the debt-service view an underwriter uses |
| C. bureau history | external debt, overdue amounts, history length, monthly delinquency |
| D. previous applications | prior refusal/approval behaviour at this lender (decision-time fields only) |
| E. installment behaviour | realised payment discipline: lateness, underpayment, recent window |
| F. credit-card behaviour | utilisation and delinquency |
| G. POS/cash behaviour | delinquency summaries only |
| H. history presence | separates "thin file" from "zero events" |

The block of anonymous building-characteristics columns and similar sparse blocks are deliberately not used (see Trade-offs).
Ratios live in Python (`build_features`) rather than SQL so that training and serving execute literally the same code; history
aggregates live in dbt so that the API can look them up instead of recomputing them from millions of rows.

## Leakage Controls

Three classes are audited in the leakage-audit section of [docs/feature_lineage.md](docs/feature_lineage.md#5-leakage-audit):

- **Target leakage** — `TARGET` is read only as the label; `assert_no_forbidden` guards the matrix; a static test scans every SQL model.
- **Temporal leakage** — every intermediate model filters to records dated on or before the application (`days_credit <= 0`,
  `days_decision < 0`, `days_instalment <= 0`, `months_balance < 0`). Fixtures contain deliberate future-dated trap rows, and an
  independent pandas re-implementation is compared to the dbt output.
- **Post-outcome information** — realised end/termination/first-due dates are never staged. The API rejects unknown fields (`extra=forbid`).

Residual risks are listed, not hidden: relative-day semantics cannot be verified against absolute dates, the split is not out-of-time, and the
external scores are opaque.

## Model Baseline

Logistic regression: median imputation with missing-indicators, standardisation, one-hot categoricals; regularisation strength chosen on
validation. It sets the bar the boosted model must beat.

## Final Model

LightGBM with early stopping on the validation split and a small, seeded random search; isotonic calibration is applied only if a two-fold
check *inside validation* shows a Brier improvement. The final model is whichever has the better **validation** ROC-AUC, and promotion to
champion additionally requires the registry rule below. Nothing in this selection reads the test split.

## Evaluation

Class imbalance is handled in evaluation, not by pretending the problem is balanced: **accuracy is deliberately not computed** (a constant
"no default" predictor scores high accuracy). Reported: ROC-AUC, PR-AUC (compare with the prevalence baseline), Brier score (compare with
predicting the base rate), KS, calibration slope/intercept, and precision/recall at thresholds chosen on validation and frozen before test
scoring: top-5/10/20% riskiest, and the F2-optimal threshold (recall-weighted because a missed default costs more than a manual review).
The test split is scored exactly once, after selection (`train.evaluate_test_once`), and a test asserts that selection code cannot receive it.

## Calibration

A PD that ranks well but is mis-scaled is not usable for provisioning or pricing. Calibration is checked with the calibration curve
(`reports/calibration_test.png`, quantile bins), the calibration slope/intercept and the Brier score against the base-rate Brier.
Isotonic regression is fitted on validation only.

## Results

<!-- RESULTS:START (generated by scripts/make_results.py; do not edit) -->
Source: `reports/metrics.json` (git `acca4b6941`, dataset `ds-73a3b3590c89`, feature schema `fs-508cbb598b`, feature-table fingerprint `83ca684bc5a8ed399e0c5ca1d81e8bdc`).

**Data used (canonical Kaggle files, row counts verified at ingest)**

| table | rows |
|---|---|
| POS_CASH_balance | 10,001,358 |
| application_test | 48,744 |
| application_train | 307,511 |
| bureau | 1,716,428 |
| bureau_balance | 27,299,925 |
| credit_card_balance | 3,840,312 |
| installments_payments | 13,605,401 |
| previous_application | 1,670,214 |

Hash split of labeled applications: train 215,874 / validation 45,622 / test 46,015. Default rate (test): 8.07%.

**Selected on validation evidence: `lightgbm`**

| model | split | ROC-AUC | PR-AUC | Brier | Brier (predict-prevalence) | KS | calib. slope | calib. intercept |
|---|---|---|---|---|---|---|---|---|
| Logistic regression (baseline) | valid | 0.7600 | 0.2438 | 0.06795 | 0.07445 | 0.3901 | 0.966 | -0.067 |
| Logistic regression (baseline) | test | 0.7663 | 0.2530 | 0.06730 | 0.07422 | 0.4008 | 0.995 | -0.012 |
| LightGBM (+ calibration if it helped on validation) | valid | 0.7827 | 0.2751 | 0.06644 | 0.07445 | 0.4248 | 0.976 | -0.037 |
| LightGBM (+ calibration if it helped on validation) | test | 0.7889 | 0.2847 | 0.06575 | 0.07422 | 0.4316 | 0.995 | 0.003 |

**Precision / recall at thresholds frozen on validation, applied to the untouched test set**

| model | rule | threshold | flag rate | precision | recall |
|---|---|---|---|---|---|
| logreg | top_5pct | 0.2508 | 5.1% | 33.7% | 21.5% |
| logreg | top_10pct | 0.1835 | 10.1% | 28.2% | 35.2% |
| logreg | top_20pct | 0.1211 | 20.0% | 21.8% | 53.9% |
| logreg | f2_optimal | 0.0892 | 29.4% | 18.2% | 66.1% |
| lightgbm | top_5pct | 0.2752 | 5.0% | 38.3% | 23.6% |
| lightgbm | top_10pct | 0.1940 | 9.8% | 30.6% | 37.2% |
| lightgbm | top_20pct | 0.1204 | 19.6% | 23.1% | 56.0% |
| lightgbm | f2_optimal | 0.0956 | 25.7% | 20.3% | 64.5% |

**Runtime and size**

| item | value | source |
|---|---|---|
| engineered features (aggregates + derived) | 61 | `features.feature_counts()` |
| model input columns | 90 | same |
| training wall-clock, all candidates incl. search | 397 s | `reports/metrics.json` |
| logistic regression fit (3-value grid) | 24 s | same |
| LightGBM random search (12 configs) | 363 s | same |
| feature pipeline (dbt build: staging, intermediate, marts, tests) | 251 s | `reports/runtime.json` |
| raw ingest, 8 files COPY into Postgres (sum of per-file load time) | 332 s | `reports/ingest_load.json` |
| `/predict` latency p50 / p95 / p99 | 57.8 / 67.4 / 71.5 ms | `reports/inference_benchmark.json` |
| benchmark conditions | 2,000 sequential requests, 0 errors, model v6 (lightgbm), 16 logical CPUs | same |

Timings depend on the machine; they are measured, not bit-reproducible. Metrics are reproducible (see `scripts/reproduce_check.py`).

**Drift monitor sanity check (static data; see Monitoring)**

| comparison | score PSI | worst monitored-feature PSI |
|---|---|---|
| baseline_train_vs_valid | 0.00028 | 0.00036 |
| baseline_train_vs_test | 0.00016 | 0.00039 |

| INJECTED perturbation (synthetic, not observed) | score PSI | score status | worst feature PSI | flagged by feature PSI (> 0.25)? |
|---|---|---|---|---|
| ext_source_2 shifted down 0.15 | 0.0086 | stable | ext_source_2 = 2.058 | yes |
| credit_income_ratio inflated x1.5 | 0.0004 | stable | credit_income_ratio = 0.337 | yes |
| ext_source_1 missingness +30pp | 0.0011 | stable | ext_source_1 = 0.071 | no |

**Champion / challenger / rollback drill** (`reports/lifecycle_demo.json`, decisions in `reports/model_decisions.jsonl`)

- 2026-10-08T21:00:10Z version 1: **PROMOTED**
  - gate0_canonical_data: pass (tag=True)
  - gate1_feature_schema: pass (model=fs-508cbb598b current=fs-508cbb598b)
  - bootstrap_min_auc: pass (auc=0.7600336802442893)
  - bootstrap_calibration: pass (slope=0.966268302688859)
- 2026-10-08T21:00:12Z version 2: **PROMOTED**
  - gate0_canonical_data: pass (tag=True)
  - gate1_feature_schema: pass (model=fs-508cbb598b current=fs-508cbb598b)
  - gate2_auc_gain: pass (challenger=0.7820 champion=0.7600 need +0.002)
  - gate3_brier: pass (challenger=0.06648 champion=0.06795)
  - gate4_ks: pass (challenger=0.4255 champion=0.3902)
- 2026-10-09T09:35:54Z version 4: **REJECTED**
  - gate0_canonical_data: pass (tag=True)
  - gate1_feature_schema: pass (model=fs-508cbb598b current=fs-508cbb598b)
  - gate2_auc_gain: FAIL (challenger=0.7820 champion=0.7820 need +0.002)
  - gate3_brier: pass (challenger=0.06637 champion=0.06648)
  - gate4_ks: pass (challenger=0.4247 champion=0.4255)
- 2026-10-09T09:53:26Z version 6: **REJECTED**
  - gate0_canonical_data: pass (tag=True)
  - gate1_feature_schema: pass (model=fs-508cbb598b current=fs-508cbb598b)
  - gate2_auc_gain: FAIL (challenger=0.7827 champion=0.7820 need +0.002)
  - gate3_brier: pass (challenger=0.06644 champion=0.06648)
  - gate4_ks: pass (challenger=0.4248 champion=0.4255)
- 2026-10-09T09:54:20Z version 6: **OVERRIDE_PROMOTED** (operator override, reason: re-baseline: pipeline made independent of physical row order; same data, metrics differ from v2 only by sampling noise; gates overridden: gate2_auc_gain)
  - gate0_canonical_data: pass (tag=True)
  - gate1_feature_schema: pass (model=fs-508cbb598b current=fs-508cbb598b)
  - gate2_auc_gain: FAIL (challenger=0.7827 champion=0.7820 need +0.002)
  - gate3_brier: pass (challenger=0.06644 champion=0.06648)
  - gate4_ks: pass (challenger=0.4248 champion=0.4255)
- rollback drill: champion v6 -> v2, then rolled forward to v6

**Concurrent load test** (`reports/load_test.json`; one machine shared by client, API workers and Postgres; rate limiter off; synchronous audit insert included)

| API workers | client concurrency | requests | errors | throughput (req/s) | p50 / p95 / p99 (ms) |
|---|---|---|---|---|---|
| 1 | 16 | 3,000 | 0 | 22.0 | 738.9 / 1044.2 / 1139.5 |
| 4 | 16 | 3,000 | 0 | 34.3 | 264.5 / 674.6 / 894.3 |

**Container smoke test on the real stack** (`reports/smoke_stack_real.json`): TLS proxy, authentication, audit row, blocked operational endpoints, Prometheus and Grafana all checked. Measured rollback (alias flip, API reload, new version observed): 21.4 s; roll-forward: 21.57 s.

**Backup/restore drill on the real database** (`reports/backup_drill.json`): PASSED; irreproducible data only (audit, registry, lineage, marts), dump 80.3 MB, backup 13.1 s, restore and verification 28.5 s, all table row counts equal.

**Where the champion is weaker: segment analysis on the test split** (descriptive only; `reports/failure_analysis.json`; AUC shown only for segments with enough rows and both classes)

| segment | n | observed default rate | mean predicted PD | predicted / observed | ROC-AUC |
|---|---|---|---|---|---|
| contract_type: Cash loans | 41,615 | 8.4% | 8.2% | 0.98 | 0.788 |
| contract_type: Revolving loans | 4,400 | 5.1% | 5.5% | 1.07 | 0.784 |
| bureau_history: has bureau history | 39,355 | 7.7% | 7.6% | 0.98 | 0.789 |
| bureau_history: no bureau history | 6,660 | 10.1% | 10.2% | 1.01 | 0.779 |
| previous_app_history: has previous applications | 43,502 | 8.2% | 8.1% | 0.99 | 0.787 |
| previous_app_history: no previous applications | 2,513 | 5.6% | 5.7% | 1.02 | 0.810 |
| ext_scores_missing: 0 of 3 missing | 16,373 | 7.5% | 7.2% | 0.95 | 0.789 |
| ext_scores_missing: 1 of 3 missing | 24,011 | 7.9% | 8.0% | 1.01 | 0.792 |
| ext_scores_missing: 2 of 3 missing | 5,608 | 10.3% | 10.2% | 0.99 | 0.769 |
| ext_scores_missing: 3 of 3 missing | 23 | 0.0% | 6.0% | n/a | n/a |
| age_band: 30-39 | 12,253 | 9.3% | 9.5% | 1.01 | 0.787 |
| age_band: 40-49 | 11,527 | 8.1% | 7.5% | 0.93 | 0.804 |
| age_band: 50-59 | 10,298 | 5.9% | 6.0% | 1.02 | 0.765 |
| age_band: 60+ | 5,134 | 4.7% | 4.7% | 1.01 | 0.754 |
| age_band: <30 | 6,803 | 11.6% | 11.6% | 1.00 | 0.762 |
<!-- RESULTS:END -->

## Inference

`POST /predict` takes `sk_id_curr` plus application-time fields and returns `prediction_id`, `probability_of_default`, `model_version`,
`feature_pipeline_version`, `timestamp` (plus `feature_schema_version` and whether history features were found). History features come from
`marts.applicant_history_features`; ratios come from `build_features`. The model is the registry **champion** alias; `POST /admin/reload`
(token-protected) re-resolves it after a promotion or rollback. Request bodies are never logged — logs carry only prediction id, model version and
latency. Unknown or protected fields (e.g. `code_gender`, `target`, post-outcome dates) are rejected with `422`.

**Network and abuse controls.** Public traffic enters through a TLS reverse proxy (Caddy, local-CA certificate: encryption for development and staging, not a publicly trusted certificate);
the proxy does not expose `/admin` or `/metrics`, and the API port is bound to the host loopback only. Each API key has a token-bucket rate limit (`429` with `Retry-After`), database calls
have connect and statement timeouts, and validation failures are counted in the metrics.

**Audit.** Each prediction is written to `audit.predictions` (`prediction_id`, model version, request timestamp, feature schema version, PD, latency,
plus an HMAC of the applicant id — no applicant attributes) *before* the response is released. If the audit write fails the API returns `503` and withholds
the score (fail-closed).

## Monitoring

Exposed at `/metrics` and visualised in Grafana: prediction distribution, per-feature missingness, distributions of the key ratio features
(histograms comparable to the training reference via PSI), request latency, error counts by type, history-lookup misses, and the active model version.
Alert rules are in `infra/alerts.yml`.

**What is simulated vs. what needs production outcomes** ([docs/monitoring.md](docs/monitoring.md)):

| signal | static dataset | needs real production |
|---|---|---|
| input drift (PSI), score drift, missingness | monitor code is validated: train-vs-test PSI is a noise floor; *injected* shifts prove alarms fire | actual traffic over time |
| latency, errors, model version | measured on a local benchmark | real load and infrastructure |
| performance decay, concept drift, calibration drift | **cannot be observed** — no application dates, no outcomes arriving after scoring | realised defaults, which arrive months later |

No production drift has been observed because there is no production. Injected shifts are labelled `INJECTED` everywhere they appear.

## Explainability

SHAP (`explain.py`, `scripts/explain_global.py`, `POST /predict?explain_top_k=N`) shows how the model's output is attributed to its inputs on the log-odds
scale of the uncalibrated model. **SHAP explains model attribution, not causal relationships**: a large contribution from an input does not imply that changing
it would change an applicant's real risk, and attributions inherit any bias in the data. The API returns this caveat with every explanation.

## Testing

`pytest` runs: unit tests (feature functions, schema, preprocessing, probability format, contracts, metrics, PSI), leakage tests (forbidden fields cannot enter the
matrix, SQL scan, temporal-guard presence, selection code cannot see the test split), integration tests (raw → features with an independent pandas
cross-check, features → trained model, registry → inference, idempotent re-ingest, bit-identical rebuild), registry tests (promotion gates, rollback), and in-process
end-to-end API tests (sample applicant → prediction, audit row, no payload in logs, fail-closed audit, forbidden fields rejected). A vectorised feature builder is tested for bit-identical output against the previous reference implementation (random and edge-case inputs, and once over every real row). Statement coverage of the
package was measured at the time of review and is reported in PRODUCTION_READINESS.md. Integration tests need the
Postgres container and use a separate database. CI (`.github/workflows/ci.yml`) runs them against a Postgres service and builds the images.

## Trade-offs

- **Hash split instead of a time split.** The data has no absolute dates, so an out-of-time split is impossible; the hash split is deterministic and
  stable but yields an *in-time* estimate that will be optimistic for a real deployment.
- **Aggregates in dbt, ratios in Python.** Keeps the online path cheap and prevents training/serving skew, at the cost of the feature logic living in two languages.
- **Fail-closed audit.** Guarantees every released score is traceable; costs availability and adds a database write to the latency path.
- **Raw as TEXT.** Preserves the files verbatim and makes schema drift visible, at the cost of casting in staging.
- **Narrow feature set.** Dropping the sparse building block and hundreds of potential interactions loses a little accuracy (not measured here) in exchange for auditability.
- **Prefect over Airflow.** Lighter to run locally; the flow structure ports directly to an Airflow DAG.
- **Single Postgres.** Fine for a laptop-scale benchmark; see PRODUCTION_READINESS for scale limits.

## Failure Analysis

Measured on the held-out test split, descriptively (`scripts/failure_analysis.py`; the segment table is in the results block; nothing there was used to select a model):

- **Calibration is good on average and by segment**, with predicted-to-observed ratios close to one in almost every segment. The visible exception is a mild under-prediction in the 40-49 age band.
- **Ranking is weakest** for the oldest applicants, for the youngest, and for applicants missing two of the three external scores. These are the segments where the model has the least signal to separate on, not segments that were searched for the worst result.
- **Default rates differ strongly by age band**, and the model uses age. That is predictive here but regulatorily sensitive and has not had a fairness or adverse-impact review.
- **Opaque external scores dominate** the global SHAP ranking (`reports/shap_global_importance.md`). If their provider changes or discontinues them, performance will drop; this is a governance dependency, not just a modelling detail.
- **A score-only drift monitor misses single-feature shifts.** In the injected tests the score PSI stayed in the stable band while the per-feature PSI of the perturbed input flagged it clearly (see the drift table). The monitor therefore tracks features individually, not only the score.
- **Calibration did not need fixing:** the isotonic step was evaluated and not applied because it did not improve Brier inside validation.
- **Boosted vs. baseline:** the gain over logistic regression is real in every ranking metric, but the models are close on calibration; if explainability to an underwriter outweighed accuracy, the baseline is a defensible choice.

## What Failed and What I Changed

Real problems hit while building and testing on fixtures:

1. **Dependency conflict** — the newest SQLAlchemy broke MLflow's tracking store at import time. Pinned SQLAlchemy below the breaking release.
2. **dbt source tests failed** on upper-case raw columns (Postgres folds unquoted identifiers). Enabled identifier/column quoting on the source.
3. **A bad environment default** — `.env` pointed at an MLflow server that was not running, so training stalled in HTTP retries before doing any work. Fixture runs now use an explicit local store, and the failure mode is documented.
4. **Fixture output could be mistaken for a result** — the training script originally wrote the calibration plot into `reports/` even for fixture runs. Outputs now go next to the `--out` path, and `make_results.py` refuses anything not marked canonical.
5. **Registry vs. non-canonical data** — promotion originally had no provenance check; added a canonical-data gate (canonical Kaggle files), covered by a test.

Problems found only when the real data was run:

6. **Shared-memory exhaustion.** `dbt` tests on the 27-million-row bureau-balance table failed with "No space left on device" because the Postgres container's default shared memory is small. Set `shm_size` for the container.
7. **A wrong assumption in my own data test.** An orphan check flagged tens of thousands of history rows without a training applicant. They belong to `application_test` clients: the history tables cover train and test clients together, which the fixtures did not reproduce. The test now checks against train plus test.
8. **Misleading timing.** On the re-run, ingest was skipped by the idempotency check, so `runtime.json` showed a near-zero ingest time. The real load time now comes from `meta.ingest_batches` (`scripts/export_ingest_stats.py`).
9. **Noisy orchestrator logs.** Prefect's own telemetry logs sqlite "database is locked" errors during start-up; they are harmless but look like failures, so the pipeline outcome is judged by task states, not by log keywords.


10. **Results depended on physical row order.** Training read the feature table without an `ORDER BY`. After a rebuild (same content, same fingerprint) Postgres returned the rows in another physical order, and LightGBM's index-based row sampling produced a slightly different model, so the test AUC moved in the third decimal. Training now sorts by applicant id, and reproduction was re-verified after a fresh rebuild. Practical consequence: **differences between models smaller than about one thousandth of AUC are within the noise of this setup.**
11. **A start-up race that only appears with several workers.** The load test crashed the API when several workers ran the audit-table DDL at the same time (a Postgres "tuple concurrently updated" error). The DDL now runs under an advisory lock, with a regression test.
12. **Slow request path.** Profiling a single request showed that building the features with pandas dominated latency, not the model or the database. A NumPy rewrite is bit-identical to the old code on every real row and cut that step by more than an order of magnitude.
13. **Container training exhausted memory.** Running the whole pipeline inside Docker with the default memory limit crashed the Docker engine during data loading; the pipeline therefore ran on the host for the reported results. The container image exists and works up to that point, but needs a larger memory limit for training.
14. **A reproduction script crashed before comparing.** On Windows the temporary MLflow database was still locked during cleanup, so the first reproduction attempt produced no verdict. Fixed and repeated.
15. **An override was needed and is recorded as one.** After the determinism fix the new model could not beat the old champion by the promotion margin (they differ only by noise), so the rule correctly rejected it. It was promoted by an operator override that records the reason and the failed gate.

## Limitations

- The raw files are not bundled (licence); a reviewer must download them to reproduce.
- In-time split; no out-of-time validation; no fairness or adverse-impact analysis; age is used as an input.
- Single-node Postgres; the API scales to several workers on one machine but there is no autoscaling, no managed secret store, and TLS uses a local certificate authority.
- Drift and performance monitoring for real deployments are unproven: only the instrumentation and alarm logic are tested.
- The system is a PD estimator, not a decision engine; thresholds shown are analytical, not policy.

## Final Answer

Yes, with stated limits. On the real Home Credit data the pipeline rebuilds from raw files, produces a leakage-audited feature table, trains a baseline and a boosted model, selects on
validation, scores the untouched test split once, registers both, promotes through an explicit rule, serves the champion with an audit trail, and supports a tested rollback. The
boosted model beats the baseline on every ranking metric and is well calibrated overall (numbers in the results block). The estimate is an **in-time** estimate: there are no dates in the data, so
out-of-time performance, and any concept drift, are unknown. "Safely deployed" is bounded by [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md), which lists what is still missing.

## Quick start

```bash
cp .env.example .env            # then set strong local values
docker compose up -d postgres
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt && .venv/Scripts/pip install -e .
# place the Kaggle CSVs in data/raw/, then:
make all-real
```
