# Monitoring: what is simulated and what is not

| Signal | Source | Implemented | Can be shown on the static dataset? |
|---|---|---|---|
| Prediction (score) distribution | `pd_prediction_probability` histogram | yes | Only as a snapshot; no time axis |
| Feature missingness | `pd_feature_missing_total / pd_feature_observed_total` per feature | yes | Snapshot rates only |
| Feature distribution | fixed-bucket histograms for 4 key ratios + `monitoring.psi` | yes | PSI between random splits (noise floor) and **injected** shifts |
| PSI | `monitoring.psi`, `psi_from_counts` (bucket counts from Prometheus) | yes | Same as above |
| Latency / errors | `pd_api_request_seconds`, `pd_api_requests_total`, `pd_api_errors_total` | yes | Local benchmark only (`reports/inference_benchmark.json`) |
| Model version | `pd_model_info`, audit table | yes | Yes (registry state) |
| Performance / concept drift / calibration drift | none possible | **no** | **No** |

## Why performance drift cannot be shown here

Home Credit gives each applicant's history relative to *their own* application date and a single label. There is no calendar date, so the data cannot be replayed
as a time series, and no outcomes arrive after a deployment. Detecting concept drift needs (1) live scoring, (2) realised default flags that mature months later,
and (3) a join of the two via `audit.predictions.prediction_id`. The audit table is designed for that join; the join itself has never run on real outcomes.

## What `scripts/drift_simulation.py` does

1. **Baseline sanity** — PSI between train/valid/test. These are random hash splits of one snapshot, so values should be near zero; this validates the PSI code and gives a noise floor.
2. **Segment heterogeneity** — score PSI between contract-type segments. Real structure in the data, not drift over time.
3. **Injected shifts** — synthetic perturbations of real test rows to prove the monitor flags the right feature. Output is labelled `INJECTED` and must never be described as observed drift.

Rule of thumb used for status: PSI below 0.1 stable, 0.1–0.25 moderate, above 0.25 major. These are industry conventions, not thresholds calibrated on this portfolio.

## Alerts

`infra/alerts.yml` are *proposed* thresholds. They have never fired on real traffic. SLOs in PRODUCTION_READINESS.md are proposals for the same reason.
