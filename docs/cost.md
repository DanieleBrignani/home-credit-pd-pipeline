# Cost awareness

No cloud deployment exists, so there are **no cost figures** here and none should be inferred. This lists the cost drivers and how to measure them.

| Driver | Why it matters | How to measure |
|---|---|---|
| Raw storage as TEXT | the largest tables are bureau balance and installments; TEXT is bulkier than typed columns | `select pg_size_pretty(pg_total_relation_size('raw.bureau_balance'))` and the rest of `raw.*` |
| Derived layers | staging/intermediate are full copies materialised as tables | same query on `staging.*`, `intermediate.*`; they can be views or dropped, since they rebuild from raw |
| Feature build compute | one full `dbt build` | `reports/runtime.json` → `feature_pipeline_dbt_build` |
| Training compute | model search over the training split | `reports/metrics.json` → `training_seconds_total` |
| Audit growth | one row per prediction | `select pg_size_pretty(pg_total_relation_size('audit.predictions'))`, rows per day from `request_ts` |
| Serving image | large because the API image carries SHAP, MLflow and LightGBM | `docker images creditrisk-api` |
| Synchronous audit write | adds a DB round trip to every request | `reports/inference_benchmark.json` (compare with and without) |

Levers if cost mattered: incremental dbt models instead of full rebuilds, typed/columnar raw storage, a slimmer serving image (drop SHAP unless explanations are requested), partitioning and retention for the audit table.
