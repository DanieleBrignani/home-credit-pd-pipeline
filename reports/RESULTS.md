# Results (generated)

Source: `reports/metrics.json` (git `5980d74063`, dataset `ds-73a3b3590c89`, feature schema `fs-508cbb598b`, feature-table fingerprint `83ca684bc5a8ed399e0c5ca1d81e8bdc`).

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
| Logistic regression (baseline) | valid | 0.7600 | 0.2438 | 0.06795 | 0.07445 | 0.3902 | 0.966 | -0.067 |
| Logistic regression (baseline) | test | 0.7663 | 0.2530 | 0.06730 | 0.07422 | 0.4008 | 0.995 | -0.012 |
| LightGBM (+ calibration if it helped on validation) | valid | 0.7820 | 0.2746 | 0.06648 | 0.07445 | 0.4255 | 0.976 | -0.032 |
| LightGBM (+ calibration if it helped on validation) | test | 0.7880 | 0.2841 | 0.06578 | 0.07422 | 0.4285 | 0.997 | 0.013 |

**Precision / recall at thresholds frozen on validation, applied to the untouched test set**

| model | rule | threshold | flag rate | precision | recall |
|---|---|---|---|---|---|
| logreg | top_5pct | 0.2508 | 5.1% | 33.6% | 21.4% |
| logreg | top_10pct | 0.1835 | 10.1% | 28.2% | 35.2% |
| logreg | top_20pct | 0.1211 | 20.0% | 21.8% | 53.9% |
| logreg | f2_optimal | 0.0892 | 29.4% | 18.2% | 66.1% |
| lightgbm | top_5pct | 0.2732 | 5.1% | 38.0% | 23.8% |
| lightgbm | top_10pct | 0.1943 | 9.8% | 30.6% | 37.1% |
| lightgbm | top_20pct | 0.1204 | 19.4% | 23.2% | 55.6% |
| lightgbm | f2_optimal | 0.0837 | 29.4% | 18.8% | 68.5% |

**Runtime and size**

| item | value | source |
|---|---|---|
| engineered features (aggregates + derived) | 61 | `features.feature_counts()` |
| model input columns | 90 | same |
| training wall-clock, all candidates incl. search | 1467 s | `reports/metrics.json` |
| logistic regression fit (3-value grid) | 75 s | same |
| LightGBM random search (12 configs) | 1388 s | same |
| feature pipeline (dbt build: staging, intermediate, marts, tests) | 421 s | `reports/runtime.json` |
| raw ingest, 8 files COPY into Postgres (sum of per-file load time) | 332 s | `reports/ingest_load.json` |
| `/predict` latency p50 / p95 / p99 | 77.3 / 188.0 / 218.8 ms | `reports/inference_benchmark.json` |
| benchmark conditions | 2,000 sequential requests, 0 errors, model v2 (lightgbm), 16 logical CPUs | same |

Timings depend on the machine; they are measured, not bit-reproducible. Metrics are reproducible (see `scripts/reproduce_check.py`).

**Drift monitor sanity check (static data; see Monitoring)**

| comparison | score PSI | worst monitored-feature PSI |
|---|---|---|
| baseline_train_vs_valid | 0.00041 | 0.00041 |
| baseline_train_vs_test | 0.00056 | 0.00056 |

| INJECTED perturbation (synthetic, not observed) | score PSI | score status | worst feature PSI | flagged by feature PSI (> 0.25)? |
|---|---|---|---|---|
| ext_source_2 shifted down 0.15 | 0.0062 | stable | ext_source_2 = 2.058 | yes |
| credit_income_ratio inflated x1.5 | 0.0009 | stable | credit_income_ratio = 0.337 | yes |
| ext_source_1 missingness +30pp | 0.0016 | stable | ext_source_1 = 0.071 | no |

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
- rollback drill: champion v2 -> v1, then rolled forward to v2

**Where the champion is weaker: segment analysis on the test split** (descriptive only; `reports/failure_analysis.json`; AUC shown only for segments with enough rows and both classes)

| segment | n | observed default rate | mean predicted PD | predicted / observed | ROC-AUC |
|---|---|---|---|---|---|
| contract_type: Cash loans | 41,615 | 8.4% | 8.2% | 0.98 | 0.787 |
| contract_type: Revolving loans | 4,400 | 5.1% | 5.6% | 1.08 | 0.782 |
| bureau_history: has bureau history | 39,355 | 7.7% | 7.6% | 0.98 | 0.788 |
| bureau_history: no bureau history | 6,660 | 10.1% | 10.1% | 1.00 | 0.779 |
| previous_app_history: has previous applications | 43,502 | 8.2% | 8.1% | 0.98 | 0.786 |
| previous_app_history: no previous applications | 2,513 | 5.6% | 5.7% | 1.02 | 0.809 |
| ext_scores_missing: 0 of 3 missing | 16,373 | 7.5% | 7.1% | 0.95 | 0.788 |
| ext_scores_missing: 1 of 3 missing | 24,011 | 7.9% | 8.0% | 1.01 | 0.791 |
| ext_scores_missing: 2 of 3 missing | 5,608 | 10.3% | 10.1% | 0.98 | 0.767 |
| ext_scores_missing: 3 of 3 missing | 23 | 0.0% | 6.2% | n/a | n/a |
| age_band: 30-39 | 12,253 | 9.3% | 9.4% | 1.01 | 0.786 |
| age_band: 40-49 | 11,527 | 8.1% | 7.5% | 0.92 | 0.802 |
| age_band: 50-59 | 10,298 | 5.9% | 6.0% | 1.02 | 0.766 |
| age_band: 60+ | 5,134 | 4.7% | 4.7% | 1.01 | 0.753 |
| age_band: <30 | 6,803 | 11.6% | 11.5% | 0.99 | 0.761 |
