"""Reference (slow, straightforward) implementation of build_features, kept ONLY as an oracle for the equivalence tests.
The production implementation in creditrisk.features is a vectorised rewrite that must give bit-identical output."""
import numpy as np
import pandas as pd

from creditrisk.features import (CATEGORICAL_FEATURES, FEATURE_COLUMNS, HISTORY_COLUMNS, INPUT_COLUMNS_RAW, ZERO_FILL,
                                 _PRESENCE)
from creditrisk.features import _ratio_series as _ratio


def build_features_reference(df: pd.DataFrame) -> pd.DataFrame:
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
