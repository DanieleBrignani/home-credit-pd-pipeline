"""TEST-ONLY synthetic fixtures with the Home Credit schema.

These files exist solely to exercise code paths (contracts, dbt SQL, temporal guards, API). They are NEVER used for
any reported model metric. Written to tests/fixtures/raw/ (never data/raw/). Includes deliberate traps:
future-dated installments / card months / previous decisions, the DAYS_EMPLOYED=365243 sentinel, empty strings.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from creditrisk.contracts import CONTRACTS

OUT = Path(__file__).parent / "raw"


def make(n_app: int = 3000, seed: int = 7, out: Path = OUT) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    out.mkdir(parents=True, exist_ok=True)
    ids = np.arange(100001, 100001 + n_app)
    ext = rng.beta(5, 2.5, size=(n_app, 3))
    income = rng.lognormal(12, 0.5, n_app).round(1)
    credit = (income * rng.uniform(1, 8, n_app)).round(1)
    annuity = (credit / rng.uniform(12, 60, n_app)).round(1)
    late_p = rng.beta(1, 8, n_app)
    logit = -2.3 - 3.0 * (ext.mean(1) - 0.6) + 4.0 * (late_p - 0.11) + 0.15 * (credit / income - 4.5)
    target = (rng.random(n_app) < 1 / (1 + np.exp(-logit))).astype(int)
    emp = -rng.integers(30, 9000, n_app)
    emp[rng.random(n_app) < 0.18] = 365243
    app = pd.DataFrame({
        "SK_ID_CURR": ids, "TARGET": target,
        "NAME_CONTRACT_TYPE": rng.choice(["Cash loans", "Revolving loans"], n_app, p=[.9, .1]),
        "CODE_GENDER": rng.choice(["F", "M"], n_app),
        "FLAG_OWN_CAR": rng.choice(["Y", "N"], n_app), "FLAG_OWN_REALTY": rng.choice(["Y", "N"], n_app),
        "CNT_CHILDREN": rng.integers(0, 4, n_app), "AMT_INCOME_TOTAL": income, "AMT_CREDIT": credit,
        "AMT_ANNUITY": annuity, "AMT_GOODS_PRICE": (credit * 0.9).round(1),
        "NAME_INCOME_TYPE": rng.choice(["Working", "Pensioner", "State servant"], n_app),
        "NAME_EDUCATION_TYPE": rng.choice(["Secondary / secondary special", "Higher education"], n_app),
        "NAME_FAMILY_STATUS": rng.choice(["Married", "Single / not married"], n_app),
        "NAME_HOUSING_TYPE": rng.choice(["House / apartment", "With parents"], n_app),
        "REGION_POPULATION_RELATIVE": rng.uniform(0.001, 0.07, n_app).round(4),
        "DAYS_BIRTH": -rng.integers(7500, 24000, n_app), "DAYS_EMPLOYED": emp,
        "DAYS_REGISTRATION": -rng.integers(0, 15000, n_app), "DAYS_ID_PUBLISH": -rng.integers(0, 6000, n_app),
        "OWN_CAR_AGE": np.where(rng.random(n_app) < .6, np.nan, rng.integers(0, 20, n_app)),
        "OCCUPATION_TYPE": rng.choice(["Laborers", "Core staff", "Managers", None], n_app),
        "CNT_FAM_MEMBERS": rng.integers(1, 6, n_app), "REGION_RATING_CLIENT_W_CITY": rng.integers(1, 4, n_app),
        "EXT_SOURCE_1": np.where(rng.random(n_app) < .5, np.nan, ext[:, 0]),
        "EXT_SOURCE_2": ext[:, 1], "EXT_SOURCE_3": np.where(rng.random(n_app) < .2, np.nan, ext[:, 2]),
        "OBS_30_CNT_SOCIAL_CIRCLE": rng.integers(0, 5, n_app), "DEF_30_CNT_SOCIAL_CIRCLE": rng.integers(0, 2, n_app),
        "DAYS_LAST_PHONE_CHANGE": -rng.integers(0, 4000, n_app),
        "AMT_REQ_CREDIT_BUREAU_YEAR": rng.integers(0, 5, n_app),
    })
    test_app = app.drop(columns=["TARGET"]).head(50).copy()
    test_app["SK_ID_CURR"] += 900000

    # previous applications (0-4 per applicant); ~10% of applicants have none
    rows = []
    sk_prev = 2000000
    for i, cid in enumerate(ids):
        for _ in range(0 if rng.random() < .1 else rng.integers(1, 5)):
            sk_prev += 1
            appl = float(rng.integers(5, 400) * 1000)
            rows.append((sk_prev, cid, rng.choice(["Consumer loans", "Cash loans", "Revolving loans"]), appl,
                         appl * rng.uniform(.8, 1.1), rng.choice(["Approved", "Refused", "Canceled"], p=[.7, .2, .1]),
                         -int(rng.integers(10, 2500)), float(rng.integers(6, 48)), rng.uniform(0, .3),
                         "POS", "XAP"))
    # trap: a decision dated AFTER the application
    rows.append((sk_prev + 1, ids[0], "Cash loans", 100000., 100000., "Approved", 25, 12., 0., "POS", "XAP"))
    prev = pd.DataFrame(rows, columns=["SK_ID_PREV", "SK_ID_CURR", "NAME_CONTRACT_TYPE", "AMT_APPLICATION",
                                       "AMT_CREDIT", "NAME_CONTRACT_STATUS", "DAYS_DECISION", "CNT_PAYMENT",
                                       "RATE_DOWN_PAYMENT", "NAME_PORTFOLIO", "CODE_REJECT_REASON"])
    approved = prev[prev.NAME_CONTRACT_STATUS == "Approved"].head(4000)
    late = dict(zip(ids, late_p))
    inst = []
    for r in approved.itertuples():
        for k in range(1, int(rng.integers(3, 10))):
            due = -int(rng.integers(5, 1500))
            delay = int(rng.integers(1, 20)) if rng.random() < late[r.SK_ID_CURR] else int(-rng.integers(0, 3))
            pay_day = min(due + delay, -1)
            amt = float(rng.integers(500, 20000))
            inst.append((r.SK_ID_PREV, r.SK_ID_CURR, 1, k, due, pay_day, amt, amt * (1 if rng.random() < .9 else .5)))
    # traps: due in the future (must be excluded), paid in the future (excluded)
    inst.append((approved.SK_ID_PREV.iloc[0], approved.SK_ID_CURR.iloc[0], 1, 99, 30, 31, 1000., 1000.))
    inst.append((approved.SK_ID_PREV.iloc[0], approved.SK_ID_CURR.iloc[0], 1, 98, -10, 12, 1000., 1000.))
    inst = pd.DataFrame(inst, columns=["SK_ID_PREV", "SK_ID_CURR", "NUM_INSTALMENT_VERSION", "NUM_INSTALMENT_NUMBER",
                                       "DAYS_INSTALMENT", "DAYS_ENTRY_PAYMENT", "AMT_INSTALMENT", "AMT_PAYMENT"])

    br, sk_b = [], 5000000
    for cid in ids[: int(n_app * .8)]:
        for _ in range(rng.integers(1, 5)):
            sk_b += 1
            s = float(rng.integers(10, 500) * 1000)
            br.append((sk_b, cid, rng.choice(["Active", "Closed"], p=[.4, .6]),
                       rng.choice(["Consumer credit", "Credit card", "Car loan"]), -int(rng.integers(30, 3000)),
                       int(rng.random() < .03) * int(rng.integers(1, 40)), float(rng.random() < .1) * 5000,
                       int(rng.random() < .05), s, s * rng.uniform(0, 1), float(rng.random() < .05) * 100))
    bureau = pd.DataFrame(br, columns=["SK_ID_BUREAU", "SK_ID_CURR", "CREDIT_ACTIVE", "CREDIT_TYPE", "DAYS_CREDIT",
                                       "CREDIT_DAY_OVERDUE", "AMT_CREDIT_MAX_OVERDUE", "CNT_CREDIT_PROLONG",
                                       "AMT_CREDIT_SUM", "AMT_CREDIT_SUM_DEBT", "AMT_CREDIT_SUM_OVERDUE"])
    bb = []
    for b in bureau.SK_ID_BUREAU.head(2500):
        for m in range(0, -int(rng.integers(2, 14)), -1):
            bb.append((b, m, rng.choice(["C", "0", "1", "2", "X"], p=[.5, .35, .08, .03, .04])))
    bb.append((bureau.SK_ID_BUREAU.iloc[0], 5, "5"))  # trap: future month
    bb = pd.DataFrame(bb, columns=["SK_ID_BUREAU", "MONTHS_BALANCE", "STATUS"])

    cc = []
    for r in prev.head(1500).itertuples():
        for m in range(-1, -int(rng.integers(2, 10)), -1):
            lim = float(rng.integers(20, 300) * 1000)
            cc.append((r.SK_ID_PREV, r.SK_ID_CURR, m, lim * rng.uniform(0, 1), lim, rng.uniform(0, 5000),
                       rng.uniform(0, 10000), int(rng.random() < .05) * int(rng.integers(1, 30)), 0))
    cc.append((prev.SK_ID_PREV.iloc[0], prev.SK_ID_CURR.iloc[0], 3, 1.0, 1000., 0., 0., 99, 99))  # trap future
    cc = pd.DataFrame(cc, columns=["SK_ID_PREV", "SK_ID_CURR", "MONTHS_BALANCE", "AMT_BALANCE",
                                   "AMT_CREDIT_LIMIT_ACTUAL", "AMT_DRAWINGS_ATM_CURRENT", "AMT_DRAWINGS_CURRENT",
                                   "SK_DPD", "SK_DPD_DEF"])
    pos = []
    for r in prev.sample(1500, random_state=1).itertuples():
        for m in range(-1, -int(rng.integers(2, 12)), -1):
            pos.append((r.SK_ID_PREV, r.SK_ID_CURR, m, int(rng.random() < .05) * int(rng.integers(1, 20)), 0))
    pos = pd.DataFrame(pos, columns=["SK_ID_PREV", "SK_ID_CURR", "MONTHS_BALANCE", "SK_DPD", "SK_DPD_DEF"])

    tables = {"application_train": app, "application_test": test_app, "bureau": bureau, "bureau_balance": bb,
              "previous_application": prev, "installments_payments": inst, "credit_card_balance": cc,
              "POS_CASH_balance": pos}
    for name, df in tables.items():
        df.to_csv(out / CONTRACTS[name].file, index=False)
    return tables


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    make(out=a.out)
    print("fixtures written to", a.out, "(TEST-ONLY synthetic data)")
