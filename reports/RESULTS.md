# Results (generated)

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

**Container smoke test on the real stack** (`reports/smoke_stack_real.json`): TLS proxy, authentication, audit row, blocked operational endpoints, Prometheus and Grafana all checked. Measured rollback (alias flip, API reload, new version observed): 8.82 s; roll-forward: 7.6 s.

**Backup/restore drill on the real database** (`reports/backup_drill.json`): PASSED; irreproducible data only (audit, registry, lineage, marts), dump 80.3 MB, backup 41.5 s, restore and verification 70.5 s, all table row counts equal.

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
