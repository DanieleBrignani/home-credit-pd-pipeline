# SLIs and proposed SLOs

**Status: proposals.** No production traffic exists, so none of these has been measured against real operation, and no target below was derived from data.
The SLIs are real, queryable instruments; the SLO numbers are engineering starting points to be revised after a baseline period.

| SLI | Definition (PromQL / SQL) | Proposed SLO | Alert in `infra/alerts.yml` |
|---|---|---|---|
| Availability | share of `/predict` requests with status not `5xx`: `1 - sum(rate(pd_api_requests_total{status=~"5.."}[30d])) / sum(rate(pd_api_requests_total[30d]))` | 99.5% over 30 days | `HighErrorRate` (fast burn proxy) |
| Latency | `histogram_quantile(0.95, sum(rate(pd_api_request_seconds_bucket[5m])) by (le))` | p95 under 250 ms | `SlowPredictions` |
| Audit completeness | `count(audit.predictions)` in a window equals count of `status="200"` responses (reconciled by SQL job; not yet implemented) | 100% (fail-closed design) | none yet |
| Model freshness | `time() - pd_model_loaded_timestamp_seconds` and registry age | review monthly | none yet |
| Feature-lookup coverage | `rate(pd_history_lookup_miss_total)` / success rate | informational (thin-file share varies) | `HistoryLookupMisses` |
| Input stability | PSI of key features and score vs training reference | under 0.25 | not automated (offline `monitoring.psi`) |
| Outcome performance | AUC/calibration on realised defaults | cannot be defined until delayed outcomes exist | — |

Error budget policy, paging rotas and on-call are not defined.
