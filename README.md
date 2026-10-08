# Home Credit — probability-of-default scoring pipeline

A reproducible data + ML pipeline that turns the relational Home Credit Default Risk files into a probability-of-default (PD)
estimate, with a registry-backed champion/challenger lifecycle, an audited inference API, and monitoring.

> **Scope.** The system estimates probability of default. It does **not** make lending decisions, set prices or limits, or
> replace underwriting policy, and it is not a credit-approval platform.
>
> **Status of results (read this first).** All code, tests, infrastructure and documentation in this repository are
> implemented and exercised on small *test-only* fixtures. The real Kaggle files are licence-gated and are not in this
> repository, so the **results block below is generated only when real data has been run** (`scripts/make_results.py` refuses
> to publish anything computed on fixtures). Until then it says so explicitly. See [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md)
> for an evidence-based audit of what is and is not production-grade.

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
**Not generated yet.** The real Home Credit files have not been ingested, and `scripts/make_results.py` refuses to publish metrics from
fixtures. To produce this block from real data run, in order: `make all-real` (see [docs/reproducibility.md](docs/reproducibility.md)).
<!-- RESULTS:END -->

## Inference

`POST /predict` takes `sk_id_curr` plus application-time fields and returns `prediction_id`, `probability_of_default`, `model_version`,
`feature_pipeline_version`, `timestamp` (plus `feature_schema_version` and whether history features were found). History features come from
`marts.applicant_history_features`; ratios come from `build_features`. The model is the registry **champion** alias; `POST /admin/reload`
(token-protected) re-resolves it after a promotion or rollback. Request bodies are never logged — logs carry only prediction id, model version and
latency. Unknown or protected fields (e.g. `code_gender`, `target`, post-outcome dates) are rejected with `422`.

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
end-to-end API tests (sample applicant → prediction, audit row, no payload in logs, fail-closed audit, forbidden fields rejected). Integration tests need the
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

Engineering failures encountered during the build are in the next section. **Model-level failure analysis (error segments, calibration by group, score-threshold
trade-offs) can only be written from real-data results and is intentionally not filled in until they exist.** Known structural weak points regardless of results:
heavy dependence on opaque external scores, thin-file applicants without history, and no fairness audit.

## What Failed and What I Changed

Real problems hit while building and testing on fixtures:

1. **Dependency conflict** — the newest SQLAlchemy broke MLflow's tracking store at import time. Pinned SQLAlchemy below the breaking release.
2. **dbt source tests failed** on upper-case raw columns (Postgres folds unquoted identifiers). Enabled identifier/column quoting on the source.
3. **A bad environment default** — `.env` pointed at an MLflow server that was not running, so training stalled in HTTP retries before doing any work. Fixture runs now use an explicit local store, and the failure mode is documented.
4. **Fixture output could be mistaken for a result** — the training script originally wrote the calibration plot into `reports/` even for fixture runs. Outputs now go next to the `--out` path, and `make_results.py` refuses anything not marked canonical.
5. **Registry vs. non-canonical data** — promotion originally had no provenance check; added a canonical-data gate (canonical Kaggle files), covered by a test.

## Limitations

- Results depend on the real files being supplied by the user; none are bundled.
- In-time split; no out-of-time validation; no fairness or adverse-impact analysis; age is used as an input.
- Single-node Postgres and a single API process; no autoscaling, no managed secrets, no TLS (local-development defaults).
- Drift and performance monitoring for real deployments are unproven: only the instrumentation and alarm logic are tested.
- The system is a PD estimator, not a decision engine; thresholds shown are analytical, not policy.

## Final Answer

Yes **structurally**: the repository shows that raw relational files can be turned into a leakage-audited, reproducible feature table, a registry-managed model and an audited
inference service whose monitoring distinguishes simulated checks from outcome-dependent ones, with tests demonstrating each control. Whether the resulting PD
is *good enough* is an empirical question answered only by the generated results block above once real data has been run; until that block is populated, this README
makes no performance claim. And "safely deployed" is bounded by [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md).

## Quick start

```bash
cp .env.example .env            # then set strong local values
docker compose up -d postgres
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt && .venv/Scripts/pip install -e .
# place the Kaggle CSVs in data/raw/, then:
make all-real
```
