"""Feature definitions shared by training AND serving (single source of truth => no train/serve skew).

Layers:
  raw application columns   : selected in dbt/models/staging/stg_application.sql (available at application time)
  history aggregates        : dbt marts.applicant_history_features (also the online lookup table)
  derived ratios            : computed HERE by `build_features` (pure function, unit-tested)

Every feature has an entry in FEATURE_REGISTRY: family, source, transformation, availability. docs/feature_lineage.md
is generated from this registry (scripts/gen_feature_lineage.py) and tests assert registry == model columns.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config
from .contracts import FORBIDDEN_FEATURE_COLUMNS


@dataclass(frozen=True)
class Feature:
    name: str
    family: str
    source: str
    transformation: str
    availability: str   # when the value is known relative to the prediction (application) time
    kind: str           # numeric | categorical


AT_APP = "known at application submission"
HIST = "known at application: only records dated on/before the application (guard in dbt SQL)"


def _f(name, family, source, tr, kind="numeric", avail=AT_APP):
    return Feature(name, family, source, tr, avail, kind)


_APP = "application_train (stg_application)"
FEATURE_REGISTRY: list[Feature] = [
    # --- family A: applicant & loan terms (raw) ---
    *[_f(n, "A. application raw", _APP, "cast only") for n in [
        "cnt_children", "amt_income_total", "amt_credit", "amt_annuity", "amt_goods_price",
        "region_population_relative", "days_birth", "days_registration", "days_id_publish", "own_car_age",
        "cnt_fam_members", "region_rating_client_w_city", "obs_30_cnt_social_circle", "def_30_cnt_social_circle",
        "days_last_phone_change", "amt_req_credit_bureau_year"]],
    _f("days_employed", "A. application raw", _APP, "cast; sentinel 365243 -> NULL"),
    _f("flag_employed_sentinel", "A. application raw", _APP, "1 if DAYS_EMPLOYED==365243 (non-working placeholder)"),
    *[_f(f"ext_source_{i}", "A. application raw", _APP, "cast only (external scoring, third-party)") for i in (1, 2, 3)],
    *[_f(n, "A. application raw", _APP, "cast only; category", kind="categorical") for n in [
        "name_contract_type", "flag_own_car", "flag_own_realty", "name_income_type", "name_education_type",
        "name_family_status", "name_housing_type", "occupation_type"]],
    # --- family B: derived affordability ratios ---
    _f("credit_income_ratio", "B. affordability ratios", _APP, "amt_credit / amt_income_total"),
    _f("annuity_income_ratio", "B. affordability ratios", _APP, "amt_annuity / amt_income_total"),
    _f("credit_term_months", "B. affordability ratios", _APP, "amt_credit / amt_annuity (implied term)"),
    _f("credit_goods_ratio", "B. affordability ratios", _APP, "amt_credit / amt_goods_price"),
    _f("income_per_family_member", "B. affordability ratios", _APP, "amt_income_total / cnt_fam_members"),
    _f("employed_to_age_ratio", "B. affordability ratios", _APP, "days_employed / days_birth"),
    _f("ext_source_mean", "B. affordability ratios", _APP, "row mean of available ext_source_1..3"),
    _f("ext_source_min", "B. affordability ratios", _APP, "row min of available ext_source_1..3"),
]

_B = "bureau + bureau_balance (int_bureau_agg)"
_P = "previous_application (int_previous_agg)"
_I = "installments_payments (int_installments_agg)"
_C = "credit_card_balance (int_credit_card_agg)"
_S = "POS_CASH_balance (int_pos_cash_agg)"

_HIST_SPEC = [  # (name, family, source, transformation, zero_fill)
    ("bureau_n_credits", "C. bureau history", _B, "count credits with days_credit<=0", True),
    ("bureau_n_active", "C. bureau history", _B, "count where CREDIT_ACTIVE='Active'", True),
    ("bureau_share_active", "C. bureau history", _B, "n_active / n_credits", False),
    ("bureau_n_credit_types", "C. bureau history", _B, "count distinct CREDIT_TYPE", True),
    ("bureau_oldest_credit_days", "C. bureau history", _B, "min DAYS_CREDIT (history length proxy)", False),
    ("bureau_newest_credit_days", "C. bureau history", _B, "max DAYS_CREDIT (recency)", False),
    ("bureau_sum_credit", "C. bureau history", _B, "sum AMT_CREDIT_SUM", False),
    ("bureau_sum_debt", "C. bureau history", _B, "sum AMT_CREDIT_SUM_DEBT", False),
    ("bureau_debt_to_credit", "C. bureau history", _B, "sum debt / sum credit", False),
    ("bureau_sum_overdue", "C. bureau history", _B, "sum AMT_CREDIT_SUM_OVERDUE", False),
    ("bureau_max_overdue_ever", "C. bureau history", _B, "max AMT_CREDIT_MAX_OVERDUE", False),
    ("bureau_n_currently_overdue", "C. bureau history", _B, "count CREDIT_DAY_OVERDUE>0", True),
    ("bureau_sum_prolong", "C. bureau history", _B, "sum CNT_CREDIT_PROLONG", True),
    ("bureau_bal_months_observed", "C. bureau history", _B, "months in bureau_balance with MONTHS_BALANCE<=0", True),
    ("bureau_bal_dpd_month_share", "C. bureau history", _B, "months with status 1-5 / months observed", False),
    ("bureau_bal_worst_status", "C. bureau history", _B, "max delinquency bucket 1-5 seen", False),
    ("prev_n_applications", "D. previous applications", _P, "count with DAYS_DECISION<0", True),
    ("prev_n_approved", "D. previous applications", _P, "count Approved", True),
    ("prev_n_refused", "D. previous applications", _P, "count Refused", True),
    ("prev_refusal_rate", "D. previous applications", _P, "refused / total", False),
    ("prev_n_consumer_loans", "D. previous applications", _P, "count Consumer loans", True),
    ("prev_n_revolving_loans", "D. previous applications", _P, "count Revolving loans", True),
    ("prev_avg_amt_application", "D. previous applications", _P, "mean AMT_APPLICATION", False),
    ("prev_avg_credit_to_application", "D. previous applications", _P, "mean AMT_CREDIT/AMT_APPLICATION (approved)", False),
    ("prev_avg_cnt_payment", "D. previous applications", _P, "mean CNT_PAYMENT", False),
    ("prev_avg_rate_down_payment", "D. previous applications", _P, "mean RATE_DOWN_PAYMENT", False),
    ("prev_last_decision_days", "D. previous applications", _P, "max DAYS_DECISION (most recent)", False),
    ("prev_first_decision_days", "D. previous applications", _P, "min DAYS_DECISION (oldest)", False),
    ("inst_n_records", "E. installment behaviour", _I, "count payment rows due on/before application", True),
    ("inst_n_due_unpaid", "E. installment behaviour", _I, "due rows with no payment date", True),
    ("inst_late_share", "E. installment behaviour", _I, "share paid after due date", False),
    ("inst_mean_days_late", "E. installment behaviour", _I, "mean max(days_late,0)", False),
    ("inst_max_days_late", "E. installment behaviour", _I, "max (DAYS_ENTRY_PAYMENT - DAYS_INSTALMENT)", False),
    ("inst_mean_payment_ratio", "E. installment behaviour", _I, "mean AMT_PAYMENT/AMT_INSTALMENT", False),
    ("inst_underpaid_share", "E. installment behaviour", _I, "share with AMT_PAYMENT < AMT_INSTALMENT", False),
    ("inst_late_share_last12m", "E. installment behaviour", _I, "late share, due within last 365 days", False),
    ("cc_n_cards", "F. credit-card behaviour", _C, "count distinct cards, MONTHS_BALANCE<0", True),
    ("cc_n_months", "F. credit-card behaviour", _C, "count card-months", True),
    ("cc_mean_utilization", "F. credit-card behaviour", _C, "mean AMT_BALANCE/limit", False),
    ("cc_max_utilization", "F. credit-card behaviour", _C, "max AMT_BALANCE/limit", False),
    ("cc_mean_dpd", "F. credit-card behaviour", _C, "mean SK_DPD", False),
    ("cc_max_dpd", "F. credit-card behaviour", _C, "max SK_DPD", False),
    ("cc_share_months_dpd_def", "F. credit-card behaviour", _C, "share of months SK_DPD_DEF>0", False),
    ("cc_atm_drawing_share", "F. credit-card behaviour", _C, "ATM drawings / all drawings", False),
    ("pos_n_loans", "G. POS/cash behaviour", _S, "count distinct loans, MONTHS_BALANCE<0", True),
    ("pos_max_dpd", "G. POS/cash behaviour", _S, "max SK_DPD", False),
    ("pos_share_months_dpd_def", "G. POS/cash behaviour", _S, "share of months SK_DPD_DEF>0", False),
]
FEATURE_REGISTRY += [_f(n, fam, src, tr, avail=HIST) for n, fam, src, tr, _ in _HIST_SPEC]
FEATURE_REGISTRY += [
    _f("bureau_debt_to_income", "C. bureau history", "bureau + application", "bureau_sum_debt / amt_income_total",
       avail=HIST),
    *[_f(f"has_{k}_history", "H. history presence", "derived", f"1 if applicant has any {k} rows (before zero-fill)",
         avail=HIST) for k in ("bureau", "prev", "inst", "cc", "pos")],
]

HISTORY_COLUMNS = [h[0] for h in _HIST_SPEC]
ZERO_FILL = [h[0] for h in _HIST_SPEC if h[4]]
_PRESENCE = {"bureau": "bureau_n_credits", "prev": "prev_n_applications", "inst": "inst_n_records",
             "cc": "cc_n_cards", "pos": "pos_n_loans"}

NUMERIC_FEATURES = [f.name for f in FEATURE_REGISTRY if f.kind == "numeric"]
CATEGORICAL_FEATURES = [f.name for f in FEATURE_REGISTRY if f.kind == "categorical"]
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES
INPUT_COLUMNS_RAW = [f.name for f in FEATURE_REGISTRY if f.family.startswith("A.")]  # needed from the caller

DERIVED_FEATURES = [f.name for f in FEATURE_REGISTRY
                    if f.family.startswith(("B.", "H.")) or f.name == "bureau_debt_to_income"]
AGGREGATE_FEATURES = HISTORY_COLUMNS


def _ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    den = den.where(den > 0)
    return num / den


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Pure transformation: raw application columns (+ optional history columns) -> model matrix.

    Missing history columns are treated as 'no history' (NaN -> presence flags 0, counts 0).
    Never reads TARGET / split / ids. Output columns == FEATURE_COLUMNS, in order.
    """
    out = pd.DataFrame(index=df.index)
    for name in INPUT_COLUMNS_RAW:
        out[name] = df[name] if name in df else np.nan
    for name in HISTORY_COLUMNS:
        out[name] = df[name] if name in df else np.nan
    num_cols = [c for c in out.columns if c not in CATEGORICAL_FEATURES]
    out[num_cols] = out[num_cols].apply(pd.to_numeric, errors="coerce").astype("float64")

    for k, col in _PRESENCE.items():
        out[f"has_{k}_history"] = out[col].notna().astype("float64")
    out[ZERO_FILL] = out[ZERO_FILL].fillna(0.0)

    out["credit_income_ratio"] = _ratio(out["amt_credit"], out["amt_income_total"])
    out["annuity_income_ratio"] = _ratio(out["amt_annuity"], out["amt_income_total"])
    out["credit_term_months"] = _ratio(out["amt_credit"], out["amt_annuity"])
    out["credit_goods_ratio"] = _ratio(out["amt_credit"], out["amt_goods_price"])
    out["income_per_family_member"] = _ratio(out["amt_income_total"], out["cnt_fam_members"])
    out["employed_to_age_ratio"] = out["days_employed"] / out["days_birth"].where(out["days_birth"] != 0)
    ext = out[["ext_source_1", "ext_source_2", "ext_source_3"]]
    out["ext_source_mean"] = ext.mean(axis=1)
    out["ext_source_min"] = ext.min(axis=1)
    out["bureau_debt_to_income"] = _ratio(out["bureau_sum_debt"], out["amt_income_total"])

    for c in CATEGORICAL_FEATURES:
        out[c] = df[c].astype("object") if c in df else None
        out[c] = out[c].where(out[c].notna(), None)

    out = out.replace([np.inf, -np.inf], np.nan)
    return out[FEATURE_COLUMNS]


def assert_no_forbidden(columns) -> None:
    bad = {c for c in columns if c in FORBIDDEN_FEATURE_COLUMNS or c.upper() in FORBIDDEN_FEATURE_COLUMNS}
    if bad:
        raise ValueError(f"forbidden columns in feature matrix: {sorted(bad)}")


def feature_schema_version() -> str:
    """fs-<hash>: changes whenever feature names/kinds/order or the pipeline version change."""
    payload = "|".join(f"{f.name}:{f.kind}" for f in FEATURE_REGISTRY) + config.FEATURE_PIPELINE_VERSION
    return "fs-" + hashlib.sha256(payload.encode()).hexdigest()[:10]


def feature_counts() -> dict:
    raw = [f for f in FEATURE_REGISTRY if f.family.startswith("A.")]
    return {
        "total_model_input_columns": len(FEATURE_COLUMNS),
        "raw_application_columns": len(raw),
        "history_aggregates_from_dbt": len(HISTORY_COLUMNS),
        "derived_in_python": len(DERIVED_FEATURES),
        "engineered_total (aggregates + derived)": len(HISTORY_COLUMNS) + len(DERIVED_FEATURES),
        "categorical": len(CATEGORICAL_FEATURES),
    }
